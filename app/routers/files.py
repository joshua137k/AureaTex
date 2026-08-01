import base64
import io
import mimetypes
import os
import zipfile
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
from core.config import BASE_PROJETOS_DIR
from core.database import get_db
from core.security import get_current_user
from routers.collab import sync_doc_content, notify_room, rename_doc_in_room
router = APIRouter(prefix="/api/projects", tags=["files"])


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class FileUpdate(BaseModel):
    content: str


class FileCreate(BaseModel):
    filename: str
    content:  str  = ""
    is_main:  bool = False


class FileRename(BaseModel):
    new_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_safe_path(project_id: int, filename: str) -> str:
    """Valida e devolve o caminho absoluto e seguro do ficheiro no disco."""
    # Pasta raiz autorizada para o projeto
    safe_base = os.path.abspath(os.path.join(BASE_PROJETOS_DIR, str(project_id)))
    
    # .lstrip("/") impede que o os.path.join ignore a base se o nome começar por "/"
    clean_filename = filename.lstrip("/")
    file_path = os.path.abspath(os.path.join(safe_base, clean_filename))
    
    # Se, após resolver os "../", o ficheiro tentar sair da pasta base, bloqueia!
    if not file_path.startswith(safe_base):
        raise HTTPException(status_code=400, detail="Tentativa de Path Traversal bloqueada.")
        
    return file_path


def _write_to_disk(file_path: str, content: str) -> None:
    """Escreve conteúdo (texto ou base64) no disco."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if content and content.startswith("data:") and ";base64," in content:
        _, encoded = content.split(";base64,", 1)
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(encoded))
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)


async def _assert_project_owner(project_id: int, user_id: int, cur) -> None:
    await cur.execute(
        """
        SELECT id FROM projects WHERE id = %s AND (
            owner_id = %s
            OR EXISTS (
                SELECT 1 FROM project_collaborators pc
                WHERE pc.project_id = %s AND pc.user_id = %s
            )
        )
        """,
        (project_id, user_id, project_id, user_id),
    )
    if not await cur.fetchone():
        raise HTTPException(
            status_code=404,
            detail="Projeto não encontrado ou sem permissão.",
        )


# ---------------------------------------------------------------------------
# Rotas — ficheiros
# ---------------------------------------------------------------------------

@router.get("/{project_id}/files")
async def list_files(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await cur.execute(
        "SELECT id, filename, is_main, updated_at FROM project_files WHERE project_id = %s ORDER BY is_main DESC, filename",
        (project_id,),
    )
    return await cur.fetchall()


@router.get("/{project_id}/files/{filename:path}")
async def get_file(
    project_id: int,
    filename:   str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await cur.execute(
        "SELECT * FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    f = await cur.fetchone()
    if not f:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")
    return f


# =========================================================================
# A ROTA DE RENAME TEM DE ESTAR AQUI (ANTES DA ROTA DO SAVE)
# =========================================================================
@router.put("/{project_id}/files/{filename:path}/rename")
async def rename_file(
    project_id: int,
    filename: str,
    body: FileRename,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
 
    await cur.execute(
        "SELECT id, is_main FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")
 
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Nome inválido.")
    if new_name == filename:
        return {"status": "unchanged", "old_name": filename, "new_name": new_name}
 
    await cur.execute(
        "SELECT id FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, new_name),
    )
    if await cur.fetchone():
        raise HTTPException(status_code=409, detail="Já existe um ficheiro com esse nome.")
 
    await cur.execute(
        "UPDATE project_files SET filename = %s WHERE project_id = %s AND filename = %s",
        (new_name, project_id, filename),
    )
 
    old_path = _get_safe_path(project_id, filename)
    new_path = _get_safe_path(project_id, new_name)
    if os.path.exists(old_path):
        os.makedirs(os.path.dirname(new_path) or ".", exist_ok=True)
        os.rename(old_path, new_path)
 
    # Sincroniza a sala de colaboração
    await rename_doc_in_room(project_id, filename, new_name) # <--- ADICIONAR ISTO
    
    notify_room(project_id, {
        "type": "file_renamed",
        "old_name": filename,
        "new_name": new_name,
        "is_main": bool(row["is_main"]),
    })
 
    return {"status": "renamed", "old_name": filename, "new_name": new_name}


# =========================================================================
# A ROTA DO SAVE (VEM DEPOIS DO RENAME)
# =========================================================================
@router.put("/{project_id}/files/{filename:path}")
async def save_file(
    project_id: int,
    filename:   str,
    body:       FileUpdate,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)

    await cur.execute("SELECT id FROM project_files WHERE project_id = %s AND filename = %s", (project_id, filename))
    exists = await cur.fetchone()

    await cur.execute(
        """
        INSERT INTO project_files (project_id, filename, content)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
        """,
        (project_id, filename, body.content),
    )
    if not exists:
        notify_room(project_id, {
            "type": "file_added",
            "file": {"name": filename, "is_main": False}
        })

    file_path = _get_safe_path(project_id, filename)
    await asyncio.to_thread(_write_to_disk, file_path, body.content)

    # AVISAR O WEBSOCKET PARA ATUALIZAR A MEMÓRIA
    sync_doc_content(project_id, filename, body.content)

    return {"status": "saved", "filename": filename}


@router.put("/{project_id}/set-main/{filename:path}")
async def set_main_file(
    project_id: int,
    filename:   str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)

    await cur.execute(
        "UPDATE project_files SET is_main = 0 WHERE project_id = %s", (project_id,)
    )
    await cur.execute(
        "UPDATE project_files SET is_main = 1 WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    notify_room(project_id, {
        "type": "file_main_changed",
        "name": filename
    })
    return {"status": "success", "main_file": filename}


@router.delete("/{project_id}/files/{filename:path}", status_code=204)
async def delete_file(
    project_id: int,
    filename:   str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "SELECT is_main FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")
    if row["is_main"]:
        raise HTTPException(
            status_code=400,
            detail="Não podes apagar o ficheiro principal (main.tex).",
        )

    await cur.execute(
        "DELETE FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    disk_path = _get_safe_path(project_id, filename)
    if os.path.exists(disk_path):
        os.remove(disk_path)
    notify_room(project_id, {
        "type": "file_deleted",
        "name": filename
    })


# ---------------------------------------------------------------------------
# Rota — upload ZIP
# ---------------------------------------------------------------------------


def _process_zip_sync(content: bytes, project_path: str):
    results = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        # Prevenção Absoluta contra Ataques de Path Traversal (Zip Slip)
        for member in z.infolist():
            member_path = os.path.abspath(os.path.join(project_path, member.filename))
            if not member_path.startswith(os.path.abspath(project_path)):
                continue # Rejeita ficheiros com '../' maliciosos
            z.extract(member, project_path)

    for root_dir, _, files_in_dir in os.walk(project_path):
        for filename in files_in_dir:
            if filename.startswith(".") or "__MACOSX" in root_dir:
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

@router.post("/upload-zip", status_code=201)
async def upload_zip_project(
    name: str        = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "INSERT INTO projects (owner_id, name, template) VALUES (%s, %s, 'zip')",
        (user["id"], name),
    )
    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id   = (await cur.fetchone())["id"]
    project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
    os.makedirs(project_path, exist_ok=True)

    content = await file.read()
    
    # Passa toda a lógica pesada (Extrair, Ler e converter 64) para uma Thread segura!
    extracted_files = await asyncio.to_thread(_process_zip_sync, content, project_path)

    main_tex_candidate = None
    for rel_path, file_content in extracted_files:
        if rel_path.lower() in ["main.tex", "thesis.tex", "tese.tex"]:
            main_tex_candidate = rel_path

        await cur.execute(
            "INSERT INTO project_files (project_id, filename, content, is_main) VALUES (%s, %s, %s, 0)",
            (project_id, rel_path, file_content),
        )

    if main_tex_candidate:
        await cur.execute(
            "UPDATE project_files SET is_main = 1 WHERE project_id = %s AND filename = %s",
            (project_id, main_tex_candidate),
        )
    else:
        await cur.execute(
            "SELECT filename FROM project_files WHERE project_id = %s AND filename LIKE '%.tex' LIMIT 1",
            (project_id,),
        )
        first_tex = await cur.fetchone()
        if first_tex:
            await cur.execute(
                "UPDATE project_files SET is_main = 1 WHERE project_id = %s AND filename = %s",
                (project_id, first_tex["filename"]),
            )

    return {"id": project_id, "name": name}

@router.get("/{project_id}/download-zip")
async def download_project_zip(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)

    # Buscar nome do projeto
    await cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
    project = await cur.fetchone()

    # Buscar todos os ficheiros
    await cur.execute(
        "SELECT filename, content FROM project_files WHERE project_id = %s",
        (project_id,),
    )
    files = await cur.fetchall()

    # Criar ZIP em memória
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if f["content"] is None:
                continue

            content = f["content"]

            if content.startswith("data:") and ";base64," in content:
                # Ficheiro binário (imagem, etc)
                _, encoded = content.split(";base64,", 1)
                data = base64.b64decode(encoded)
                zf.writestr(f["filename"], data)
            else:
                # Ficheiro de texto
                zf.writestr(f["filename"], content.encode("utf-8"))

    zip_buffer.seek(0)

    # Nome seguro para o ficheiro
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project["name"])

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"'
        },
    )