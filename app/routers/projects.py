import os
import base64  
import re
import subprocess
import urllib.parse
from datetime import datetime
from typing import Optional
import asyncio
from collections import defaultdict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config import BASE_PROJETOS_DIR, GITEA_URL
from core.database import get_db
from core.security import get_current_user
from routers.collab import sync_doc_content,notify_room
from routers.collab import _rooms
from routers.compile import _project_locks, _escrever_ficheiros_para_compilacao
router = APIRouter(prefix="/api/projects", tags=["projects"])
_git_locks = defaultdict(asyncio.Lock)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name:        str
    description: str  = ""
    template:    str  = "blank"
    is_public:   bool = False


class ProjectUpdate(BaseModel):
    name:        Optional[str]  = None
    description: Optional[str]  = None
    is_public:   Optional[bool] = None


# ---------------------------------------------------------------------------
# Templates LaTeX
# ---------------------------------------------------------------------------

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
    import base64
    import mimetypes
    results = []
    for root_dir, _, filenames in os.walk(project_path):
        for filename in filenames:
            if any(part.startswith('.') for part in root_dir.split(os.sep)): continue
            if filename.endswith(IGNORED_EXTS): continue

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


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@router.get("")
async def list_projects(user=Depends(get_current_user), cur=Depends(get_db)):
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
        "INSERT INTO projects (owner_id, name, description, template, is_public) VALUES (%s, %s, %s, %s, %s)",
        (user["id"], body.name, body.description, body.template, int(body.is_public)),
    )
    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id = (await cur.fetchone())["id"]

    project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
    os.makedirs(project_path, exist_ok=True)

    content = TEMPLATES.get(body.template, TEMPLATES["blank"])
    await cur.execute(
        "INSERT INTO project_files (project_id, filename, content, is_main) VALUES (%s, 'main.tex', %s, 1)",
        (project_id, content),
    )
    with open(os.path.join(project_path, "main.tex"), "w", encoding="utf-8") as f:
        f.write(content)

    await cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
    return await cur.fetchone()


@router.get("/{project_id}")
async def get_project(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        """
        SELECT * FROM projects WHERE id = %s AND (
            owner_id = %s
            OR EXISTS (
                SELECT 1 FROM project_collaborators pc
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

    updates, values = [], []
    if body.name        is not None: updates.append("name = %s");        values.append(body.name)
    if body.description is not None: updates.append("description = %s"); values.append(body.description)
    if body.is_public   is not None: updates.append("is_public = %s");   values.append(int(body.is_public))

    if updates:
        values.append(project_id)
        await cur.execute(f"UPDATE projects SET {', '.join(updates)} WHERE id = %s", values)

    await cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
    return await cur.fetchone()


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    import shutil

    await cur.execute(
        "SELECT id FROM projects WHERE id = %s AND owner_id = %s",
        (project_id, user["id"]),
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    await cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))

    project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
    if os.path.exists(project_path):
        shutil.rmtree(project_path)

    # =========================================================================
    # CORREÇÃO MEMORY LEAK: Limpar os Locks deste projeto da RAM
    # =========================================================================
    
    # 1. Limpar Lock do Git (que está neste ficheiro)
    _git_locks.pop(project_id, None)
    
    # 2. Limpar Lock do Compile (importado localmente para evitar dependências circulares)
    
    _project_locks.pop(project_id, None)


IGNORED_EXTS = (
    '.aux', '.log', '.out', '.toc', '.synctex.gz', '.fls', '.fdb_latexmk',
    '.bcf', '.lof', '.lot', '.mw', '.run.xml', '.bbl', '.blg', '.nav', '.snm', '.vrb'
)




async def _run_git(args, cwd, **kwargs):
    """Executa comandos git numa thread separada para não bloquear o event loop do FastAPI."""
    return await asyncio.to_thread(subprocess.run, args, cwd=cwd, **kwargs)


@router.post("/{project_id}/pull-gitea")
async def pull_from_gitea(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    print(f"\n[DEBUG PULL] INICIANDO PULL DO GITEA - PROJETO {project_id} - USER {user['username']}")
    
    await cur.execute("SELECT gitea_token FROM users WHERE id = %s", (user["id"],))
    row = await cur.fetchone()
    if not row or not row["gitea_token"]:
        raise HTTPException(status_code=401, detail="Token do Gitea não encontrado.")

    gitea_token = row["gitea_token"]

    await cur.execute(
        """
        SELECT p.name, p.gitea_repo FROM projects p WHERE p.id = %s AND (
            p.owner_id = %s OR EXISTS (
                SELECT 1 FROM project_collaborators pc
                WHERE pc.project_id = p.id AND pc.user_id = %s
            )
        )
        """,
        (project_id, user["id"], user["id"]),
    )
    project = await cur.fetchone()
    if not project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    repo_name = project["gitea_repo"]
    if not repo_name:
        raise HTTPException(status_code=400, detail="Faz Push primeiro para criar o repositório.")
        
    async with _git_locks[project_id]:
        print(f"[DEBUG PULL] Lock adquirido. Verificando RAM (_rooms)...")
        # 0. FORÇAR A RAM A IR PARA A BASE DE DADOS IMEDIATAMENTE
        if project_id in _rooms:
            room = _rooms[project_id]
            async with room._lock:
                for fname, doc in list(room.docs.items()):
                    if doc.content:
                        print(f"[DEBUG PULL] Guardando RAM -> BD antes do pull: {fname}")
                        await cur.execute(
                            """
                            INSERT INTO project_files (project_id, filename, content)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
                            """,
                            (project_id, fname, doc.content)
                        )

        project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
        os.makedirs(project_path, exist_ok=True)
        print(f"[DEBUG PULL] Pasta local de trabalho: {project_path}")

        domain = GITEA_URL.replace("https://", "").replace("http://", "")
        username_encoded = urllib.parse.quote(user["username"])
        gitea_token_encoded = urllib.parse.quote(gitea_token)
        remote_url = f"https://oauth2:{gitea_token_encoded}@{domain}/{username_encoded}/{repo_name}.git"

        try:
            print("[DEBUG PULL] Passo 1: Espelhando BD para o Disco local antes dos comandos Git.")
            # 1. Limpar lixo antigo e espelhar BD no Disco
            for ext in IGNORED_EXTS:
                await cur.execute("DELETE FROM project_files WHERE project_id = %s AND filename LIKE %s", (project_id, f"%{ext}"))
            
            await cur.execute("SELECT filename, content FROM project_files WHERE project_id = %s", (project_id,))
            files = await cur.fetchall()
            await asyncio.to_thread(_escrever_ficheiros_para_compilacao, project_path, files)

            gitignore_path = os.path.join(project_path, ".gitignore")
            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(["*" + ext for ext in IGNORED_EXTS]) + "\n")

            print("[DEBUG PULL] Passo 2: Configurando Git.")
            # 2. Configurar Git
            if not os.path.exists(os.path.join(project_path, ".git")):
                print("[DEBUG PULL] Inicializando novo repo git local...")
                await _run_git(["git", "init"], cwd=project_path)
                await _run_git(["git", "branch", "-M", "main"], cwd=project_path)
                await _run_git(["git", "remote", "add", "origin", remote_url], cwd=project_path)
            else:
                print("[DEBUG PULL] Repo git já existe, atualizando remote...")
                await _run_git(["git", "remote", "set-url", "origin", remote_url], cwd=project_path)
            
            await _run_git(["git", "config", "user.name", "JoshuaTeX System"], cwd=project_path)
            await _run_git(["git", "config", "user.email", "system@joshuatex.local"], cwd=project_path)
            
            print("[DEBUG PULL] Passo 3: Criando commit de segurança local.")
            # 3. Commit Local de Segurança
            await _run_git(["git", "add", "."], cwd=project_path)
            time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await _run_git(["git", "commit", "-m", f"Auto-save antes do pull: {time_str}"], cwd=project_path)

            print("[DEBUG PULL] Passo 4: Forçar reset igual à cloud...")
            
            # 4. Fetch + Hard Reset (CAGA PARA O LOCAL, PEGA EXATAMENTE O DA NUVEM)
            await _run_git(["git", "fetch", "origin", "main"], cwd=project_path)
            
            proc = await _run_git([
                "git", "reset", "--hard", "origin/main"
            ], cwd=project_path, capture_output=True, text=True)
            
            # Opcional mas recomendado: apaga ficheiros não monitorizados que tenham sobrado
            await _run_git(["git", "clean", "-fd"], cwd=project_path)

            print(f"[DEBUG PULL] --- RESULTADO GIT RESET ---")
            print(f"[DEBUG PULL] Return code: {proc.returncode}")
            print(f"[DEBUG PULL] Stdout:\n{proc.stdout}")
            print(f"[DEBUG PULL] Stderr:\n{proc.stderr}")
            print(f"[DEBUG PULL] --------------------------")

            if proc.returncode != 0:
                if "Authentication failed" in proc.stderr:
                    raise Exception("A tua sessão expirou! Faz Logout e entra novamente.")

            print("[DEBUG PULL] Passo 5: Lendo ficheiros ATUALIZADOS do disco e guardando na Base de Dados.")

            await cur.execute("DELETE FROM project_files WHERE project_id = %s AND filename LIKE '.fuse_hidden%%'", (project_id,))
            # 5. Sincronizar Disco de volta para a Base de Dados
            files_updated = 0
            files_from_disk = await asyncio.to_thread(_ler_ficheiros_do_disco, project_path)
            for rel_path, file_content in files_from_disk:
                await cur.execute(
                    """
                    INSERT INTO project_files (project_id, filename, content)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
                    """,
                    (project_id, rel_path, file_content),
                )
                files_updated += 1
            print(f"[DEBUG PULL] Total de {files_updated} ficheiros gravados na BD.")

            print("[DEBUG PULL] Passo 6: Limpando ficheiros fantasmas (apagados no git)...")
            # Limpar Fantasmas (ficheiros apagados)
            valid_files = []
            for dp, dn, fnames in os.walk(project_path):
                if any(p.startswith('.') for p in dp.split(os.sep)):
                    continue
                for f in fnames:
                    if not f.endswith(IGNORED_EXTS):
                        valid_files.append(os.path.relpath(os.path.join(dp, f), project_path).replace("\\", "/"))

            if valid_files:
                format_strings = ','.join(['%s'] * len(valid_files))
                await cur.execute(
                    f"DELETE FROM project_files WHERE project_id = %s AND filename NOT IN ({format_strings})",
                    (project_id, *valid_files)
                )
            else:
                await cur.execute("DELETE FROM project_files WHERE project_id = %s", (project_id,))

            await cur.execute("UPDATE projects SET updated_at = NOW() WHERE id = %s", (project_id,))
            await cur.connection.commit()

            print("[DEBUG PULL] Passo 7: Matando Cache de RAM e notificando reload...")
            # =========================================================================
            # 6. MATA O CACHE EM RAM PARA PROTEGER A BASE DE DADOS DO FANTASMA
            # =========================================================================
            if project_id in _rooms:
                room = _rooms[project_id]
                print(f"[DEBUG PULL] Apagando cache da _rooms[{project_id}]...")
                
                async with room._lock:
                    for fname, doc in list(room.docs.items()):
                        doc.content = ""
                        doc.history.clear()
                
                # Avisar os clientes
                notify_room(project_id, {"type": "project_reloaded"})
                
                # Destruir a sala
                _rooms.pop(project_id, None)
                print(f"[DEBUG PULL] Sala de collab apagada com sucesso.")
            else:
                print(f"[DEBUG PULL] O projeto não estava carregado em RAM (_rooms).")

            print("[DEBUG PULL] PULL CONCLUÍDO COM SUCESSO!\n")
            return {
                "status": "success",
                "message": f"Pull concluído! {files_updated} ficheiros sincronizados.",
            }

        except Exception as e:
            print(f"[DEBUG PULL] !!! ERRO FATAL NO PULL !!!\n{str(e)}")
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/{project_id}/publish-gitea")
async def publish_to_gitea(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute("SELECT gitea_token FROM users WHERE id = %s", (user["id"],))
    row = await cur.fetchone()
    if not row or not row["gitea_token"]:
        raise HTTPException(status_code=401, detail="Token do Gitea não encontrado.")

    gitea_token = row["gitea_token"]

    await cur.execute(
        """
        SELECT name, gitea_repo FROM projects p WHERE p.id = %s AND (
            p.owner_id = %s OR EXISTS (
                SELECT 1 FROM project_collaborators pc
                WHERE pc.project_id = p.id AND pc.user_id = %s
            )
        )
        """,
        (project_id, user["id"], user["id"]),
    )
    project = await cur.fetchone()
    
    if not project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado ou não tens permissão.")
        
    repo_name = project["gitea_repo"]

    if not repo_name:
        repo_name = re.sub(r"[^a-zA-Z0-9-]", "", project["name"].replace(" ", "-")).lower()
        if not repo_name: repo_name = f"projeto-{project_id}"
        await cur.execute("UPDATE projects SET gitea_repo = %s WHERE id = %s", (repo_name, project_id))

    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GITEA_URL}/api/v1/user/repos",
            headers={"Authorization": f"token {gitea_token}"},
            json={"name": repo_name, "private": True, "description": "Projeto JoshuaTeX"},
        )
        if resp.status_code not in (201, 409):
            raise HTTPException(status_code=400, detail=f"Erro repositório Gitea: {resp.text}")

    async with _git_locks[project_id]:
        # 0. FORÇAR A RAM A IR PARA A BASE DE DADOS IMEDIATAMENTE
        if project_id in _rooms:
            room = _rooms[project_id]
            async with room._lock:
                for fname, doc in list(room.docs.items()):
                    if doc.content:
                        await cur.execute(
                            """
                            INSERT INTO project_files (project_id, filename, content)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
                            """,
                            (project_id, fname, doc.content)
                        )

        project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
        domain = GITEA_URL.replace("https://", "").replace("http://", "")
        username_encoded = urllib.parse.quote(user["username"])
        gitea_token_encoded = urllib.parse.quote(gitea_token)
        remote_url = f"https://oauth2:{gitea_token_encoded}@{domain}/{username_encoded}/{repo_name}.git"

        try:
            await cur.execute("SELECT filename, content FROM project_files WHERE project_id = %s", (project_id,))
            files = await cur.fetchall()
            
            # Substituto da lógica síncrona pela Thread assíncrona!
            await asyncio.to_thread(_escrever_ficheiros_para_compilacao, project_path, files)

            gitignore_path = os.path.join(project_path, ".gitignore")
            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(["*" + ext for ext in IGNORED_EXTS]) + "\n")

            if not os.path.exists(os.path.join(project_path, ".git")):
                await _run_git(["git", "init"], cwd=project_path, check=True)
                await _run_git(["git", "branch", "-M", "main"], cwd=project_path)
                await _run_git(["git", "remote", "add", "origin", remote_url], cwd=project_path, check=True)
            else:
                await _run_git(["git", "remote", "set-url", "origin", remote_url], cwd=project_path, check=True)

            await _run_git(["git", "config", "user.name", "JoshuaTeX System"], cwd=project_path)
            await _run_git(["git", "config", "user.email", "system@joshuatex.local"], cwd=project_path)

            await _run_git(["git", "add", "."], cwd=project_path, check=True)
            time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await _run_git(["git", "commit", "-m", f"Auto-save JoshuaTeX: {time_str}"], cwd=project_path)

            proc = await _run_git(["git", "push", "-u", "origin", "main"], cwd=project_path, capture_output=True, text=True)
            if proc.returncode != 0:
                if "Authentication failed" in proc.stderr:
                    raise Exception("A tua sessão expirou! Faz Logout e entra novamente.")
                raise Exception(f"Falha no Push: {proc.stderr}")

            return {"status": "success", "message": "Guardado com sucesso no Gitea!"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))