import asyncio
import io
import os
import re
import shutil
import zipfile

import pymysql
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.config import MAX_FILE_CHARS, MAX_ZIP_BYTES
from core.database import get_db
from core.paths import clean_filename, project_dir, safe_project_path
from core.security import get_current_user
from core.storage import (
    ArchiveError,
    content_to_bytes,
    extract_zip,
    read_project_tree,
    remove_file,
    write_file,
)

from routers.collab import (
    drop_doc,
    flush_project,
    is_project_reloading,
    notify_room,
    record_history,
    rename_doc_in_room,
    sync_doc_content,
)


router = APIRouter(prefix="/api/projects", tags=["files"])

PROVIDERS = ("gitea", "github")


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class FileUpdate(BaseModel):
    content: str = Field(max_length=MAX_FILE_CHARS)


class FileRename(BaseModel):
    new_name: str = Field(max_length=255)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def validate_project_name(name: str) -> str:
    name = (name or "").strip()

    if not name or len(name) > 255 or re.search(r"[\x00-\x1f]", name):
        raise HTTPException(status_code=400, detail="Nome de projeto inválido.")

    return name


async def _assert_project_owner(
    project_id: int,
    user_id: int,
    cur,
    require_edit: bool = True,
) -> None:
    """
    Valida se o utilizador tem acesso ao projeto e se tem permissão de edição
    (se exigido).
    """
    role_filter = "AND pc.role = 'editor'" if require_edit else ""

    await cur.execute(
        f"""
        SELECT id
        FROM projects
        WHERE id = %s
          AND (
                owner_id = %s
                OR EXISTS (
                    SELECT 1
                    FROM project_collaborators pc
                    WHERE pc.project_id = %s
                      AND pc.user_id = %s
                      {role_filter}
                )
          )
        """,
        (project_id, user_id, project_id, user_id),
    )

    if not await cur.fetchone():
        raise HTTPException(
            status_code=403,
            detail="Projeto não encontrado ou sem permissão.",
        )


async def _assert_pull_not_running(project_id: int) -> None:
    """
    Impede alterações em ficheiros enquanto existe um pull/reload a correr.
    """
    if is_project_reloading(project_id):
        raise HTTPException(
            status_code=409,
            detail="Pull em curso. Aguarda a atualização do projeto.",
        )


async def _touch_project(project_id: int, cur) -> None:
    await cur.execute("UPDATE projects SET updated_at = NOW() WHERE id = %s", (project_id,))


# ---------------------------------------------------------------------------
# Rotas — ficheiros
# ---------------------------------------------------------------------------
@router.put("/{project_id}/files/{filename:path}/rename")
async def rename_file(
    project_id: int,
    filename: str,
    body: FileRename,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await _assert_pull_not_running(project_id)

    filename = clean_filename(filename)
    new_name = clean_filename(body.new_name)

    await cur.execute(
        "SELECT id, is_main FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")

    if new_name == filename:
        return {
            "status": "unchanged",
            "old_name": filename,
            "new_name": new_name,
        }

    old_path = safe_project_path(project_id, filename)
    new_path = safe_project_path(project_id, new_name)

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
    await cur.execute(
        "UPDATE project_file_history SET filename = %s WHERE project_id = %s AND filename = %s",
        (new_name, project_id, filename),
    )
    await _touch_project(project_id, cur)

    if os.path.isfile(old_path):
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.replace(old_path, new_path)

    await rename_doc_in_room(project_id, filename, new_name)

    notify_room(project_id, {
        "type": "file_renamed",
        "old_name": filename,
        "new_name": new_name,
        "is_main": bool(row["is_main"]),
    })

    return {
        "status": "renamed",
        "old_name": filename,
        "new_name": new_name,
    }


@router.put("/{project_id}/files/{filename:path}")
async def save_file(
    project_id: int,
    filename: str,
    body: FileUpdate,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await _assert_pull_not_running(project_id)

    # Validar ANTES de tocar na BD ou no disco.
    filename = clean_filename(filename)
    safe_project_path(project_id, filename)

    try:
        content_to_bytes(body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await cur.execute(
        "SELECT id FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    exists = await cur.fetchone()

    await cur.execute(
        """
        INSERT INTO project_files (project_id, filename, content)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
        """,
        (project_id, filename, body.content),
    )
    await record_history(cur, project_id, filename, body.content, user["id"])
    await _touch_project(project_id, cur)

    await asyncio.to_thread(write_file, project_id, filename, body.content)

    if not exists:
        notify_room(project_id, {
            "type": "file_added",
            "file": {
                "name": filename,
                "is_main": False,
            },
        })

    # Quem tiver este ficheiro aberto no browser recebe o conteúdo novo, e a
    # cópia em memória do collab deixa de poder sobrepor-se a este save.
    await sync_doc_content(project_id, filename, body.content)

    return {
        "status": "saved",
        "filename": filename,
    }


@router.put("/{project_id}/set-main/{filename:path}")
async def set_main_file(
    project_id: int,
    filename: str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await _assert_pull_not_running(project_id)

    filename = clean_filename(filename)

    await cur.execute(
        "SELECT id FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )

    # Antes, um nome inexistente deixava o projeto sem ficheiro principal.
    if not await cur.fetchone():
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")

    await cur.execute(
        "UPDATE project_files SET is_main = (filename = %s) WHERE project_id = %s",
        (filename, project_id),
    )

    notify_room(project_id, {
        "type": "file_main_changed",
        "name": filename,
    })

    return {
        "status": "success",
        "main_file": filename,
    }


@router.delete("/{project_id}/files/{filename:path}", status_code=204)
async def delete_file(
    project_id: int,
    filename: str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await _assert_pull_not_running(project_id)

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

    # Primeiro tira-se da memória do collab, para nenhum autosave o recriar.
    await drop_doc(project_id, filename)

    await cur.execute(
        "DELETE FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )
    await _touch_project(project_id, cur)

    await asyncio.to_thread(remove_file, project_id, filename)

    notify_room(project_id, {
        "type": "file_deleted",
        "name": filename,
    })


@router.get("/{project_id}/files")
async def list_files(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

    await cur.execute(
        """
        SELECT id, filename, is_main, updated_at
        FROM project_files
        WHERE project_id = %s
        ORDER BY is_main DESC, filename
        """,
        (project_id,),
    )

    return await cur.fetchall()


@router.get("/{project_id}/files/{filename:path}/history")
async def get_file_history(
    project_id: int,
    filename: str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)

    await cur.execute(
        """
        SELECT h.id, h.saved_at, u.display_name, u.username
        FROM project_file_history h
        JOIN users u ON h.user_id = u.id
        WHERE h.project_id = %s AND h.filename = %s
        ORDER BY h.saved_at DESC
        LIMIT 40
        """,
        (project_id, filename),
    )

    return await cur.fetchall()


@router.get("/{project_id}/history/{history_id}")
async def get_history_content(
    project_id: int,
    history_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)

    await cur.execute(
        "SELECT content FROM project_file_history WHERE id = %s AND project_id = %s",
        (history_id, project_id),
    )

    row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Histórico não encontrado.")

    return row


@router.post("/{project_id}/files/{filename:path}/restore/{history_id}")
async def restore_file_history(
    project_id: int,
    filename: str,
    history_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur)
    await _assert_pull_not_running(project_id)

    filename = clean_filename(filename)

    await cur.execute(
        """
        SELECT content
        FROM project_file_history
        WHERE id = %s AND project_id = %s AND filename = %s
        """,
        (history_id, project_id, filename),
    )

    row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Histórico não encontrado.")

    content = row["content"] or ""

    # Guarda primeiro o que ainda estiver só em memória, para a versão atual
    # ficar no histórico antes de ser substituída.
    await flush_project(project_id)

    await cur.execute(
        """
        UPDATE project_files
        SET content = %s, updated_at = NOW()
        WHERE project_id = %s AND filename = %s
        """,
        (content, project_id, filename),
    )

    if cur.rowcount == 0:
        await cur.execute(
            "SELECT id FROM project_files WHERE project_id = %s AND filename = %s",
            (project_id, filename),
        )
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")

    await cur.execute(
        """
        INSERT INTO project_file_history (project_id, filename, user_id, content, saved_at)
        VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())
        """,
        (project_id, filename, user["id"], content),
    )
    await _touch_project(project_id, cur)

    await asyncio.to_thread(write_file, project_id, filename, content)

    await sync_doc_content(project_id, filename, content)

    return {"status": "success"}


@router.get("/{project_id}/files/{filename:path}")
async def get_file(
    project_id: int,
    filename: str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

    # Inclui o que ainda só estiver na memória do collab (autosave pendente).
    await flush_project(project_id)

    await cur.execute(
        "SELECT * FROM project_files WHERE project_id = %s AND filename = %s",
        (project_id, filename),
    )

    f = await cur.fetchone()

    if not f:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")

    return f


@router.post("/upload-zip", status_code=201)
async def upload_zip_project(
    name: str = Form(...),
    remote_provider: str = Form("gitea"),
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    name = validate_project_name(name)

    if remote_provider not in PROVIDERS:
        remote_provider = "gitea"

    content = await file.read(MAX_ZIP_BYTES + 1)

    if len(content) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="O ZIP é demasiado grande (máx. 50 MB).")

    try:
        await cur.execute(
            """
            INSERT INTO projects (owner_id, name, template, remote_provider)
            VALUES (%s, %s, 'zip', %s)
            """,
            (user["id"], name, remote_provider),
        )
    except pymysql.err.IntegrityError:
        raise HTTPException(status_code=409, detail="Já tens um projeto com esse nome.")

    await cur.execute("SELECT LAST_INSERT_ID() as id")
    project_id = (await cur.fetchone())["id"]

    project_path = project_dir(project_id)

    try:
        # Pasta nova e limpa — nunca reaproveitar restos de um projeto antigo.
        await asyncio.to_thread(shutil.rmtree, project_path, True)
        await asyncio.to_thread(extract_zip, content, project_path)
        extracted_files = await asyncio.to_thread(read_project_tree, project_path)

        if not extracted_files:
            raise ArchiveError("O ZIP não contém ficheiros utilizáveis.")

    except ArchiveError as e:
        await asyncio.to_thread(shutil.rmtree, project_path, True)
        raise HTTPException(status_code=400, detail=str(e))

    except Exception:
        await asyncio.to_thread(shutil.rmtree, project_path, True)
        raise

    for rel_path, file_content in extracted_files:
        await cur.execute(
            """
            INSERT INTO project_files (project_id, filename, content, is_main)
            VALUES (%s, %s, %s, 0)
            """,
            (project_id, rel_path, file_content),
        )

    # Ficheiro principal: main/thesis/tese.tex mais perto da raiz; senão o
    # primeiro .tex com \documentclass; senão o primeiro .tex.
    tex_files = sorted(
        (p for p, _ in extracted_files if p.lower().endswith(".tex")),
        key=lambda p: (p.count("/"), p.lower()),
    )
    by_name = [p for p in tex_files if p.split("/")[-1].lower() in ("main.tex", "thesis.tex", "tese.tex")]
    contents = dict(extracted_files)
    with_class = [p for p in tex_files if "\\documentclass" in contents[p]]

    main_candidate = (by_name or with_class or tex_files or [None])[0]

    if main_candidate:
        await cur.execute(
            "UPDATE project_files SET is_main = 1 WHERE project_id = %s AND filename = %s",
            (project_id, main_candidate),
        )

    return {
        "id": project_id,
        "name": name,
    }


@router.get("/{project_id}/download-zip")
async def download_project_zip(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

    await flush_project(project_id)

    await cur.execute(
        "SELECT name FROM projects WHERE id = %s",
        (project_id,),
    )
    project = await cur.fetchone()

    await cur.execute(
        "SELECT filename, content FROM project_files WHERE project_id = %s",
        (project_id,),
    )
    files = await cur.fetchall()

    def build_zip() -> io.BytesIO:
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                if f["content"] is None:
                    continue

                try:
                    zf.writestr(f["filename"], content_to_bytes(f["content"]))
                except ValueError:
                    continue

        zip_buffer.seek(0)
        return zip_buffer

    zip_buffer = await asyncio.to_thread(build_zip)

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project["name"]) or "project"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"'
        },
    )
