"""
Cópia em disco dos ficheiros de projeto.

A base de dados é a fonte de verdade; o disco (/projetos/<id>) é a área de
trabalho do pdflatex e do git. Binários são guardados na BD como data-URI
base64 e descodificados ao escrever no disco.
"""

import asyncio
import base64
import binascii
import io
import mimetypes
import os
import stat
import zipfile

from core.config import MAX_FILE_CHARS, MAX_ZIP_ENTRIES, MAX_ZIP_UNPACKED
from core.paths import InvalidFilename, normalize_filename, project_dir, safe_join

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

# Maior ficheiro (em bytes) que se importa do disco/ZIP para a BD.
MAX_IMPORT_BYTES = (MAX_FILE_CHARS * 3) // 4 - 1024


class ArchiveError(ValueError):
    pass


def is_binary_content(content) -> bool:
    return bool(content) and content.startswith("data:") and ";base64," in content[:200]


def content_to_bytes(content: str) -> bytes:
    if is_binary_content(content):
        _, encoded = content.split(";base64,", 1)

        try:
            return base64.b64decode(encoded)
        except (binascii.Error, ValueError):
            raise InvalidFilename("Conteúdo base64 inválido.")

    return (content or "").encode("utf-8")


def bytes_to_content(data: bytes, filename: str) -> str:
    """Texto UTF-8 fica como está; o resto vira data-URI base64."""
    try:
        text = data.decode("utf-8")
        if "\x00" not in text:
            return text
    except UnicodeDecodeError:
        pass

    mime_type, _ = mimetypes.guess_type(filename)
    mime_type = mime_type or "application/octet-stream"

    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def write_file(project_id: int, filename: str, content: str) -> None:
    """Escreve um ficheiro do projeto no disco (bloqueante — usar to_thread)."""
    path = safe_join(project_dir(project_id), filename)
    data = content_to_bytes(content)

    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Nunca escrever através de um symlink deixado no disco.
    if os.path.islink(path):
        os.unlink(path)

    with open(path, "wb") as fh:
        fh.write(data)


def remove_file(project_id: int, filename: str) -> None:
    try:
        path = safe_join(project_dir(project_id), filename)
    except InvalidFilename:
        return

    if os.path.isfile(path) or os.path.islink(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# Sincronização BD → disco
# ---------------------------------------------------------------------------

# project_id → {filename: md5 do conteúdo que está no disco}
# É só uma otimização para não reescrever (e descodificar de base64) dezenas de
# imagens em cada compilação. A chave é o hash do conteúdo na BD, por isso
# nunca serve conteúdo antigo: se o hash mudar, o ficheiro é reescrito.
_synced: dict[int, dict[str, str]] = {}


def forget_project(project_id: int) -> None:
    _synced.pop(project_id, None)


async def sync_project_to_disk(project_id: int, cur) -> list[dict]:
    """
    Garante que o disco tem o conteúdo atual da BD.
    Devolve a lista de ficheiros [{filename, is_main}].
    """
    await cur.execute(
        """
        SELECT filename, is_main, MD5(content) AS h
        FROM project_files
        WHERE project_id = %s
        """,
        (project_id,),
    )
    rows = await cur.fetchall()

    base = project_dir(project_id)
    await asyncio.to_thread(os.makedirs, base, exist_ok=True)

    cache = _synced.setdefault(project_id, {})
    names = set()

    for row in rows:
        filename = row["filename"]
        names.add(filename)

        try:
            path = safe_join(base, filename)
        except InvalidFilename:
            continue

        if row["h"] is None:
            continue

        if cache.get(filename) == row["h"] and os.path.isfile(path) and not os.path.islink(path):
            continue

        await cur.execute(
            "SELECT content FROM project_files WHERE project_id = %s AND filename = %s",
            (project_id, filename),
        )
        content_row = await cur.fetchone()

        if not content_row or content_row["content"] is None:
            continue

        try:
            await asyncio.to_thread(write_file, project_id, filename, content_row["content"])
            cache[filename] = row["h"]
        except (InvalidFilename, OSError) as e:
            print(f"[storage] Não foi possível escrever {project_id}/{filename}: {e}")

    for stale in set(cache) - names:
        cache.pop(stale, None)

    return [{"filename": r["filename"], "is_main": r["is_main"]} for r in rows]


# ---------------------------------------------------------------------------
# Disco → BD (depois de git pull ou de extrair um ZIP)
# ---------------------------------------------------------------------------

def read_project_tree(project_path: str) -> list[tuple[str, str]]:
    """
    Lê os ficheiros de um projeto no disco. Ignora pastas escondidas (.git),
    ficheiros auxiliares do LaTeX, symlinks e ficheiros demasiado grandes.
    """
    results = []

    for root_dir, dirs, filenames in os.walk(project_path, followlinks=False):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d != "__MACOSX" and not os.path.islink(os.path.join(root_dir, d))
        ]

        for filename in filenames:
            if filename.endswith(IGNORED_EXTS) or filename.startswith(".fuse_hidden"):
                continue

            if filename in (".DS_Store", "Thumbs.db"):
                continue

            filepath = os.path.join(root_dir, filename)

            try:
                st = os.lstat(filepath)
            except OSError:
                continue

            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_IMPORT_BYTES:
                continue

            rel_path = os.path.relpath(filepath, project_path).replace("\\", "/")

            try:
                rel_path = normalize_filename(rel_path)
            except InvalidFilename:
                continue

            with open(filepath, "rb") as fh:
                data = fh.read()

            results.append((rel_path, bytes_to_content(data, filename)))

    return results


def extract_zip(content: bytes, dest_dir: str) -> None:
    """
    Extrai um ZIP para dest_dir de forma segura: sem path traversal, sem
    pastas .git, sem symlinks, e com limites de tamanho (zip bombs).
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ArchiveError("O ficheiro não é um ZIP válido.")

    with archive as z:
        members = [m for m in z.infolist() if not m.is_dir()]

        if len(members) > MAX_ZIP_ENTRIES:
            raise ArchiveError("O ZIP tem demasiados ficheiros.")

        if sum(m.file_size for m in members) > MAX_ZIP_UNPACKED:
            raise ArchiveError("O ZIP é demasiado grande depois de descomprimido.")

        os.makedirs(dest_dir, exist_ok=True)
        written = 0

        for member in members:
            # Symlinks dentro do ZIP (modo Unix nos 16 bits altos).
            if stat.S_ISLNK(member.external_attr >> 16):
                continue

            if "__MACOSX" in member.filename.split("/"):
                continue

            try:
                target = safe_join(dest_dir, member.filename)
            except InvalidFilename:
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)

            with z.open(member) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break

                    written += len(chunk)
                    if written > MAX_ZIP_UNPACKED:
                        raise ArchiveError("O ZIP é demasiado grande depois de descomprimido.")

                    dst.write(chunk)
