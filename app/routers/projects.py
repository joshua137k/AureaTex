import os
import base64
import mimetypes
import re
import shutil
import subprocess
import urllib.parse
import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config import BASE_PROJETOS_DIR, GITEA_URL
from core.database import get_db
from core.security import get_current_user

from routers.collab import (
    _rooms,
    _save_tasks,
    freeze_room_for_pull,
    finish_pull_reload,
    abort_pull_reload,
)

from routers.compile import (
    _project_locks,
    _escrever_ficheiros_para_compilacao,
)


router = APIRouter(prefix="/api/projects", tags=["projects"])


_git_locks = defaultdict(asyncio.Lock)


IGNORED_EXTS = (
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".synctex.gz",
    ".synctex(busy)",
    ".fls",
    ".fdb_latexmk",
    ".bcf",
    ".lof",
    ".lot",
    ".mw",
    ".run.xml",
    ".bbl",
    ".blg",
    ".nav",
    ".snm",
    ".vrb",
)


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    template: str = "blank"
    is_public: bool = False
    remote_provider: str = "gitea"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None


class GithubImport(BaseModel):
    repo_name: str


TEMPLATES = {
    "blank": r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}
\usepackage{amsmath}

\title{Novo Documento}
\author{Autor}
\date{\today}

\begin{document}

\maketitle

\section{Introdução}
Escreve aqui o teu texto.

\end{document}""",

    "article": r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{geometry}
\geometry{margin=2.5cm}

\title{Título do Artigo}
\author{Primeiro Autor \and Segundo Autor}
\date{\today}

\begin{document}

\maketitle

\begin{abstract}
Resumo do artigo.
\end{abstract}

\section{Introdução}

\section{Metodologia}

\section{Resultados}

\section{Conclusão}

\bibliographystyle{plain}
\bibliography{refs}

\end{document}""",

    "thesis": r"""\documentclass[12pt,a4paper]{report}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{geometry}
\geometry{margin=3cm}

\title{Título da Tese}
\author{Nome do Autor}
\date{2025}

\begin{document}

\maketitle
\tableofcontents

\chapter{Introdução}

\chapter{Estado da Arte}

\chapter{Metodologia}

\chapter{Resultados e Discussão}

\chapter{Conclusão}

\bibliographystyle{plain}
\bibliography{refs}

\end{document}""",

    "beamer": r"""\documentclass{beamer}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}

\title{Título da Apresentação}
\author{Autor}
\date{\today}

\begin{document}

\begin{frame}
\titlepage
\end{frame}

\begin{frame}{Introdução}
\begin{itemize}
\item Ponto 1
\item Ponto 2
\end{itemize}
\end{frame}

\end{document}""",
}


def _ler_ficheiros_do_disco(project_path: str):
    results = []

    for root_dir, _, filenames in os.walk(project_path):
        for filename in filenames:
            if any(part.startswith(".") for part in root_dir.split(os.sep)):
                continue

            if filename.endswith(IGNORED_EXTS):
                continue

            filepath = os.path.join(root_dir, filename)
            rel_path = os.path.relpath(filepath, project_path).replace("\\", "/")

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    file_content = f.read()

            except UnicodeDecodeError:
                with open(filepath, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")

                mime_type, _ = mimetypes.guess_type(filepath)
                mime_type = mime_type or "application/octet-stream"
                file_content = f"data:{mime_type};base64,{encoded}"

            results.append((rel_path, file_content))

    return results


async def _run_git(args, cwd, **kwargs):
    """
    Executa comandos git numa thread separada para não bloquear o event loop.
    """
    return await asyncio.to_thread(subprocess.run, args, cwd=cwd, **kwargs)


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
            body.name,
            body.description,
            body.template,
            int(body.is_public),
            body.remote_provider,
        ),
    )

    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id = (await cur.fetchone())["id"]

    project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
    os.makedirs(project_path, exist_ok=True)

    content = TEMPLATES.get(body.template, TEMPLATES["blank"])

    await cur.execute(
        """
        INSERT INTO project_files (project_id, filename, content, is_main)
        VALUES (%s, 'main.tex', %s, 1)
        """,
        (project_id, content),
    )

    with open(os.path.join(project_path, "main.tex"), "w", encoding="utf-8") as f:
        f.write(content)

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
        SELECT *
        FROM projects
        WHERE id = %s
          AND (
                owner_id = %s
                OR EXISTS (
                    SELECT 1
                    FROM project_collaborators pc
                    WHERE pc.project_id = %s AND pc.user_id = %s
                )
          )
        """,
        (project_id, user["id"], project_id, user["id"]),
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
        values.append(body.name)

    if body.description is not None:
        updates.append("description = %s")
        values.append(body.description)

    if body.is_public is not None:
        updates.append("is_public = %s")
        values.append(int(body.is_public))

    if updates:
        values.append(project_id)

        await cur.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = %s",
            values,
        )

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

    await cur.execute(
        "DELETE FROM projects WHERE id = %s",
        (project_id,),
    )

    project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))

    if os.path.exists(project_path):
        shutil.rmtree(project_path)

    _git_locks.pop(project_id, None)
    _project_locks.pop(project_id, None)

    if project_id in _rooms:
        _rooms.pop(project_id, None)

    chaves_a_remover = [k for k in list(_save_tasks.keys()) if k[0] == project_id]

    for k in chaves_a_remover:
        _save_tasks[k].cancel()
        _save_tasks.pop(k, None)


# ---------------------------------------------------------------------------
# Pull remoto
# ---------------------------------------------------------------------------
@router.post("/{project_id}/pull-remote")
async def pull_from_remote(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "SELECT * FROM users WHERE id = %s",
        (user["id"],),
    )
    db_user = await cur.fetchone()

    await cur.execute(
        """
        SELECT p.name, p.remote_provider, p.remote_repo, p.gitea_repo
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
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    provider = project.get("remote_provider") or "gitea"
    repo_name = project.get("remote_repo") or project.get("gitea_repo")

    if not repo_name:
        raise HTTPException(
            status_code=400,
            detail="Faz Push primeiro para criar o repositório.",
        )

    # ------------------------------------------------------------------
    # Determinar URL remoto
    # ------------------------------------------------------------------
    if provider == "github":
        if not db_user.get("github_token"):
            raise HTTPException(status_code=401, detail="Conta GitHub não vinculada.")

        gh_token = urllib.parse.quote(db_user["github_token"])

        if "/" in repo_name:
            remote_url = f"https://x-access-token:{gh_token}@github.com/{repo_name}.git"
        else:
            gh_user = db_user.get("github_username")
            remote_url = f"https://x-access-token:{gh_token}@github.com/{gh_user}/{repo_name}.git"

    else:
        if not db_user.get("gitea_token"):
            raise HTTPException(status_code=401, detail="Conta Gitea não vinculada.")

        domain = GITEA_URL.replace("https://", "").replace("http://", "")
        username_encoded = urllib.parse.quote(db_user["username"])
        token_encoded = urllib.parse.quote(db_user["gitea_token"])

        if "/" in repo_name:
            remote_url = f"https://oauth2:{token_encoded}@{domain}/{repo_name}.git"
        else:
            remote_url = f"https://oauth2:{token_encoded}@{domain}/{username_encoded}/{repo_name}.git"

    # O pull usa também o lock do compile para impedir compilações concorrentes.
    async with _git_locks[project_id], _project_locks[project_id]:
        await freeze_room_for_pull(project_id)

        try:
            project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
            os.makedirs(project_path, exist_ok=True)

            lock_file = os.path.join(project_path, ".git", "index.lock")

            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                except OSError:
                    pass

            await _run_git(
                ["git", "config", "--global", "--add", "safe.directory", project_path],
                cwd=project_path,
            )

            # ----------------------------------------------------------
            # Preparar disco com o conteúdo atual da DB.
            # Não guardamos aqui o estado do collab: o remoto é que manda.
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

            await cur.execute(
                """
                SELECT filename, content
                FROM project_files
                WHERE project_id = %s
                """,
                (project_id,),
            )
            files = await cur.fetchall()

            await asyncio.to_thread(
                _escrever_ficheiros_para_compilacao,
                project_path,
                files,
            )

            gitignore_path = os.path.join(project_path, ".gitignore")

            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(["*" + ext for ext in IGNORED_EXTS]) + "\n")

            if not os.path.exists(os.path.join(project_path, ".git")):
                await _run_git(["git", "init"], cwd=project_path)
                await _run_git(["git", "branch", "-M", "main"], cwd=project_path)
                await _run_git(["git", "remote", "add", "origin", remote_url], cwd=project_path)
            else:
                await _run_git(["git", "remote", "set-url", "origin", remote_url], cwd=project_path)

            await _run_git(["git", "config", "user.name", "AureaTeX System"], cwd=project_path)
            await _run_git(["git", "config", "user.email", "system@aureatex.local"], cwd=project_path)

            await _run_git(["git", "add", "."], cwd=project_path)

            time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await _run_git(
                ["git", "commit", "-m", f"Auto-save antes do pull: {time_str}"],
                cwd=project_path,
            )

            # ----------------------------------------------------------
            # Puxar remoto por cima
            # ----------------------------------------------------------
            fetch_proc = await _run_git(
                ["git", "fetch", "origin", "main"],
                cwd=project_path,
                capture_output=True,
                text=True,
            )

            if fetch_proc.returncode != 0:
                stderr = fetch_proc.stderr or ""

                if "Authentication failed" in stderr or "403" in stderr:
                    raise Exception("Sessão expirou ou não tens permissão no remoto.")

                raise Exception(stderr or "Erro no git fetch.")

            reset_proc = await _run_git(
                ["git", "reset", "--hard", "origin/main"],
                cwd=project_path,
                capture_output=True,
                text=True,
            )

            await _run_git(["git", "clean", "-fd"], cwd=project_path)

            if reset_proc.returncode != 0:
                stderr = reset_proc.stderr or ""

                if "Authentication failed" in stderr or "403" in stderr:
                    raise Exception("Sessão expirou ou não tens permissão no remoto.")

                raise Exception(stderr or "Erro no git reset.")

            # ----------------------------------------------------------
            # Limpar ficheiros temporários estranhos
            # ----------------------------------------------------------
            await cur.execute(
                """
                DELETE FROM project_files
                WHERE project_id = %s
                  AND filename LIKE '.fuse_hidden%%'
                """,
                (project_id,),
            )

            # Segurança extra: cancela autosaves antigos que ainda existam.
            for k in list(_save_tasks.keys()):
                if k[0] == project_id:
                    _save_tasks[k].cancel()
                    _save_tasks.pop(k, None)

            # ----------------------------------------------------------
            # Atualizar DB com o conteúdo que veio do remoto
            # ----------------------------------------------------------
            files_updated = 0
            files_from_disk = await asyncio.to_thread(_ler_ficheiros_do_disco, project_path)

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

                files_updated += 1

            # ----------------------------------------------------------
            # Apagar da DB ficheiros que não existem no disco
            # ----------------------------------------------------------
            valid_files = []

            for dp, dn, fnames in os.walk(project_path):
                if any(p.startswith(".") for p in dp.split(os.sep)):
                    continue

                for f in fnames:
                    if not f.endswith(IGNORED_EXTS):
                        valid_files.append(
                            os.path.relpath(
                                os.path.join(dp, f),
                                project_path,
                            ).replace("\\", "/")
                        )

            if valid_files:
                format_strings = ",".join(["%s"] * len(valid_files))

                await cur.execute(
                    f"""
                    DELETE FROM project_files
                    WHERE project_id = %s
                      AND filename NOT IN ({format_strings})
                    """,
                    (project_id, *valid_files),
                )
            else:
                await cur.execute(
                    "DELETE FROM project_files WHERE project_id = %s",
                    (project_id,),
                )

            await cur.execute(
                "UPDATE projects SET updated_at = NOW() WHERE id = %s",
                (project_id,),
            )

            await cur.connection.commit()

            # ------------------------------------------------------------
            # Finaliza o collab e força reload dos clientes.
            # ------------------------------------------------------------
            await finish_pull_reload(project_id)

            return {
                "status": "success",
                "message": f"Pull concluído! {files_updated} ficheiros sincronizados.",
            }

        except HTTPException:
            await abort_pull_reload(project_id)
            raise

        except Exception as e:
            await abort_pull_reload(project_id)
            raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Push remoto
# ---------------------------------------------------------------------------
@router.post("/{project_id}/publish-remote")
async def publish_to_remote(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "SELECT * FROM users WHERE id = %s",
        (user["id"],),
    )
    db_user = await cur.fetchone()

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

    provider = project.get("remote_provider") or "gitea"
    repo_name = project.get("remote_repo") or project.get("gitea_repo")

    if not repo_name:
        repo_name = re.sub(
            r"[^a-zA-Z0-9-]",
            "",
            project["name"].replace(" ", "-"),
        ).lower()

        if not repo_name:
            repo_name = f"projeto-{project_id}"

        await cur.execute(
            "UPDATE projects SET remote_repo = %s, gitea_repo = %s WHERE id = %s",
            (repo_name, repo_name, project_id),
        )

    # ------------------------------------------------------------------
    # Criar repositório remoto se ainda não existir
    # ------------------------------------------------------------------
    if "/" not in repo_name:
        async with httpx.AsyncClient() as client:
            if provider == "github":
                if not db_user.get("github_token"):
                    raise HTTPException(status_code=401, detail="Conta GitHub não vinculada.")

                resp = await client.post(
                    "https://api.github.com/user/repos",
                    headers={
                        "Authorization": f"Bearer {db_user['github_token']}",
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
                        detail=f"Erro repositório GitHub: {resp.text}",
                    )

            else:
                if not db_user.get("gitea_token"):
                    raise HTTPException(status_code=401, detail="Conta Gitea não vinculada.")

                resp = await client.post(
                    f"{GITEA_URL}/api/v1/user/repos",
                    headers={"Authorization": f"token {db_user['gitea_token']}"},
                    json={
                        "name": repo_name,
                        "private": True,
                        "description": "Projeto AureaTeX",
                    },
                )

                if resp.status_code not in (201, 409):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Erro repositório Gitea: {resp.text}",
                    )

    # ------------------------------------------------------------------
    # URL remoto
    # ------------------------------------------------------------------
    if provider == "github":
        if not db_user.get("github_token"):
            raise HTTPException(status_code=401, detail="Conta GitHub não vinculada.")

        gh_token = urllib.parse.quote(db_user["github_token"])

        if "/" in repo_name:
            remote_url = f"https://x-access-token:{gh_token}@github.com/{repo_name}.git"
        else:
            gh_user = db_user.get("github_username")
            remote_url = f"https://x-access-token:{gh_token}@github.com/{gh_user}/{repo_name}.git"

    else:
        if not db_user.get("gitea_token"):
            raise HTTPException(status_code=401, detail="Conta Gitea não vinculada.")

        domain = GITEA_URL.replace("https://", "").replace("http://", "")
        username_encoded = urllib.parse.quote(db_user["username"])
        token_encoded = urllib.parse.quote(db_user["gitea_token"])

        if "/" in repo_name:
            remote_url = f"https://oauth2:{token_encoded}@{domain}/{repo_name}.git"
        else:
            remote_url = f"https://oauth2:{token_encoded}@{domain}/{username_encoded}/{repo_name}.git"

    # O push também usa o lock do compile para não chocar com compilações.
    async with _git_locks[project_id], _project_locks[project_id]:
        # No push, guarda o estado atual do collab na DB antes de enviar.
        if project_id in _rooms:
            room = _rooms[project_id]

            if not getattr(room, "reloading", False):
                async with room._lock:
                    for fname, doc in list(room.docs.items()):
                        if doc.content:
                            await cur.execute(
                                """
                                INSERT INTO project_files (project_id, filename, content)
                                VALUES (%s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                    content = VALUES(content),
                                    updated_at = NOW()
                                """,
                                (project_id, fname, doc.content),
                            )

        project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
        os.makedirs(project_path, exist_ok=True)

        lock_file = os.path.join(project_path, ".git", "index.lock")

        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except OSError:
                pass

        await _run_git(
            ["git", "config", "--global", "--add", "safe.directory", project_path],
            cwd=project_path,
        )

        try:
            await cur.execute(
                """
                SELECT filename, content
                FROM project_files
                WHERE project_id = %s
                """,
                (project_id,),
            )
            files = await cur.fetchall()

            await asyncio.to_thread(
                _escrever_ficheiros_para_compilacao,
                project_path,
                files,
            )

            gitignore_path = os.path.join(project_path, ".gitignore")

            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(["*" + ext for ext in IGNORED_EXTS]) + "\n")

            if not os.path.exists(os.path.join(project_path, ".git")):
                await _run_git(["git", "init"], cwd=project_path, check=True)
                await _run_git(["git", "branch", "-M", "main"], cwd=project_path)
                await _run_git(
                    ["git", "remote", "add", "origin", remote_url],
                    cwd=project_path,
                    check=True,
                )
            else:
                await _run_git(
                    ["git", "remote", "set-url", "origin", remote_url],
                    cwd=project_path,
                    check=True,
                )

            await _run_git(["git", "config", "user.name", "AureaTeX System"], cwd=project_path)
            await _run_git(["git", "config", "user.email", "system@aureatex.local"], cwd=project_path)

            await _run_git(["git", "add", "."], cwd=project_path, check=True)

            time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await _run_git(
                ["git", "commit", "-m", f"Auto-save AureaTeX: {time_str}"],
                cwd=project_path,
            )

            proc = await _run_git(
                ["git", "push", "-u", "origin", "main"],
                cwd=project_path,
                capture_output=True,
                text=True,
            )

            if proc.returncode != 0:
                stderr = proc.stderr or ""

                if "Authentication failed" in stderr or "403" in stderr:
                    raise Exception("Sessão expirou ou não tens permissão no remoto.")

                safe_error = re.sub(r":(.*?)\@", ":***@", stderr)
                raise Exception(f"Falha na operação Git: {safe_error}")

            return {
                "status": "success",
                "message": f"Guardado com sucesso no {provider.capitalize()}!",
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
@router.get("/github/repos")
async def list_github_repos(
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "SELECT github_token FROM users WHERE id = %s",
        (user["id"],),
    )
    db_user = await cur.fetchone()

    if not db_user or not db_user.get("github_token"):
        raise HTTPException(status_code=401, detail="Conta GitHub não vinculada.")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user/repos?per_page=100&sort=updated",
            headers={
                "Authorization": f"Bearer {db_user['github_token']}",
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
    await cur.execute(
        "SELECT github_token FROM users WHERE id = %s",
        (user["id"],),
    )
    db_user = await cur.fetchone()

    if not db_user or not db_user.get("github_token"):
        raise HTTPException(status_code=401, detail="GitHub não vinculado.")

    name = body.repo_name.split("/")[-1]

    await cur.execute(
        """
        INSERT INTO projects (owner_id, name, template, remote_provider, remote_repo)
        VALUES (%s, %s, 'github', 'github', %s)
        """,
        (user["id"], name, body.repo_name),
    )

    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id = (await cur.fetchone())["id"]

    try:
        await pull_from_remote(project_id, user, cur)

        await cur.execute(
            """
            SELECT filename
            FROM project_files
            WHERE project_id = %s
              AND filename LIKE %s
            LIMIT 1
            """,
            (project_id, "%.tex"),
        )
        first_tex = await cur.fetchone()

        if first_tex:
            await cur.execute(
                """
                UPDATE project_files
                SET is_main = 1
                WHERE project_id = %s
                  AND filename = %s
                """,
                (project_id, first_tex["filename"]),
            )

        await cur.connection.commit()

    except Exception as e:
        await cur.execute(
            "DELETE FROM projects WHERE id = %s",
            (project_id,),
        )

        await cur.connection.commit()

        raise HTTPException(
            status_code=400,
            detail=f"Erro ao clonar repositório: {str(e)}",
        )

    return {
        "id": project_id,
        "name": name,
    }