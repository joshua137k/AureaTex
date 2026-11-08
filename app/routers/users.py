import asyncio
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.database import get_db
from core.security import (
    get_current_user,
    get_password_hash,
    revoke_sessions,
    validate_new_password,
    verify_password,
)

router = APIRouter(tags=["users"])

# Campos que nunca saem para o cliente.
_PRIVATE_FIELDS = ("password_hash", "gitea_token", "github_token", "zotero_api_key", "tokens_valid_after")


class PasswordUpdate(BaseModel):
    old_password: str = Field(default="", max_length=256)
    new_password: str = Field(max_length=256)


@router.get("/api/me")
async def me(user=Depends(get_current_user)):
    has_pw = bool(user.get("password_hash"))

    public = {k: v for k, v in user.items() if k not in _PRIVATE_FIELDS}

    public["has_password"] = has_pw
    public["has_gitea"] = bool(user.get("gitea_id"))
    public["has_github"] = bool(user.get("github_id"))
    public["has_zotero"] = bool(user.get("zotero_api_key"))
    return public


@router.put("/api/users/password")
async def update_password(
    body: PasswordUpdate,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    # Se o utilizador já tem password definida, exige a password antiga correta
    if user["password_hash"]:
        if not body.old_password:
            raise HTTPException(status_code=400, detail="A password atual é obrigatória.")
        if not await verify_password(body.old_password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Password atual incorreta.")

    validate_new_password(body.new_password)

    hashed = await get_password_hash(body.new_password)
    await cur.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hashed, user["id"])
    )

    # Quem tivesse um token antigo (ex.: roubado) perde o acesso; esta sessão
    # recebe um token novo.
    token = await revoke_sessions(cur, user)

    return {
        "status": "success",
        "message": "Password atualizada. As outras sessões foram terminadas.",
        "access_token": token,
    }


def _version(cmd: list) -> str:
    try:
        out = subprocess.check_output(cmd, timeout=10, stderr=subprocess.STDOUT)
        return out.decode(errors="replace").split("\n")[0].strip()
    except Exception as e:
        return f"ERRO: {type(e).__name__}"


@router.get("/system-check")
async def system_check(user=Depends(get_current_user), cur=Depends(get_db)):
    """Diagnóstico — só para utilizadores autenticados."""
    results = {
        "pdflatex": await asyncio.to_thread(_version, ["pdflatex", "--version"]),
        "git": await asyncio.to_thread(_version, ["git", "--version"]),
    }

    try:
        await cur.execute("SELECT VERSION() as v")
        row = await cur.fetchone()
        results["mariadb"] = row["v"]
    except Exception:
        results["mariadb"] = "ERRO"

    return results
