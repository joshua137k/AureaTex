import subprocess
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.database import get_db
from core.security import get_current_user, get_password_hash, verify_password

router = APIRouter(tags=["users"])

class PasswordUpdate(BaseModel):
    old_password: str = ""
    new_password: str

@router.get("/api/me")
async def me(user=Depends(get_current_user)):
    user.pop("gitea_token", None)
    
    # Extrair hashes e tokens por segurança e adicionar flags de estado
    has_pw = bool(user.pop("password_hash", None))
    has_gitea = bool(user.get("gitea_id"))

    user.pop("gitea_token", None)
    user.pop("github_token", None)
    user.pop("zotero_api_key", None)
    
    user["has_password"] = has_pw
    user["has_gitea"] = has_gitea
    user["has_github"] = bool(user.get("github_id"))
    return user


@router.put("/api/users/password")
async def update_password(
    body: PasswordUpdate, 
    user=Depends(get_current_user), 
    cur=Depends(get_db)
):
    # Se o utilizador já tem password definida, exige a password antiga correta
    if user["password_hash"]:
        if not body.old_password:
            raise HTTPException(status_code=400, detail="A password atual é obrigatória.")
        if not verify_password(body.old_password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Password atual incorreta.")

    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="A nova password deve ter pelo menos 6 caracteres.")

    hashed = get_password_hash(body.new_password)
    await cur.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s", 
        (hashed, user["id"])
    )
    return {"status": "success", "message": "Password atualizada com sucesso."}


@router.get("/system-check")
async def system_check(cur=Depends(get_db)):
    results = {}
    try:
        latex = subprocess.check_output(["pdflatex", "--version"]).decode().split("\n")[0]
        results["pdflatex"] = latex
    except Exception as e:
        results["pdflatex"] = f"ERRO: {e}"

    try:
        git = subprocess.check_output(["git", "--version"]).decode().strip()
        results["git"] = git
    except Exception as e:
        results["git"] = f"ERRO: {e}"

    try:
        await cur.execute("SELECT VERSION() as v")
        row = await cur.fetchone()
        results["mariadb"] = row["v"]
    except Exception as e:
        results["mariadb"] = f"ERRO: {e}"

    return results