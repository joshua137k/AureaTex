import asyncio
import base64
import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from collections import defaultdict

from core.config import BASE_PROJETOS_DIR
from core.database import get_db
from core.security import get_current_user


try:
    from routers.collab import _rooms, is_project_reloading
except ImportError:
    from routers.collab import _rooms

    def is_project_reloading(project_id: int) -> bool:
        return False


router = APIRouter(prefix="/api/projects", tags=["compile"])


_compile_semaphore = asyncio.Semaphore(5)
_project_locks = defaultdict(asyncio.Lock)


async def _run_pdflatex(project_path: str, main_file: str) -> str:
    """
    Corre pdflatex de forma assíncrona, sem bloquear o event loop.
    """
    cmd = [
        "pdflatex",
        "-synctex=1",
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


async def _run_bibtex(project_path: str, main_file: str) -> str:
    """
    Corre bibtex para gerar a bibliografia (.bbl) a partir do .aux.
    """
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
    """
    Escreve os ficheiros no disco.
    Concebido para correr numa thread separada.
    """
    for f in files:
        if f["content"] is None:
            continue

        file_path = os.path.join(project_path, f["filename"])
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if f["content"].startswith("data:") and ";base64," in f["content"]:
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
):
    from core.database import get_pool
    import aiomysql

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            from routers.files import _assert_project_owner

            await _assert_project_owner(
                project_id,
                user["id"],
                cur,
                require_edit=True,
            )

            async with _project_locks[project_id]:
                # ------------------------------------------------------------
                # Proteção contra pull/reload em curso.
                # ------------------------------------------------------------
                if is_project_reloading(project_id):
                    raise HTTPException(
                        status_code=409,
                        detail="Pull em curso. Compilação bloqueada temporariamente.",
                    )

                # ------------------------------------------------------------
                # Flush do estado colaborativo para a DB.
                # Não fazer flush se a sala estiver congelada por pull.
                # ------------------------------------------------------------
                if project_id in _rooms:
                    room = _rooms[project_id]

                    if (
                        not getattr(room, "reloading", False)
                        and not is_project_reloading(project_id)
                    ):
                        async with room._lock:
                            async with pool.acquire() as conn:
                                async with conn.cursor() as cur:
                                    for fname, doc in room.docs.items():
                                        if not doc.content:
                                            continue

                                        for _ in range(3):
                                            try:
                                                await cur.execute(
                                                    """
                                                    INSERT INTO project_files (
                                                        project_id,
                                                        filename,
                                                        content
                                                    )
                                                    VALUES (%s, %s, %s)
                                                    ON DUPLICATE KEY UPDATE
                                                        content = VALUES(content),
                                                        updated_at = NOW()
                                                    """,
                                                    (project_id, fname, doc.content),
                                                )

                                                await conn.commit()
                                                break

                                            except Exception as e:
                                                await conn.rollback()

                                                if "1020" not in str(e):
                                                    raise

                # ------------------------------------------------------------
                # Buscar ficheiros atuais da DB.
                # ------------------------------------------------------------
                async with pool.acquire() as conn:
                    async with conn.cursor(aiomysql.DictCursor) as cur:
                        await cur.execute(
                            """
                            SELECT filename, content, is_main
                            FROM project_files
                            WHERE project_id = %s
                            """,
                            (project_id,),
                        )

                        files = await cur.fetchall()

                project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))
                os.makedirs(project_path, exist_ok=True)

                main_file = "main.tex"

                for f in files:
                    if f["is_main"]:
                        main_file = f["filename"]

                await asyncio.to_thread(
                    _escrever_ficheiros_para_compilacao,
                    project_path,
                    files,
                )

                target_tex = os.path.join(project_path, main_file)

                if not os.path.exists(target_tex):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Ficheiro principal '{main_file}' não encontrado.",
                    )

                start = datetime.utcnow()
                log_output = ""

                async def _full_compilation_cycle():
                    log = ""

                    log += await _run_pdflatex(project_path, main_file)

                    bib_log = await _run_bibtex(project_path, main_file)
                    log += f"\n--- BIBTEX LOG ---\n{bib_log}\n"

                    log += await _run_pdflatex(project_path, main_file)
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
                            detail={
                                "error": "Compilação excedeu o tempo limite (90s).",
                                "log": log_output,
                            },
                        )

                duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)

                base_name = os.path.splitext(main_file)[0]
                pdf_path = os.path.join(project_path, f"{base_name}.pdf")

                success = os.path.exists(pdf_path)

                safe_log = log_output[-15000:] if len(log_output) > 15000 else log_output

                async with pool.acquire() as conn:
                    async with conn.cursor(aiomysql.DictCursor) as cur:
                        await cur.execute(
                            """
                            INSERT INTO compile_log (
                                project_id,
                                user_id,
                                success,
                                log_output,
                                duration_ms
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                project_id,
                                user["id"],
                                int(success),
                                safe_log,
                                duration_ms,
                            ),
                        )

                        await conn.commit()

                if success:
                    return FileResponse(
                        path=pdf_path,
                        media_type="application/pdf",
                        filename=f"{base_name}.pdf",
                    )

                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "Erro de compilação LaTeX.",
                        "log": log_output[-3000:],
                    },
                )


@router.get("/{project_id}/compile/log")
async def compile_log(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    from routers.files import _assert_project_owner

    await _assert_project_owner(
        project_id,
        user["id"],
        cur,
        require_edit=False,
    )

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


@router.get("/{project_id}/synctex")
async def synctex_inverse_search(
    project_id: int,
    page: int,
    x: float,
    y: float,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    from routers.files import _assert_project_owner

    await _assert_project_owner(
        project_id,
        user["id"],
        cur,
        require_edit=False,
    )

    project_path = os.path.join(BASE_PROJETOS_DIR, str(project_id))

    await cur.execute(
        """
        SELECT filename
        FROM project_files
        WHERE project_id = %s AND is_main = 1
        """,
        (project_id,),
    )

    main_row = await cur.fetchone()

    if not main_row:
        return {
            "file": None,
            "line": None,
            "error": "Ficheiro principal não encontrado.",
        }

    base_name = os.path.splitext(main_row["filename"])[0]
    pdf_file = f"{base_name}.pdf"

    cmd = ["synctex", "edit", "-o", f"{page}:{x}:{y}:{pdf_file}"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")

    file_match = re.search(r"Input:([^\r\n]+)", output)
    line_match = re.search(r"Line:(\d+)", output)

    if file_match and line_match:
        filepath = file_match.group(1).strip()

        if filepath.startswith("./"):
            filepath = filepath[2:]

        if filepath.startswith(project_path):
            filepath = filepath[len(project_path):].lstrip("/")

        return {
            "file": filepath,
            "line": int(line_match.group(1).strip()),
        }

    return {
        "file": None,
        "line": None,
        "error": "Coordenada sem código associado.",
        "raw": output,
    }