import asyncio
import base64
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from collections import defaultdict

from core.config import BASE_PROJETOS_DIR
from core.database import get_db
from core.security import get_current_user

router = APIRouter(prefix="/api/projects", tags=["compile"])

# Semáforo: máximo de N compilações simultâneas (evita o servidor morrer)
_compile_semaphore = asyncio.Semaphore(5)
_project_locks = defaultdict(asyncio.Lock) 

async def _run_pdflatex(project_path: str, main_file: str) -> str:
    """Corre pdflatex de forma assíncrona, sem bloquear o event loop."""
    cmd = [
        "pdflatex",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        main_file,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")

async def _run_bibtex(project_path: str, main_file: str) -> str:
    """Corre bibtex para gerar a bibliografia (.bbl) a partir do .aux"""
    base_name = os.path.splitext(main_file)[0]
    cmd = ["bibtex", base_name]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")


def _escrever_ficheiros_para_compilacao(project_path: str, files: list):
    """Escreve os ficheiros num bloco, concebido para correr numa Thread."""
    for f in files:
        if f["content"] is not None:
            file_path = os.path.join(project_path, f["filename"])
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            if f["content"].startswith("data:") and ";base64," in f["content"]:
                import base64
                _, encoded = f["content"].split(";base64,", 1)
                with open(file_path, "wb") as fh:
                    fh.write(base64.b64decode(encoded))
            else:
                with open(file_path, "w", encoding="utf-8") as fh:
                    fh.write(f["content"])

@router.post("/{project_id}/compile")
async def compile_project(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    # 1. Verificar acesso (FICA FORA DO LOCK)
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
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    # 2. ENTRAR NO LOCK DO PROJETO AQUI
    async with _project_locks[project_id]:
        project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
        os.makedirs(project_path, exist_ok=True)

        # Escrever ficheiros no disco
        await cur.execute(
            "SELECT filename, content, is_main FROM project_files WHERE project_id = %s",
            (project_id,),
        )
        files = await cur.fetchall()

        main_file = "main.tex"
        for f in files:
            if f["is_main"]:
                main_file = f["filename"]

            await asyncio.to_thread(_escrever_ficheiros_para_compilacao, project_path, files)

        target_tex = os.path.join(project_path, main_file)
        if not os.path.exists(target_tex):
            raise HTTPException(
                status_code=404,
                detail=f"Ficheiro principal '{main_file}' não encontrado.",
            )

        start = datetime.utcnow()
        log_output = ""

        # =================================================================
        #  PDFLATEX -> BIBTEX -> PDFLATEX
        # =================================================================
        async def _full_compilation_cycle():
            log = ""
            # Passagem 1: Cria o .aux com as referências
            log += await _run_pdflatex(project_path, main_file)
            
            # Passagem 2: BibTeX lê o .aux e puxa do ref.bib
            bib_log = await _run_bibtex(project_path, main_file)
            log += f"\n\n--- BIBTEX LOG ---\n{bib_log}\n"
            
            # Passagem 3: Puxa o .bbl para dentro do documento
            log += await _run_pdflatex(project_path, main_file)
            
            # Passagem 4: Resolve os números das páginas finais
            log += await _run_pdflatex(project_path, main_file)
            return log

        async with _compile_semaphore:
            try:
                log_output = await asyncio.wait_for(
                    _full_compilation_cycle(),
                    timeout=90.0,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=408,
                    detail={"error": "Compilação excedeu o tempo limite (90s).", "log": log_output},
                )

        duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        base_name = os.path.splitext(main_file)[0]
        pdf_path = os.path.join(project_path, f"{base_name}.pdf")
        success = os.path.exists(pdf_path)


        safe_log = log_output[-15000:] if len(log_output) > 15000 else log_output

        await cur.execute(
            "INSERT INTO compile_log (project_id, user_id, success, log_output, duration_ms) VALUES (%s, %s, %s, %s, %s)",
            (project_id, user["id"], int(success), safe_log, duration_ms),
        )

        if success:
            return FileResponse(
                path=pdf_path,
                media_type="application/pdf",
                filename=f"{base_name}.pdf",
            )

        raise HTTPException(
            status_code=400,
            detail={"error": "Erro de compilação LaTeX.", "log": log_output[-3000:]},
        )


@router.get("/{project_id}/compile/log")
async def compile_log(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        """
        SELECT success, duration_ms, compiled_at, LEFT(log_output, 2000) as log_output
        FROM compile_log
        WHERE project_id = %s
        ORDER BY compiled_at DESC
        LIMIT 10
        """,
        (project_id,),
    )
    return await cur.fetchall()