import secrets
import base64
import json
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from core.config import (
    APP_URL, 
    CLIENT_ID, 
    CLIENT_SECRET, 
    GITEA_URL, 
    REDIRECT_URI,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET
)
from core.database import get_db
from core.security import (
    create_jwt,
    get_password_hash,
    verify_password,
)

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    username: str
    email:    str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@router.post("/api/auth/register")
async def register_user(user_in: UserRegister, cur=Depends(get_db)):
    await cur.execute(
        "SELECT id FROM users WHERE username = %s OR email = %s",
        (user_in.username, user_in.email),
    )
    if await cur.fetchone():
        raise HTTPException(status_code=400, detail="Utilizador ou email já em uso.")

    hashed = get_password_hash(user_in.password)
    await cur.execute(
        "INSERT INTO users (username, email, password_hash, display_name, last_login) VALUES (%s, %s, %s, %s, NOW())",
        (user_in.username, user_in.email, hashed, user_in.username),
    )
    await cur.execute("SELECT LAST_INSERT_ID() as id")
    row = await cur.fetchone()

    token = create_jwt(row["id"], user_in.username)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/api/auth/login")
async def login_user(user_in: UserLogin, cur=Depends(get_db)):
    await cur.execute("SELECT * FROM users WHERE username = %s", (user_in.username,))
    user = await cur.fetchone()

    if not user or not user["password_hash"]:
        raise HTTPException(
            status_code=400,
            detail="Credenciais inválidas ou conta exclusiva Gitea.",
        )

    if not verify_password(user_in.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Credenciais inválidas.")

    await cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],))
    token = create_jwt(user["id"], user["username"])
    return {"access_token": token, "token_type": "bearer"}


@router.get("/login")
def login(token: Optional[str] = None):
    if not CLIENT_ID:
        raise HTTPException(status_code=500, detail="GITEA_CLIENT_ID não configurado.")
    
    state_payload = {"rnd": secrets.token_urlsafe(8)}
    if token:
        state_payload["token"] = token
        
    state_str = base64.urlsafe_b64encode(json.dumps(state_payload).encode()).decode().rstrip("=")
    
    url = (
        f"{GITEA_URL}/login/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&state={state_str}"
    )
    return RedirectResponse(url=url)


@router.get("/auth/callback")
async def auth_callback(code: str, state: str = "", cur=Depends(get_db)):
    logged_in_user_id = None
    if state:
        try:
            state_padded = state + "=" * (-len(state) % 4)
            state_data = json.loads(base64.urlsafe_b64decode(state_padded).decode())
            if "token" in state_data:
                from core.security import decode_jwt
                payload = decode_jwt(state_data["token"])
                logged_in_user_id = int(payload["sub"])
        except Exception:
            pass

    # 2. Obter Access Token do Gitea
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"{GITEA_URL}/login/oauth/access_token",
            data={
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Falha ao obter token do Gitea.")

        access_token = token_resp.json().get("access_token")

        user_resp  = await client.get(
            f"{GITEA_URL}/api/v1/user",
            headers={"Authorization": f"token {access_token}"},
        )
        gitea_user = user_resp.json()

    gitea_id     = gitea_user["id"]
    username     = gitea_user["login"]
    email        = gitea_user.get("email", "")
    display_name = gitea_user.get("full_name") or username
    avatar_url   = gitea_user.get("avatar_url", "")

    if logged_in_user_id:
        await cur.execute("SELECT id FROM users WHERE gitea_id = %s", (gitea_id,))
        existing = await cur.fetchone()
        if existing and existing["id"] != logged_in_user_id:
            raise HTTPException(status_code=400, detail="Esta conta Gitea já está vinculada a outro utilizador!")
            
        await cur.execute(
            "UPDATE users SET gitea_id = %s, gitea_token = %s, avatar_url = %s WHERE id = %s",
            (gitea_id, access_token, avatar_url, logged_in_user_id)
        )
        await cur.execute("SELECT username FROM users WHERE id = %s", (logged_in_user_id,))
        db_username = (await cur.fetchone())["username"]
        user_id = logged_in_user_id
        
    else:
        await cur.execute("SELECT id, username FROM users WHERE gitea_id = %s", (gitea_id,))
        row = await cur.fetchone()
        
        if row:
            # Login Gitea Normal (já estava registado via Gitea)
            user_id = row["id"]
            db_username = row["username"]
            await cur.execute(
                "UPDATE users SET gitea_token = %s, avatar_url = %s, last_login = NOW() WHERE id = %s",
                (access_token, avatar_url, user_id)
            )
        else:
            # Novo ID Gitea. Verifica se o username ou email batem com uma conta manual já existente.
            await cur.execute(
                "SELECT id, username FROM users WHERE username = %s OR (email != '' AND email = %s)",
                (username, email)
            )
            row = await cur.fetchone()
            if row:
                # Merge: Associação automática porque o nickname ou email são idênticos.
                user_id = row["id"]
                db_username = row["username"]
                await cur.execute(
                    "UPDATE users SET gitea_id = %s, gitea_token = %s, avatar_url = %s, last_login = NOW() WHERE id = %s",
                    (gitea_id, access_token, avatar_url, user_id)
                )
            else:
                # Criação Totalmente Nova de Conta via Gitea
                db_username = username
                await cur.execute(
                    """
                    INSERT INTO users (gitea_id, username, email, display_name, avatar_url, gitea_token, last_login)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (gitea_id, username, email, display_name, avatar_url, access_token)
                )
                await cur.execute("SELECT LAST_INSERT_ID() as id")
                user_id = (await cur.fetchone())["id"]

    await cur.connection.commit()

    token = create_jwt(user_id, db_username)
    return RedirectResponse(url=f"{APP_URL}/app/projects?token={token}")







@router.get("/login/github")
def login_github(token: Optional[str] = None):
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID não configurado.")
    
    state_payload = {"rnd": secrets.token_urlsafe(8)}
    if token:
        state_payload["token"] = token
        
    state_str = base64.urlsafe_b64encode(json.dumps(state_payload).encode()).decode().rstrip("=")
    
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={APP_URL}/auth/github/callback"
        f"&scope=user:email,repo"
        f"&state={state_str}"
    )
    return RedirectResponse(url=url)


@router.get("/auth/github/callback")
async def github_callback(code: str, state: str = "", cur=Depends(get_db)):
    logged_in_user_id = None
    if state:
        try:
            state_padded = state + "=" * (-len(state) % 4)
            state_data = json.loads(base64.urlsafe_b64decode(state_padded).decode())
            if "token" in state_data:
                from core.security import decode_jwt
                payload = decode_jwt(state_data["token"])
                logged_in_user_id = int(payload["sub"])
        except Exception:
            pass

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{APP_URL}/auth/github/callback"
            },
            headers={"Accept": "application/json"}
        )
        access_token = token_resp.json().get("access_token")

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        gh_user = user_resp.json()

        email_resp = await client.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        gh_emails = email_resp.json()
        primary_email = next((e["email"] for e in gh_emails if e["primary"]), "")

    gh_id = str(gh_user["id"])
    gh_username = gh_user["login"]
    display_name = gh_user.get("name") or gh_username
    avatar_url = gh_user.get("avatar_url", "")

    if logged_in_user_id:
        await cur.execute(
            "UPDATE users SET github_id=%s, github_username=%s, github_token=%s, avatar_url=%s WHERE id=%s",
            (gh_id, gh_username, access_token, avatar_url, logged_in_user_id)
        )
        await cur.execute("SELECT username FROM users WHERE id = %s", (logged_in_user_id,))
        db_username = (await cur.fetchone())["username"]
        user_id = logged_in_user_id
    else:
        await cur.execute("SELECT id, username FROM users WHERE github_id = %s OR (email != '' AND email = %s)", (gh_id, primary_email))
        row = await cur.fetchone()
        
        if row:
            user_id = row["id"]
            db_username = row["username"]
            await cur.execute(
                "UPDATE users SET github_id=%s, github_username=%s, github_token=%s, avatar_url=%s, last_login=NOW() WHERE id=%s",
                (gh_id, gh_username, access_token, avatar_url, user_id)
            )
        else:
            db_username = gh_username
            await cur.execute(
                """
                INSERT INTO users (github_id, github_username, username, email, display_name, avatar_url, github_token, last_login)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (gh_id, gh_username, gh_username, primary_email, display_name, avatar_url, access_token)
            )
            await cur.execute("SELECT LAST_INSERT_ID() as id")
            user_id = (await cur.fetchone())["id"]

    await cur.connection.commit()
    token = create_jwt(user_id, db_username)
    return RedirectResponse(url=f"{APP_URL}/app/projects?token={token}")