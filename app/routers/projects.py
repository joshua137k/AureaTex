import asyncio
import base64
import os
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from typing import Optional

import httpx
import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import GITEA_URL
from core.database import get_db
from core.paths import project_dir
from core.security import get_current_user
from core.storage import (
    IGNORED_EXTS,
    forget_project,
    read_project_tree,
    sync_project_to_disk,
    write_file,
)
from core.templates import TEMPLATES

from routers.collab import (
    abort_pull_reload,
    close_room,
    finish_pull_reload,
    flush_project,
    freeze_room_for_pull,
)
from routers.compile import _project_locks
from routers.files import PROVIDERS, validate_project_name


router = APIRouter(prefix="/api/projects", tags=["projects"])


_git_locks = defaultdict(asyncio.Lock)

GIT_TIMEOUT_SECS = 180
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$")


class ProjectCreate(BaseModel):
    name: str = Field(max_length=255)
    description: str = Field(default="", max_length=5000)
    template: str = "blank"
    is_public: bool = False
    remote_provider: str = "gitea"


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    is_public: Optional[bool] = None


class GithubImport(BaseModel):
    repo_name: str = Field(max_length=200)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------
def _git_env(auth: Optional[str] = None) -> dict:
    """
    Ambiente para o git. A configuração vai por variáveis de ambiente para:
      - o token nunca ficar gravado em .git/config nem aparecer nos argumentos;
      - neutralizar hooks, fsmonitor e symlinks vindos de repositórios alheios.
    """
    config = [
        ("safe.directory", "*"),
        ("core.hooksPath", "/dev/null"),
        ("core.fsmonitor", "false"),
        ("core.symlinks", "false"),
        ("credential.helper", ""),
        ("protocol.file.allow", "never"),
        ("protocol.ext.allow", "never"),
        ("user.name", "AureaTeX System"),
        ("user.email", "system@aureatex.local"),
        ("init.defaultBranch", "main"),
    ]

    if auth:
        token = base64.b64encode(auth.encode()).decode()
        config.append(("http.extraHeader", f"Authorization: Basic {token}"))

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/bin/true"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_COUNT"] = str(len(config))

    for i, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value

    return env


def _scrub(text: str) -> str:
    """Remove credenciais de mensagens de erro do git."""
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text or "")
    text = re.sub(r"(Authorization:\s*\w+\s+)\S+", r"\1***", text)
    return text.strip()[:2000]


async def _run_git(args, cwd, auth: Optional[str] = None, check: bool = False):
    """
    Executa comandos git numa thread separada para não bloquear o event loop.
    """
    def run():
        return subprocess.run(
            args,
            cwd=cwd,
            env=_git_env(auth),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT_SECS,
            stdin=subprocess.DEVNULL,
        )

    try:
        proc = await asyncio.to_thread(run)
    except subprocess.TimeoutExpired:
        raise Exception("O git demorou demasiado tempo a responder.")

    if check and proc.returncode != 0:
        raise _git_error(proc)

    return proc


def _git_error(proc) -> Exception:
    stderr = proc.stderr or proc.stdout or ""

    if "Authentication failed" in stderr or "403" in stderr or "401" in stderr:
        return Exception(
            "Sessão expirou ou não tens permissão no remoto. Volta a ligar a conta nas definições."
        )

    return Exception(_scrub(stderr) or "Erro na operação Git.")


def _remote_for(project: dict, db_user: dict) -> tuple[str, str, str]:
    """Devolve (provider, remote_url, auth) — sem credenciais no URL."""
    provider = project.get("remote_provider") or "gitea"
    repo_name = project.get("remote_repo") or project.get("gitea_repo") or ""

    if not _REPO_NAME_RE.match(repo_name) or ".." in repo_name:
        raise HTTPException(status_code=400, detail="Nome de repositório remoto inválido.")

    if provider == "github":
        if not db_user.get("github_token"):
            raise HTTPException(status_code=401, detail="Conta GitHub não vinculada.")

        owner_repo = repo_name if "/" in repo_name else f"{db_user.get('github_username')}/{repo_name}"
        base_url = "https://github.com"
        auth = f"x-access-token:{db_user['github_token']}"

    else:
        if not db_user.get("gitea_token"):
            raise HTTPException(status_code=401, detail="Conta Gitea não vinculada.")

        owner_repo = repo_name if "/" in repo_name else f"{db_user['username']}/{repo_name}"
        base_url = GITEA_URL
        auth = f"oauth2:{db_user['gitea_token']}"

    if not _REPO_NAME_RE.match(owner_repo) or ".." in owner_repo:
        raise HTTPException(status_code=400, detail="Nome de repositório remoto inválido.")

    return provider, f"{base_url}/{owner_repo}.git", auth


async def _prepare_repo(project_path: str, remote_url: str) -> None:
    """git init (se preciso), .gitignore e remote sem credenciais."""
    lock_file = os.path.join(project_path, ".git", "index.lock")

    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except OSError:
            pass

    gitignore_path = os.path.join(project_path, ".gitignore")

    if not os.path.exists(gitignore_path):
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write("\n".join(["*" + ext for ext in IGNORED_EXTS]) + "\n")

    if not os.path.isdir(os.path.join(project_path, ".git")):
        await _run_git(["git", "init"], cwd=project_path, check=True)
        await _run_git(["git", "branch", "-M", "main"], cwd=project_path)
        await _run_git(["git", "remote", "add", "origin", remote_url], cwd=project_path, check=True)
    else:
        # set-url também limpa tokens que versões antigas gravavam no URL.
        proc = await _run_git(["git", "remote", "set-url", "origin", remote_url], cwd=project_path)

        if proc.returncode != 0:
            await _run_git(["git", "remote", "add", "origin", remote_url], cwd=project_path, check=True)


async def _commit_all(project_path: str, message: str) -> None:
    await _run_git(["git", "add", "-A", "."], cwd=project_path, check=True)
    # Sai com código != 0 quando não há nada para commitar — é normal.
    await _run_git(["git", "commit", "-m", message], cwd=project_path)


async def _project_for_git(project_id: int, user: dict, cur) -> dict:
    await cur.execute(
        """
        SELECT p.id, p.name, p.remote_provider, p.remote_repo, p.gitea_repo
        FROM projects p
        WHERE p.id = %s
          AND (
                p.owner_id = %s
                OR EXISTS (
                    SELECT 1
                    FROM project_collaborators pc
                    WHERE pc.project_id = p.id
                      AND pc.user_id = %s
                      AND pc.role = 'editor'
                )
          )
        """,
        (project_id, user["id"], user["id"]),
    )

    project = await cur.fetchone()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Projeto não encontrado ou não tens permissão.",
        )

    return project


# ---------------------------------------------------------------------------
# CRUD projetos
# ---------------------------------------------------------------------------
@router.get("")
async def list_projects(
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        """
        SELECT p.*, COUNT(pc.id) as collaborators
        FROM projects p
        LEFT JOIN project_collaborators pc ON p.id = pc.project_id
        WHERE p.owner_id = %s
        GROUP BY p.id
        ORDER BY p.updated_at DESC
        """,
        (user["id"],),
    )

    return await cur.fetchall()


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    name = validate_project_name(body.name)
    template = body.template if body.template in TEMPLATES else "blank"
    provider = body.remote_provider if body.remote_provider in PROVIDERS else "gitea"

    try:
        await cur.execute(
            """
            INSERT INTO projects (
                owner_id,
                name,
                description,
                template,
                is_public,
                remote_provider
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user["id"],
                name,
                body.description,
                template,
                int(body.is_public),
                provider,
            ),
        )
    except pymysql.err.IntegrityError:
        raise HTTPException(status_code=409, detail="Já tens um projeto com esse nome.")

    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id = (await cur.fetchone())["id"]

    content = TEMPLATES[template]

    await cur.execute(
        """
        INSERT INTO project_files (project_id, filename, content, is_main)
        VALUES (%s, 'main.tex', %s, 1)
        """,
        (project_id, content),
    )

    # Pasta limpa: um id reutilizado não pode herdar ficheiros antigos.
    await asyncio.to_thread(shutil.rmtree, project_dir(project_id), True)
    await asyncio.to_thread(write_file, project_id, "main.tex", content)

    await cur.execute(
        "SELECT * FROM projects WHERE id = %s",
        (project_id,),
    )

    return await cur.fetchone()


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        """
        SELECT p.*,
               CASE WHEN p.owner_id = %s THEN 'owner' ELSE pc.role END AS role
        FROM projects p
        LEFT JOIN project_collaborators pc
            ON pc.project_id = p.id AND pc.user_id = %s
        WHERE p.id = %s
          AND (p.owner_id = %s OR pc.id IS NOT NULL)
        """,
        (user["id"], user["id"], project_id, user["id"]),
    )

    project = await cur.fetchone()

    if not project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    return project


@router.patch("/{project_id}")
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "SELECT id FROM projects WHERE id = %s AND owner_id = %s",
        (project_id, user["id"]),
    )

    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    updates = []
    values = []

    if body.name is not None:
        updates.append("name = %s")
        values.append(validate_project_name(body.name))

    if body.description is not None:
        updates.append("description = %s")
        values.append(body.description)

    if body.is_public is not None:
        updates.append("is_public = %s")
        values.append(int(body.is_public))

    if updates:
        values.append(project_id)

        try:
            await cur.execute(
                f"UPDATE projects SET {', '.join(updates)} WHERE id = %s",
                values,
            )
        except pymysql.err.IntegrityError:
            raise HTTPException(status_code=409, detail="Já tens um projeto com esse nome.")

    await cur.execute(
        "SELECT * FROM projects WHERE id = %s",
        (project_id,),
    )

    return await cur.fetchone()


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "SELECT id FROM projects WHERE id = %s AND owner_id = %s",
        (project_id, user["id"]),
    )

    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    # Fecha a sala primeiro para nenhum autosave correr a meio.
    await close_room(project_id, reason="Project deleted")

    await cur.execute(
        "DELETE FROM projects WHERE id = %s",
        (project_id,),
    )

    await asyncio.to_thread(shutil.rmtree, project_dir(project_id), True)

    forget_project(project_id)
    _git_locks.pop(project_id, None)
    _project_locks.pop(project_id, None)


# ---------------------------------------------------------------------------
# Pull remoto
# ---------------------------------------------------------------------------
@router.post("/{project_id}/pull-remote")
async def pull_from_remote(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    project = await _project_for_git(project_id, user, cur)

    if not (project.get("remote_repo") or project.get("gitea_repo")):
        raise HTTPException(
            status_code=400,
            detail="Faz Push primeiro para criar o repositório.",
        )

    provider, remote_url, auth = _remote_for(project, user)

    # O pull usa também o lock do compile para impedir compilações concorrentes.
    async with _git_locks[project_id], _project_locks[project_id]:
        # Guarda o que estiver pendente ANTES de congelar: fica no commit de
        # segurança local, recuperável com git se o pull apagar algo.
        await flush_project(project_id)
        await freeze_room_for_pull(project_id)

        try:
            project_path = project_dir(project_id)
            os.makedirs(project_path, exist_ok=True)

            # ----------------------------------------------------------
            # Preparar disco com o conteúdo atual da BD.
            # ----------------------------------------------------------
            for ext in IGNORED_EXTS:
                await cur.execute(
                    """
                    DELETE FROM project_files
                    WHERE project_id = %s
                      AND filename LIKE %s
                    """,
                    (project_id, f"%{ext}"),
                )

            forget_project(project_id)
            await sync_project_to_disk(project_id, cur)

            await _prepare_repo(project_path, remote_url)

            time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await _commit_all(project_path, f"Auto-save antes do pull: {time_str}")

            # ----------------------------------------------------------
            # Puxar remoto por cima
            # ----------------------------------------------------------
            fetch_proc = await _run_git(
                ["git", "fetch", "origin", "main"],
                cwd=project_path,
                auth=auth,
            )

            if fetch_proc.returncode != 0:
                raise _git_error(fetch_proc)

            await _run_git(
                ["git", "reset", "--hard", "origin/main"],
                cwd=project_path,
                check=True,
            )

            await _run_git(["git", "clean", "-fd"], cwd=project_path)

            # ----------------------------------------------------------
            # Atualizar BD com o conteúdo que veio do remoto
            # ----------------------------------------------------------
            files_from_disk = await asyncio.to_thread(read_project_tree, project_path)

            for rel_path, file_content in files_from_disk:
                await cur.execute(
                    """
                    INSERT INTO project_files (project_id, filename, content)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        content = VALUES(content),
                        updated_at = NOW()
                    """,
                    (project_id, rel_path, file_content),
                )

            # ----------------------------------------------------------
            # Apagar da BD ficheiros que não existem no disco
            # ----------------------------------------------------------
            valid_files = [rel_path for rel_path, _ in files_from_disk]

            await cur.execute(
                "SELECT filename, is_main FROM project_files WHERE project_id = %s",
                (project_id,),
            )
            existing = await cur.fetchall()

            keep = set(valid_files)
            to_delete = [r["filename"] for r in existing if r["filename"] not in keep]

            for i in range(0, len(to_delete), 500):
                chunk = to_delete[i:i + 500]
                marks = ",".join(["%s"] * len(chunk))

                await cur.execute(
                    f"""
                    DELETE FROM project_files
                    WHERE project_id = %s
                      AND filename IN ({marks})
                    """,
                    (project_id, *chunk),
                )

            # Se o ficheiro principal desapareceu no remoto, escolhe outro.
            if not any(r["is_main"] and r["filename"] in keep for r in existing):
                await _pick_main_file(project_id, cur)

            await cur.execute(
                "UPDATE projects SET updated_at = NOW() WHERE id = %s",
                (project_id,),
            )

            await cur.connection.commit()
            forget_project(project_id)

            # ------------------------------------------------------------
            # Finaliza o collab e força reload dos clientes.
            # ------------------------------------------------------------
            await finish_pull_reload(project_id)

            return {
                "status": "success",
                "message": f"Pull concluído! {len(files_from_disk)} ficheiros sincronizados.",
            }

        except HTTPException:
            await abort_pull_reload(project_id)
            raise

        except Exception as e:
            await cur.connection.rollback()
            await abort_pull_reload(project_id)
            raise HTTPException(status_code=500, detail=_scrub(str(e)))


async def _pick_main_file(project_id: int, cur) -> None:
    """Define um ficheiro principal quando o projeto não tem nenhum."""
    await cur.execute(
        """
        SELECT filename, (content LIKE %s) AS has_class
        FROM project_files
        WHERE project_id = %s AND filename LIKE %s
        """,
        ("%\\\\documentclass%", project_id, "%.tex"),
    )
    candidates = await cur.fetchall()

    if not candidates:
        return

    def rank(row):
        name = row["filename"]
        base = name.split("/")[-1].lower()
        return (
            0 if base in ("main.tex", "thesis.tex", "tese.tex") else 1,
            0 if row["has_class"] else 1,
            name.count("/"),
            name.lower(),
        )

    best = min(candidates, key=rank)["filename"]

    await cur.execute(
        "UPDATE project_files SET is_main = (filename = %s) WHERE project_id = %s",
        (best, project_id),
    )


# ---------------------------------------------------------------------------
# Push remoto
# ---------------------------------------------------------------------------
@router.post("/{project_id}/publish-remote")
async def publish_to_remote(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    project = await _project_for_git(project_id, user, cur)

    provider = project.get("remote_provider") or "gitea"
    repo_name = project.get("remote_repo") or project.get("gitea_repo")

    if not repo_name:
        repo_name = re.sub(
            r"[^a-zA-Z0-9-]",
            "",
            project["name"].replace(" ", "-"),
        ).lower().strip("-")

        if not repo_name:
            repo_name = f"projeto-{project_id}"

        await cur.execute(
            "UPDATE projects SET remote_repo = %s, gitea_repo = %s WHERE id = %s",
            (repo_name, repo_name, project_id),
        )
        project["remote_repo"] = repo_name

    provider, remote_url, auth = _remote_for(project, user)

    # ------------------------------------------------------------------
    # Criar repositório remoto se ainda não existir
    # ------------------------------------------------------------------
    if "/" not in repo_name:
        async with httpx.AsyncClient(timeout=20) as client:
            if provider == "github":
                resp = await client.post(
                    "https://api.github.com/user/repos",
                    headers={
                        "Authorization": f"Bearer {user['github_token']}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                    json={
                        "name": repo_name,
                        "private": True,
                        "description": "Projeto AureaTeX",
                    },
                )

                if resp.status_code not in (201, 409, 422):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Erro repositório GitHub: {_scrub(resp.text)[:300]}",
                    )

            else:
                resp = await client.post(
                    f"{GITEA_URL}/api/v1/user/repos",
                    headers={"Authorization": f"token {user['gitea_token']}"},
                    json={
                        "name": repo_name,
                        "private": True,
                        "description": "Projeto AureaTeX",
                    },
                )

                if resp.status_code not in (201, 409):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Erro repositório Gitea: {_scrub(resp.text)[:300]}",
                    )

    # O push também usa o lock do compile para não chocar com compilações.
    async with _git_locks[project_id], _project_locks[project_id]:
        # Guarda na BD o que ainda só estiver na memória do collab.
        await flush_project(project_id)

        project_path = project_dir(project_id)
        os.makedirs(project_path, exist_ok=True)

        try:
            await sync_project_to_disk(project_id, cur)
            await cur.connection.commit()

            await _prepare_repo(project_path, remote_url)

            time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await _commit_all(project_path, f"Auto-save AureaTeX: {time_str}")

            proc = await _run_git(
                ["git", "push", "-u", "origin", "main"],
                cwd=project_path,
                auth=auth,
            )

            if proc.returncode != 0:
                stderr = proc.stderr or ""

                if "rejected" in stderr or "fetch first" in stderr or "non-fast-forward" in stderr:
                    raise Exception(
                        "O remoto tem alterações que não estão aqui. Faz Pull primeiro."
                    )

                raise _git_error(proc)

            return {
                "status": "success",
                "message": f"Guardado com sucesso no {provider.capitalize()}!",
            }

        except HTTPException:
            raise

        except Exception as e:
            raise HTTPException(status_code=500, detail=_scrub(str(e)))


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
@router.get("/github/repos")
async def list_github_repos(
    user=Depends(get_current_user),
):
    if not user.get("github_token"):
        raise HTTPException(status_code=401, detail="Conta GitHub não vinculada.")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://api.github.com/user/repos?per_page=100&sort=updated",
            headers={
                "Authorization": f"Bearer {user['github_token']}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        if resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail="Erro ao buscar repositórios no GitHub.",
            )

        repos = resp.json()

        return [
            {
                "name": r["name"],
                "full_name": r["full_name"],
                "private": r["private"],
            }
            for r in repos
        ]


@router.post("/import-github")
async def import_github_repo(
    body: GithubImport,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    if not user.get("github_token"):
        raise HTTPException(status_code=401, detail="GitHub não vinculado.")

    repo_name = body.repo_name.strip()

    if not _REPO_NAME_RE.match(repo_name) or "/" not in repo_name or ".." in repo_name:
        raise HTTPException(status_code=400, detail="Repositório inválido (usa o formato dono/repositório).")

    name = repo_name.split("/")[-1]

    try:
        await cur.execute(
            """
            INSERT INTO projects (owner_id, name, template, remote_provider, remote_repo)
            VALUES (%s, %s, 'github', 'github', %s)
            """,
            (user["id"], name, repo_name),
        )
    except pymysql.err.IntegrityError:
        raise HTTPException(status_code=409, detail="Já tens um projeto com esse nome.")

    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id = (await cur.fetchone())["id"]

    # Pasta limpa: um id reutilizado não pode herdar ficheiros antigos.
    await asyncio.to_thread(shutil.rmtree, project_dir(project_id), True)

    try:
        await pull_from_remote(project_id, user, cur)
        await cur.connection.commit()

    except Exception as e:
        await cur.connection.rollback()

        await cur.execute(
            "DELETE FROM projects WHERE id = %s",
            (project_id,),
        )
        await cur.connection.commit()

        await asyncio.to_thread(shutil.rmtree, project_dir(project_id), True)

        detail = e.detail if isinstance(e, HTTPException) else str(e)

        raise HTTPException(
            status_code=400,
            detail=f"Erro ao clonar repositório: {detail}",
        )

    return {
        "id": project_id,
        "name": name,
    }
