import asyncio
import io
import os
import shutil
import uuid
import zipfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response

from core.config import BASE_PROJETOS_DIR
from core.security import get_current_user

router = APIRouter(prefix="/api/vscode", tags=["vscode"])

# Semáforo exclusivo para integrações externas (evita que spam de compilações deite o servidor abaixo)
_vscode_compile_semaphore = asyncio.Semaphore(5)

async def _run_pdflatex(project_path: str, main_file: str) -> str:
    """Corre pdflatex de forma assíncrona, sem bloquear o event loop."""
    cmd = [
        "pdflatex",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "--",
        main_file,
    ]
    env = os.environ.copy()
    env["openin_any"] = "p"
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=project_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")


@router.post("/compile")
async def compile_vscode(
    main_file: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """
    Rota efêmera para integrações (VS Code).
    Não usa a base de dados. Apenas descompacta o ZIP, compila e devolve o PDF, limpando tudo a seguir.
    """
    # 1. Cria uma pasta temporária única
    temp_dir = os.path.join(BASE_PROJETOS_DIR, f"vscode_tmp_{user['id']}_{uuid.uuid4().hex[:8]}")
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # 2. Descompacta o ZIP em segurança diretamente para o disco
        zip_content = await file.read()
        with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
            for member in z.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                # Prevenção contra Path Traversal
                if member_path.startswith(os.path.abspath(temp_dir)):
                    z.extract(member, temp_dir)

        target_tex = os.path.join(temp_dir, main_file)
        if not os.path.exists(target_tex):
            raise HTTPException(
                status_code=400, 
                detail={"error": f"Ficheiro principal '{main_file}' não encontrado no ZIP.", "log": ""}
            )

        # 3. Compilação
        log_output = ""
        async with _vscode_compile_semaphore:
            try:
                # Compila 2 vezes para garantir que Índice e Referências ficam corretos
                for _ in range(2):
                    log_output = await asyncio.wait_for(
                        _run_pdflatex(temp_dir, main_file),
                        timeout=60.0,
                    )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=408,
                    detail={"error": "Tempo limite de compilação excedido (60s).", "log": log_output},
                )

        # 4. Ler o PDF gerado
        base_name = os.path.splitext(main_file)[0]
        pdf_path = os.path.join(temp_dir, f"{base_name}.pdf")
        
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            # Devolvemos os bytes do PDF com o cabeçalho correto
            return Response(content=pdf_bytes, media_type="application/pdf")
        
        # 5. Se o PDF não existe, houve erro de LaTeX
        raise HTTPException(
            status_code=400,
            detail={"error": "Erro de compilação LaTeX.", "log": log_output[-3000:]},
        )
        
    finally:
        # 6. LIMPEZA GARANTIDA - Apaga a pasta quer tenha sucesso ou erro
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)