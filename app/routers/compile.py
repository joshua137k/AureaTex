import asyncio
import os
import re
import time
from collections import defaultdict
from typing import Optional

import aiomysql
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from core.config import COMPILE_TIMEOUT_SECS
from core.database import get_db, get_pool
from core.latex import CompileTimeout, compile_latex, output_pdf_name
from core.paths import InvalidFilename, project_dir, safe_join
from core.security import get_current_user
from core.storage import sync_project_to_disk

from routers.collab import flush_project, is_project_reloading
from routers.files import _assert_project_owner


router = APIRouter(prefix="/api/projects", tags=["compile"])


# Compilações em simultâneo em todo o servidor.
_compile_semaphore = asyncio.Semaphore(4)
# Uma compilação de cada vez por projeto (partilhado com push/pull).
_project_locks = defaultdict(asyncio.Lock)

# TEXT do MariaDB leva 65 535 bytes.
_LOG_DB_BYTES = 60000
_LOGS_KEPT_PER_PROJECT = 20


def _tail_bytes(text: str, limit: int) -> str:
    data = text.encode("utf-8")

    if len(data) <= limit:
        return text

    return data[-limit:].decode("utf-8", errors="ignore")


async def _prepare_project(project_id: int, wanted: Optional[str] = None) -> str:
    """
    Grava o que estiver pendente no collab, sincroniza a BD para o disco e
    devolve o ficheiro a compilar: `wanted` (um .tex qualquer do projeto, sem
    mudar o principal) ou o ficheiro principal.
    """
    await flush_project(project_id)

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            files = await sync_project_to_disk(project_id, cur)
            await conn.commit()

    if wanted:
        if not wanted.lower().endswith(".tex") or wanted not in {f["filename"] for f in files}:
            raise HTTPException(
                status_code=404,
                detail={"error": f"'{wanted}' não é um ficheiro .tex deste projeto.", "log": ""},
            )
        main_file = wanted
    else:
        main_file = next((f["filename"] for f in files if f["is_main"]), None)

    if main_file is None:
        names = {f["filename"] for f in files}
        main_file = "main.tex" if "main.tex" in names else None

    if main_file is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "O projeto não tem ficheiro principal definido.", "log": ""},
        )

    try:
        target = safe_join(project_dir(project_id), main_file)
    except InvalidFilename:
        target = None

    if not target or main_file.startswith("-") or not os.path.isfile(target):
        raise HTTPException(
            status_code=404,
            detail={"error": f"Ficheiro principal '{main_file}' não encontrado.", "log": ""},
        )

    return main_file


async def _store_log(project_id: int, user_id: int, success: bool, log: str, duration_ms: int) -> None:
    pool = await get_pool()

    async with pool.acquire() as conn:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO compile_log (project_id, user_id, success, log_output, duration_ms)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (project_id, user_id, int(success), _tail_bytes(log, _LOG_DB_BYTES), duration_ms),
                )

                # Não deixar a tabela crescer para sempre.
                await cur.execute(
                    """
                    DELETE FROM compile_log
                    WHERE project_id = %s
                      AND id NOT IN (
                          SELECT id FROM (
                              SELECT id FROM compile_log
                              WHERE project_id = %s
                              ORDER BY id DESC
                              LIMIT %s
                          ) AS keep
                      )
                    """,
                    (project_id, project_id, _LOGS_KEPT_PER_PROJECT),
                )

            await conn.commit()

        except Exception as e:
            await conn.rollback()
            print(f"[compile] Erro ao guardar log: {e}")


@router.post("/{project_id}/compile")
async def compile_project(
    project_id: int,
    file: Optional[str] = Query(default=None, max_length=255),
    user=Depends(get_current_user),
):
    # A ligação à BD só é usada para verificar permissões — não fica presa
    # durante a compilação.
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await _assert_project_owner(project_id, user["id"], cur, require_edit=True)
            await conn.commit()

    async with _project_locks[project_id]:
        if is_project_reloading(project_id):
            raise HTTPException(
                status_code=409,
                detail={"error": "Pull em curso. Compilação bloqueada temporariamente.", "log": ""},
            )

        main_file = await _prepare_project(project_id, file)
        project_path = project_dir(project_id)

        start = time.monotonic()

        async with _compile_semaphore:
            try:
                log_output, success = await compile_latex(project_path, main_file)

            except CompileTimeout as e:
                await _store_log(
                    project_id, user["id"], False,
                    e.log + f"\n! Compilation timed out after {COMPILE_TIMEOUT_SECS}s.\n",
                    int((time.monotonic() - start) * 1000),
                )
                raise HTTPException(
                    status_code=408,
                    detail={
                        "error": f"Compilação excedeu o tempo limite ({COMPILE_TIMEOUT_SECS}s).",
                        "log": e.log[-20000:],
                    },
                )

        duration_ms = int((time.monotonic() - start) * 1000)

        await _store_log(project_id, user["id"], success, log_output, duration_ms)

        if success:
            pdf_name = output_pdf_name(main_file)

            return FileResponse(
                path=os.path.join(project_path, pdf_name),
                media_type="application/pdf",
                filename=pdf_name,
                headers={"Cache-Control": "no-store"},
            )

        raise HTTPException(
            status_code=400,
            detail={
                "error": "Erro de compilação LaTeX.",
                "log": log_output[-20000:],
            },
        )


@router.get("/{project_id}/compile/log")
async def compile_log(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

    # O mais recente vem completo (para a lista de erros/avisos); os anteriores
    # só com um excerto.
    await cur.execute(
        """
        SELECT id, success, duration_ms, compiled_at, LEFT(log_output, 2000) AS log_output
        FROM compile_log
        WHERE project_id = %s
        ORDER BY id DESC
        LIMIT 10
        """,
        (project_id,),
    )
    rows = await cur.fetchall()

    if rows:
        await cur.execute("SELECT log_output FROM compile_log WHERE id = %s", (rows[0]["id"],))
        full = await cur.fetchone()

        if full:
            rows[0]["log_output"] = full["log_output"]

    return rows


@router.get("/{project_id}/pdf")
async def download_pdf(
    project_id: int,
    user=Depends(get_current_user),
):
    """
    PDF do ficheiro principal, para descarregar a partir da lista de projetos.
    Quem pode editar recebe uma compilação nova; quem só pode ver recebe o
    último PDF compilado (viewers não compilam).
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

            await cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
            name = (await cur.fetchone() or {}).get("name") or "project"

            try:
                await _assert_project_owner(project_id, user["id"], cur, require_edit=True)
                can_edit = True
            except HTTPException:
                can_edit = False

            await cur.execute(
                "SELECT filename FROM project_files WHERE project_id = %s AND is_main = 1",
                (project_id,),
            )
            main_row = await cur.fetchone()
            await conn.commit()

    safe_name = re.sub(r"[^A-Za-z0-9_\-]", "_", name) or "project"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.pdf"', "Cache-Control": "no-store"}

    if can_edit:
        response = await compile_project(project_id, file=None, user=user)
        response.headers.update(headers)
        return response

    if main_row:
        pdf = os.path.join(project_dir(project_id), output_pdf_name(main_row["filename"]))
        if os.path.isfile(pdf):
            return FileResponse(pdf, media_type="application/pdf", headers=headers)

    raise HTTPException(
        status_code=404,
        detail={"error": "Este projeto ainda não foi compilado. Pede ao dono para compilar.", "log": ""},
    )


@router.get("/{project_id}/synctex")
async def synctex_inverse_search(
    project_id: int,
    page: int,
    x: float,
    y: float,
    file: Optional[str] = Query(default=None, max_length=255),
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

    project_path = project_dir(project_id)

    # `file` = o .tex que gerou o PDF (quando não é o principal).
    if file:
        await cur.execute(
            "SELECT filename FROM project_files WHERE project_id = %s AND filename = %s",
            (project_id, file),
        )
    else:
        await cur.execute(
            """
            SELECT filename
            FROM project_files
            WHERE project_id = %s AND is_main = 1
            """,
            (project_id,),
        )

    main_row = await cur.fetchone()

    if not main_row or not os.path.isdir(project_path):
        return {
            "file": None,
            "line": None,
            "error": "Ficheiro principal não encontrado.",
        }

    pdf_file = output_pdf_name(main_row["filename"])

    cmd = ["synctex", "edit", "-o", f"{page}:{x}:{y}:{pdf_file}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=project_path,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        return {"file": None, "line": None, "error": "SyncTeX demorou demasiado."}

    output = stdout.decode("utf-8", errors="replace")

    file_match = re.search(r"Input:([^\r\n]+)", output)
    line_match = re.search(r"Line:(\d+)", output)

    if file_match and line_match:
        filepath = file_match.group(1).strip()
        real_base = os.path.realpath(project_path)

        for prefix in (project_path, real_base):
            if filepath.startswith(prefix + "/"):
                filepath = filepath[len(prefix) + 1:]
                break

        # "/projetos/1/./main.tex" → "main.tex"
        filepath = os.path.normpath(filepath).replace("\\", "/")

        return {
            "file": filepath,
            "line": int(line_match.group(1)),
        }

    return {
        "file": None,
        "line": None,
        "error": "Coordenada sem código associado.",
    }
