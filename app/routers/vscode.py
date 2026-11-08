import asyncio
import os
import shutil
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from core.config import BASE_PROJETOS_DIR, COMPILE_TIMEOUT_SECS, MAX_ZIP_BYTES
from core.latex import CompileTimeout, compile_latex, output_pdf_name
from core.paths import InvalidFilename, safe_join
from core.security import get_current_user
from core.storage import ArchiveError, extract_zip

from routers.compile import _compile_semaphore

router = APIRouter(prefix="/api/vscode", tags=["vscode"])

# Uma compilação efémera de cada vez por utilizador.
_user_locks = defaultdict(asyncio.Lock)


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
    zip_content = await file.read(MAX_ZIP_BYTES + 1)

    if len(zip_content) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail={"error": "O ZIP é demasiado grande (máx. 50 MB).", "log": ""})

    temp_dir = os.path.join(BASE_PROJETOS_DIR, f"vscode_tmp_{user['id']}_{uuid.uuid4().hex[:12]}")

    async with _user_locks[user["id"]]:
        try:
            try:
                await asyncio.to_thread(extract_zip, zip_content, temp_dir)
                target_tex = safe_join(temp_dir, main_file)
            except (ArchiveError, InvalidFilename) as e:
                raise HTTPException(status_code=400, detail={"error": str(e), "log": ""})

            rel_main = os.path.relpath(target_tex, os.path.realpath(temp_dir)).replace("\\", "/")

            if rel_main.startswith("-") or not os.path.isfile(target_tex):
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"Ficheiro principal '{main_file}' não encontrado no ZIP.", "log": ""},
                )

            async with _compile_semaphore:
                try:
                    log_output, success = await compile_latex(temp_dir, rel_main, synctex=False)
                except CompileTimeout as e:
                    raise HTTPException(
                        status_code=408,
                        detail={
                            "error": f"Tempo limite de compilação excedido ({COMPILE_TIMEOUT_SECS}s).",
                            "log": e.log[-20000:],
                        },
                    )

            if success:
                with open(os.path.join(temp_dir, output_pdf_name(rel_main)), "rb") as f:
                    pdf_bytes = f.read()

                return Response(content=pdf_bytes, media_type="application/pdf")

            raise HTTPException(
                status_code=400,
                detail={"error": "Erro de compilação LaTeX.", "log": log_output[-20000:]},
            )

        finally:
            # Apaga a pasta quer tenha sucesso ou erro
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)
