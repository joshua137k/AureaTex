"""
Servidor MCP (Model Context Protocol) — liga clientes de IA (Claude Code,
Claude Desktop, Cursor, VS Code...) aos projetos do AureaTeX.

  POST /mcp            JSON-RPC 2.0, transporte "Streamable HTTP" sem estado
                       (respostas em JSON, sem SSE).
  /api/tokens          gestão dos tokens de acesso (a partir do browser).

Segurança:
  - Autenticação com tokens próprios ("atx_..."), nunca com o JWT do browser.
    Só o SHA-256 é guardado; podem ser revogados e ter validade.
  - Cada token é "read" (só leitura) ou "write" (pode editar e compilar).
  - O token só dá acesso a /mcp: não muda a password, não gere convites, etc.
  - As permissões por projeto são as mesmas do editor (dono/editor/viewer).
  - Pedidos com cabeçalho Origin de outro site são recusados (DNS rebinding).
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from core.ai_context import build_context
from core.config import APP_URL
from core.database import get_db
from core.latex_log import format_summary, parse_log
from core.paths import InvalidFilename, normalize_filename
from core.security import RateLimiter, client_ip, get_current_user
from core.storage import is_binary_content

from routers import compile as compile_router
from routers import files as files_router
from routers.collab import flush_project

router = APIRouter(tags=["mcp"])

TOKEN_PREFIX = "atx_"
MAX_TOKENS_PER_USER = 20

SERVER_INFO = {"name": "aureatex", "title": "AureaTeX", "version": "1.0.0"}
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "AureaTeX is a self-hosted LaTeX editor (like Overleaf). Start with list_projects, "
    "then get_project_context to see the file tree and sources. Prefer edit_file "
    "(exact text replacement) over write_file for small changes. After editing, call "
    "compile_project to check for LaTeX errors. Edits appear live to anyone who has "
    "the project open in the browser. File paths are relative to the project root, "
    "with '/' as separator."
)

# Falhas de autenticação por IP e chamadas por token.
_auth_failures = RateLimiter(limit=30, window=600)
_calls = RateLimiter(limit=600, window=600)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Gestão de tokens (browser, com a sessão normal)
# ---------------------------------------------------------------------------

class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: str = Field(default="read", pattern="^(read|write)$")
    expires_days: Optional[int] = Field(default=90, ge=1, le=3650)


@router.get("/api/tokens")
async def list_tokens(user=Depends(get_current_user), cur=Depends(get_db)):
    await cur.execute(
        """
        SELECT id, name, prefix, scope, created_at, last_used_at, expires_at
        FROM api_tokens WHERE user_id = %s ORDER BY created_at DESC
        """,
        (user["id"],),
    )
    return await cur.fetchall()


@router.post("/api/tokens", status_code=201)
async def create_token(body: TokenCreate, user=Depends(get_current_user), cur=Depends(get_db)):
    await cur.execute("SELECT COUNT(*) AS n FROM api_tokens WHERE user_id = %s", (user["id"],))
    if (await cur.fetchone())["n"] >= MAX_TOKENS_PER_USER:
        raise HTTPException(status_code=400, detail=f"Máximo de {MAX_TOKENS_PER_USER} tokens. Revoga algum primeiro.")

    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(days=body.expires_days) if body.expires_days else None

    await cur.execute(
        """
        INSERT INTO api_tokens (user_id, name, token_hash, prefix, scope, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (user["id"], body.name.strip(), _hash(token), token[:10], body.scope, expires),
    )

    return {
        "token": token,          # só agora — não volta a ser mostrado
        "name": body.name.strip(),
        "scope": body.scope,
        "expires_at": expires,
        "mcp_url": f"{APP_URL}/mcp",
    }


@router.delete("/api/tokens/{token_id}", status_code=204)
async def revoke_token(token_id: int, user=Depends(get_current_user), cur=Depends(get_db)):
    await cur.execute("DELETE FROM api_tokens WHERE id = %s AND user_id = %s", (token_id, user["id"]))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Token não encontrado.")


# ---------------------------------------------------------------------------
# Autenticação MCP
# ---------------------------------------------------------------------------

def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": message}},
        headers={"WWW-Authenticate": 'Bearer realm="aureatex"'},
    )


async def _mcp_user(request: Request, cur):
    """Devolve (user, scope, token_id) ou uma JSONResponse de erro."""
    ip_key = f"ip:{client_ip(request)}"

    if _auth_failures.is_blocked(ip_key):
        return JSONResponse(status_code=429, content={"detail": "Demasiadas tentativas."})

    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    if not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        _auth_failures.hit(ip_key)
        return _unauthorized("Token em falta ou inválido. Cria um em AureaTeX → AI connection (MCP).")

    await cur.execute(
        """
        SELECT t.id AS token_id, t.scope, t.expires_at, t.last_used_at, u.*
        FROM api_tokens t JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = %s
        """,
        (_hash(token),),
    )
    row = await cur.fetchone()

    if not row:
        _auth_failures.hit(ip_key)
        return _unauthorized("Token inválido ou revogado.")

    if row["expires_at"] and row["expires_at"] < datetime.utcnow():
        return _unauthorized("Token expirado. Cria um novo.")

    if _calls.is_blocked(f"t:{row['token_id']}"):
        return JSONResponse(status_code=429, content={"detail": "Demasiados pedidos. Espera uns minutos."})
    _calls.hit(f"t:{row['token_id']}")

    if not row["last_used_at"] or datetime.utcnow() - row["last_used_at"] > timedelta(minutes=5):
        await cur.execute("UPDATE api_tokens SET last_used_at = UTC_TIMESTAMP() WHERE id = %s", (row["token_id"],))

    return row, row["scope"], row["token_id"]


# ---------------------------------------------------------------------------
# Ferramentas
# ---------------------------------------------------------------------------

class ToolError(Exception):
    pass


def _schema(props: dict, required: list) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_PID = {"type": "integer", "description": "Project id (from list_projects)."}
_PATH = {"type": "string", "description": "File path relative to the project root, e.g. 'chapters/intro.tex'."}

TOOLS = [
    {
        "name": "list_projects",
        "title": "List projects",
        "description": "List the LaTeX projects you own or that were shared with you, with id, name, your role and last update.",
        "inputSchema": _schema({}, []),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_project_context",
        "title": "Get project context",
        "description": (
            "Get a project's file tree and (mode='full') the content of all text files as markdown. "
            "Use mode='tree' first for big projects. 'path' limits it to one folder or file."
        ),
        "inputSchema": _schema({
            "project_id": _PID,
            "mode": {"type": "string", "enum": ["tree", "full"], "default": "full"},
            "path": {"type": "string", "description": "Optional folder or file to limit to.", "default": ""},
        }, ["project_id"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "read_file",
        "title": "Read file",
        "description": "Read one text file. Optionally a line range (1-based, inclusive); lines are then prefixed with their numbers.",
        "inputSchema": _schema({
            "project_id": _PID,
            "path": _PATH,
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        }, ["project_id", "path"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "search_project",
        "title": "Search project",
        "description": "Find a literal text in every text file of a project. Returns file:line: text (max 200 matches).",
        "inputSchema": _schema({
            "project_id": _PID,
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "case_sensitive": {"type": "boolean", "default": False},
        }, ["project_id", "query"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_compile_log",
        "title": "Get last compile result",
        "description": "Errors and warnings of the latest compilation of a project, plus the end of the raw log.",
        "inputSchema": _schema({"project_id": _PID}, ["project_id"]),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "edit_file",
        "title": "Edit file",
        "description": (
            "Replace an exact piece of text in a file. old_text must match exactly once "
            "(include surrounding lines to make it unique) unless replace_all is true. "
            "Needs a 'write' token and editor access."
        ),
        "inputSchema": _schema({
            "project_id": _PID,
            "path": _PATH,
            "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        }, ["project_id", "path", "old_text", "new_text"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "write_file",
        "title": "Write file",
        "description": "Create a text file or replace its whole content. Needs a 'write' token and editor access.",
        "inputSchema": _schema({"project_id": _PID, "path": _PATH, "content": {"type": "string"}}, ["project_id", "path", "content"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "rename_file",
        "title": "Rename / move file",
        "description": "Rename or move a file inside the project. Needs a 'write' token.",
        "inputSchema": _schema({"project_id": _PID, "path": _PATH, "new_path": _PATH}, ["project_id", "path", "new_path"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "delete_file",
        "title": "Delete file",
        "description": "Delete a file (not the main file). Cannot be undone from here. Needs a 'write' token.",
        "inputSchema": _schema({"project_id": _PID, "path": _PATH}, ["project_id", "path"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
    {
        "name": "compile_project",
        "title": "Compile project",
        "description": (
            "Compile the project with pdflatex (+bibtex/biber) and return errors and warnings with file:line. "
            "By default compiles the main file; 'file' compiles another .tex with \\documentclass instead "
            "(e.g. a second version) without changing the main file. Needs a 'write' token."
        ),
        "inputSchema": _schema({
            "project_id": _PID,
            "file": {"type": "string", "description": "Optional .tex file to compile instead of the main file."},
        }, ["project_id"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]

_WRITE_TOOLS = {"edit_file", "write_file", "rename_file", "delete_file", "compile_project"}


def _int(args: dict, key: str) -> int:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value))
        except ValueError:
            raise ToolError(f"'{key}' tem de ser um número inteiro.")
    return value


def _path(args: dict, key: str = "path") -> str:
    try:
        return normalize_filename(str(args.get(key) or ""))
    except InvalidFilename as e:
        raise ToolError(f"Caminho inválido '{args.get(key)}': {e}")


async def _access(cur, user, project_id: int, edit: bool) -> None:
    try:
        await files_router._assert_project_owner(project_id, user["id"], cur, require_edit=edit)
    except HTTPException:
        raise ToolError(
            "Projeto não encontrado ou sem permissão"
            + (" de edição (és só viewer?)." if edit else ".")
        )


async def _current_content(cur, project_id: int, path: str) -> str:
    await flush_project(project_id)
    await cur.execute(
        "SELECT content FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, path),
    )
    row = await cur.fetchone()
    if not row:
        raise ToolError(f"Ficheiro não encontrado: {path}")
    content = row["content"] or ""
    if is_binary_content(content):
        raise ToolError(f"'{path}' é um ficheiro binário (imagem/PDF); não pode ser lido como texto.")
    return content


async def _save(cur, user, project_id: int, path: str, content: str) -> None:
    try:
        body = files_router.FileUpdate(content=content)
    except ValidationError:
        raise ToolError("Ficheiro demasiado grande.")
    await files_router.save_file(project_id, path, body, user=user, cur=cur)
    await cur.connection.commit()


async def _last_log(cur, project_id: int):
    await cur.execute(
        "SELECT success, duration_ms, compiled_at, log_output FROM compile_log WHERE project_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    return await cur.fetchone()


async def _run_tool(name: str, args: dict, user, scope: str, cur) -> str:
    if name in _WRITE_TOOLS and scope != "write":
        raise ToolError("Este token é só de leitura. Cria um token com acesso 'write' para editar ou compilar.")

    if name == "list_projects":
        await cur.execute(
            """
            SELECT p.id, p.name, p.updated_at,
                   CASE WHEN p.owner_id = %s THEN 'owner' ELSE pc.role END AS role,
                   (SELECT filename FROM project_files f WHERE f.project_id = p.id AND f.is_main = 1 LIMIT 1) AS main_file
            FROM projects p
            LEFT JOIN project_collaborators pc ON pc.project_id = p.id AND pc.user_id = %s
            WHERE p.owner_id = %s OR pc.id IS NOT NULL
            ORDER BY p.updated_at DESC
            """,
            (user["id"], user["id"], user["id"]),
        )
        rows = await cur.fetchall()
        if not rows:
            return "No projects yet."
        return "\n".join(
            f"- id={r['id']}  {r['name']}  (role: {r['role']}, main: {r['main_file'] or '-'}, updated {r['updated_at']:%Y-%m-%d %H:%M} UTC)"
            for r in rows
        )

    project_id = _int(args, "project_id")

    if name == "get_project_context":
        await _access(cur, user, project_id, edit=False)
        await flush_project(project_id)
        mode = args.get("mode") or "full"
        if mode not in ("tree", "full"):
            raise ToolError("mode tem de ser 'tree' ou 'full'.")
        result = await build_context(cur, project_id, mode, str(args.get("path") or "")[:255])
        return result["text"]

    if name == "read_file":
        await _access(cur, user, project_id, edit=False)
        path = _path(args)
        content = await _current_content(cur, project_id, path)
        start, end = args.get("start_line"), args.get("end_line")
        if start is None and end is None:
            return content
        lines = content.split("\n")
        s = max(1, _int(args, "start_line") if start is not None else 1)
        e = min(len(lines), _int(args, "end_line") if end is not None else len(lines))
        width = len(str(e))
        return "\n".join(f"{i:>{width}}| {lines[i - 1]}" for i in range(s, e + 1)) or "(empty range)"

    if name == "search_project":
        await _access(cur, user, project_id, edit=False)
        await flush_project(project_id)
        query = str(args.get("query") or "")
        if not query:
            raise ToolError("query vazia.")
        sensitive = bool(args.get("case_sensitive"))
        needle = query if sensitive else query.lower()

        await cur.execute(
            "SELECT filename, content FROM project_files WHERE project_id = %s ORDER BY filename",
            (project_id,),
        )
        matches = []
        for row in await cur.fetchall():
            content = row["content"] or ""
            if is_binary_content(content):
                continue
            for i, line in enumerate(content.split("\n"), 1):
                if needle in (line if sensitive else line.lower()):
                    matches.append(f"{row['filename']}:{i}: {line.strip()[:200]}")
                    if len(matches) >= 200:
                        break
            if len(matches) >= 200:
                break
        return "\n".join(matches) if matches else f"No matches for {query!r}."

    if name == "get_compile_log":
        await _access(cur, user, project_id, edit=False)
        last = await _last_log(cur, project_id)
        if not last:
            return "This project was never compiled."
        log = last["log_output"] or ""
        parsed = parse_log(log)
        status = "FAILED" if not last["success"] else ("PDF generated WITH ERRORS" if parsed["error_count"] else "SUCCESS")
        return (
            f"Last compile: {status} at {last['compiled_at']} UTC ({last['duration_ms']} ms)\n\n"
            + format_summary(parsed)
            + "\n\n--- end of raw log ---\n" + log[-3000:]
        )

    # ---- escrita ----
    await _access(cur, user, project_id, edit=True)

    if name == "write_file":
        path = _path(args)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("content tem de ser texto.")
        await _save(cur, user, project_id, path, content)
        return f"Saved {path} ({len(content)} chars, {content.count(chr(10)) + 1} lines)."

    if name == "edit_file":
        path = _path(args)
        old, new = args.get("old_text"), args.get("new_text")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ToolError("old_text e new_text têm de ser texto (old_text não vazio).")
        content = await _current_content(cur, project_id, path)
        count = content.count(old)
        if count == 0:
            raise ToolError("old_text não foi encontrado. Lê o ficheiro outra vez (pode ter mudado) e copia o texto exatamente.")
        if count > 1 and not args.get("replace_all"):
            raise ToolError(f"old_text aparece {count} vezes. Inclui mais contexto para ser único, ou usa replace_all.")
        updated = content.replace(old, new) if args.get("replace_all") else content.replace(old, new, 1)
        await _save(cur, user, project_id, path, updated)
        line = content[:content.find(old)].count("\n") + 1
        return f"Edited {path}: {count if args.get('replace_all') else 1} replacement(s), first at line {line}."

    if name == "rename_file":
        path, new_path = _path(args), _path(args, "new_path")
        await files_router.rename_file(project_id, path, files_router.FileRename(new_name=new_path), user=user, cur=cur)
        await cur.connection.commit()
        return f"Renamed {path} → {new_path}."

    if name == "delete_file":
        path = _path(args)
        await files_router.delete_file(project_id, path, user=user, cur=cur)
        await cur.connection.commit()
        return f"Deleted {path}."

    if name == "compile_project":
        await cur.connection.commit()
        try:
            await compile_router.compile_project(project_id, file=args.get("file") or None, user=user)
            ok, err = True, ""
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, dict) else {"error": str(e.detail)}
            ok, err = False, detail.get("error", "")
            if e.status_code in (404, 409):
                raise ToolError(err)

        last = await _last_log(cur, project_id)
        log = (last or {}).get("log_output") or ""
        parsed = parse_log(log)
        if not ok:
            head = f"Compilation FAILED: {err}"
        elif parsed["error_count"]:
            head = "Compilation finished WITH ERRORS (a PDF was still generated, but it is probably wrong)."
        else:
            head = "Compilation SUCCEEDED — PDF generated."
        if last:
            head += f" ({last['duration_ms']} ms)"
        tail = "" if ok and not parsed["error_count"] else "\n\n--- end of raw log ---\n" + log[-3000:]
        return head + "\n\n" + format_summary(parsed) + tail

    raise ToolError(f"Ferramenta desconhecida: {name}")


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------

def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _handle(msg, user, scope: str, cur) -> Optional[dict]:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")

    method = msg["method"]
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # Notificações (sem id) não têm resposta.
    if "id" not in msg:
        return None

    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS + f" You are connected as '{user['username']}' with a {scope} token.",
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        tools = TOOLS if scope == "write" else [t for t in TOOLS if t["name"] not in _WRITE_TOOLS]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(req_id, -32602, "arguments must be an object")
        if name not in {t["name"] for t in TOOLS}:
            return _error(req_id, -32602, f"Unknown tool: {name}")

        try:
            text, is_error = await _run_tool(name, args, user, scope, cur), False
        except ToolError as e:
            text, is_error = str(e), True
        except HTTPException as e:
            detail = e.detail.get("error") if isinstance(e.detail, dict) else e.detail
            text, is_error = str(detail), True
        except Exception as e:
            print(f"[mcp] erro em {name}: {e!r}")
            await cur.connection.rollback()
            text, is_error = "Erro interno ao executar a ferramenta.", True

        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }}

    if method in ("resources/list", "prompts/list"):
        key = method.split("/")[0]
        return {"jsonrpc": "2.0", "id": req_id, "result": {key: []}}

    return _error(req_id, -32601, f"Method not found: {method}")


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    return not origin or origin.rstrip("/") == APP_URL


@router.post("/mcp")
async def mcp_endpoint(request: Request, cur=Depends(get_db)):
    if not _origin_ok(request):
        return JSONResponse(status_code=403, content={"detail": "Origin não permitida."})

    auth = await _mcp_user(request, cur)
    if isinstance(auth, Response):
        return auth
    user, scope, _ = auth

    try:
        payload = json.loads(await request.body())
    except ValueError:
        return JSONResponse(status_code=400, content=_error(None, -32700, "Parse error"))

    if isinstance(payload, list):
        if not payload or len(payload) > 20:
            return JSONResponse(status_code=400, content=_error(None, -32600, "Invalid batch"))
        replies = [r for r in [await _handle(m, user, scope, cur) for m in payload] if r]
        return JSONResponse(replies) if replies else Response(status_code=202)

    reply = await _handle(payload, user, scope, cur)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp")
@router.delete("/mcp")
async def mcp_no_stream():
    # Servidor sem estado: não há stream SSE nem sessões para terminar.
    return Response(status_code=405, headers={"Allow": "POST"})
