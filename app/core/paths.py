"""
Validação de nomes de ficheiro e caminhos dentro de um projeto.

Todos os nomes vindos do cliente (REST, WebSocket, ZIP, git) passam por
normalize_filename() antes de tocarem na base de dados ou no disco.
"""

import os
import re

from fastapi import HTTPException

from core.config import BASE_PROJETOS_DIR

MAX_FILENAME_LEN = 255

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class InvalidFilename(ValueError):
    pass


def normalize_filename(filename: str) -> str:
    """
    Devolve o nome relativo normalizado (separador '/') ou lança InvalidFilename.

    Rejeita caminhos absolutos, '..', segmentos vazios, a pasta .git e
    caracteres de controlo.
    """
    if not isinstance(filename, str):
        raise InvalidFilename("Nome inválido.")

    name = filename.replace("\\", "/").strip()
    name = re.sub(r"/+", "/", name).lstrip("/")

    if not name or len(name) > MAX_FILENAME_LEN:
        raise InvalidFilename("Nome de ficheiro vazio ou demasiado longo.")

    if _CONTROL_CHARS.search(name):
        raise InvalidFilename("Nome de ficheiro com caracteres inválidos.")

    if name.endswith("/"):
        raise InvalidFilename("Nome de ficheiro inválido.")

    for part in name.split("/"):
        if part in ("", ".", ".."):
            raise InvalidFilename("Caminho inválido.")

        if part.lower() == ".git":
            raise InvalidFilename("Acesso à pasta .git bloqueado.")

    return name


def is_valid_filename(filename: str) -> bool:
    try:
        return normalize_filename(filename) == filename
    except InvalidFilename:
        return False


def clean_filename(filename: str) -> str:
    """Versão para rotas HTTP: converte InvalidFilename em 400."""
    try:
        return normalize_filename(filename)
    except InvalidFilename as e:
        raise HTTPException(status_code=400, detail=str(e))


def project_dir(project_id: int) -> str:
    return os.path.join(BASE_PROJETOS_DIR, str(int(project_id)))


def safe_join(base_dir: str, filename: str) -> str:
    """
    Junta base_dir + filename garantindo que o resultado fica dentro de base_dir,
    mesmo que existam symlinks pelo caminho. Lança InvalidFilename caso contrário.
    """
    name = normalize_filename(filename)
    base = os.path.realpath(base_dir)
    full = os.path.realpath(os.path.join(base, name))

    if os.path.commonpath([base, full]) != base or full == base:
        raise InvalidFilename("Tentativa de Path Traversal bloqueada.")

    return full


def safe_project_path(project_id: int, filename: str) -> str:
    """Caminho absoluto e seguro de um ficheiro de projeto (para rotas HTTP)."""
    try:
        return safe_join(project_dir(project_id), filename)
    except InvalidFilename as e:
        raise HTTPException(status_code=400, detail=str(e))
