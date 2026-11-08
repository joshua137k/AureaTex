import re
import secrets
import urllib.parse
from typing import Optional

import httpx
import pymysql
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from core.config import (
    APP_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    GITEA_URL,
    REDIRECT_URI,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
)
from core.database import get_db
from core.security import (
    RateLimiter,
    client_ip,
    create_jwt,
    create_state_token,
    decode_state_token,
    get_current_user,
    get_password_hash,
    revoke_sessions,
    validate_new_password,
    verify_password,
)

router = APIRouter(tags=["auth"])

GITHUB_REDIRECT_URI = f"{APP_URL}/auth/github/callback"
STATE_COOKIE = "oauth_nonce"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,38}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Falhas de login: 20 por 10 minutos, por IP.
_login_failures = RateLimiter(limit=20, window=600)
# Contas novas: 10 por hora por IP.
_registrations = RateLimiter(limit=10, window=3600)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    username: str = Field(max_length=64)
    email:    str = Field(max_length=255)
    password: str = Field(max_length=256)


class UserLogin(BaseModel):
    username: str = Field(max_length=255)
    password: str = Field(max_length=256)


# ---------------------------------------------------------------------------
# Login local
# ---------------------------------------------------------------------------

@router.post("/api/auth/register")
async def register_user(user_in: UserRegister, request: Request, cur=Depends(get_db)):
    ip_key = f"ip:{client_ip(request)}"
    _registrations.check(ip_key, "Demasiadas contas criadas a partir deste endereço. Tenta mais tarde.")

    username = user_in.username.strip()
    email = user_in.email.strip().lower()

    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username inválido: usa 3 a 39 letras, números, '.', '-' ou '_'.",
        )

    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Email inválido.")

    validate_new_password(user_in.password)

    await cur.execute(
        "SELECT id FROM users WHERE username = %s OR email = %s",
        (username, email),
    )
    if await cur.fetchone():
        raise HTTPException(status_code=400, detail="Utilizador ou email já em uso.")

    hashed = await get_password_hash(user_in.password)

    try:
        await cur.execute(
            "INSERT INTO users (username, email, password_hash, display_name, last_login) VALUES (%s, %s, %s, %s, NOW())",
            (username, email, hashed, username),
        )
    except pymysql.err.IntegrityError:
        raise HTTPException(status_code=400, detail="Utilizador ou email já em uso.")

    await cur.execute("SELECT LAST_INSERT_ID() as id")
    row = await cur.fetchone()

    _registrations.hit(ip_key)

    token = create_jwt(row["id"], username)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/api/auth/login")
async def login_user(user_in: UserLogin, request: Request, cur=Depends(get_db)):
    username = user_in.username.strip()

    # Só as falhas contam (o plugin do VS Code faz login em cada sincronização).
    # A chave é o IP e não a conta, para ninguém conseguir bloquear a conta de
    # outra pessoa falhando de propósito.
    ip_key = f"ip:{client_ip(request)}"
    _login_failures.check(ip_key, "Demasiadas tentativas falhadas. Tenta de novo daqui a uns minutos.")

    await cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = await cur.fetchone()

    # verify_password corre sempre o bcrypt (mesmo sem utilizador), para o
    # tempo de resposta não revelar que usernames existem.
    ok = await verify_password(user_in.password, (user or {}).get("password_hash") or "")

    if not ok or not user:
        _login_failures.hit(ip_key)
        raise HTTPException(status_code=400, detail="Credenciais inválidas.")

    await cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],))
    token = create_jwt(user["id"], user["username"])
    return {"access_token": token, "token_type": "bearer"}


@router.post("/api/auth/logout-all")
async def logout_everywhere(user=Depends(get_current_user), cur=Depends(get_db)):
    """Invalida todos os tokens já emitidos e devolve um novo para esta sessão."""
    token = await revoke_sessions(cur, user)
    return {"access_token": token, "token_type": "bearer"}


# ---------------------------------------------------------------------------
# OAuth — helpers
# ---------------------------------------------------------------------------

def _authorize_url(provider: str, state: str) -> str:
    if provider == "github":
        if not GITHUB_CLIENT_ID:
            raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID não configurado.")

        params = {
            "client_id": GITHUB_CLIENT_ID,
            "redirect_uri": GITHUB_REDIRECT_URI,
            "scope": "user:email repo",
            "state": state,
        }
        return "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)

    if not CLIENT_ID:
        raise HTTPException(status_code=500, detail="GITEA_CLIENT_ID não configurado.")

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": state,
    }
    return f"{GITEA_URL}/login/oauth/authorize?" + urllib.parse.urlencode(params)


def _start_oauth(provider: str, link_user_id: Optional[int] = None) -> tuple[str, str]:
    """
    Devolve (url, nonce). O `state` é um token assinado e de curta duração; o
    nonce fica também num cookie para o callback confirmar que o pedido
    começou neste browser (proteção CSRF). O JWT do utilizador nunca é
    enviado ao fornecedor.
    """
    nonce = secrets.token_urlsafe(24)
    data = {"provider": provider, "n": nonce}

    if link_user_id:
        data["link"] = link_user_id

    return _authorize_url(provider, create_state_token(data)), nonce


def _set_state_cookie(response, nonce: str) -> None:
    response.set_cookie(
        STATE_COOKIE,
        nonce,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=APP_URL.startswith("https://"),
        path="/auth",
    )


def _check_state(state: str, cookie_nonce: Optional[str], provider: str) -> Optional[int]:
    """Valida o state e devolve o id do utilizador a vincular (ou None)."""
    data = decode_state_token(state)

    if data.get("provider") != provider:
        raise HTTPException(status_code=400, detail="Pedido de login inválido.")

    if not cookie_nonce or not secrets.compare_digest(cookie_nonce, str(data.get("n", ""))):
        raise HTTPException(
            status_code=400,
            detail="Pedido de login inválido ou expirado. Volta à página de login e tenta de novo.",
        )

    link = data.get("link")
    return int(link) if link else None


def _login_error(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{APP_URL}/app/login#error={urllib.parse.quote(message)}")


def _finish(user_id: int, username: str) -> RedirectResponse:
    # O token vai no fragmento (#), que não é enviado a servidores nem fica em logs.
    token = create_jwt(user_id, username)
    response = RedirectResponse(url=f"{APP_URL}/app/projects#token={token}")
    response.delete_cookie(STATE_COOKIE, path="/auth")
    return response


async def _unique_username(cur, wanted: str) -> Optional[str]:
    """Devolve `wanted` se estiver livre; None se já existir."""
    await cur.execute("SELECT id FROM users WHERE username = %s", (wanted,))
    return None if await cur.fetchone() else wanted


_USERNAME_TAKEN = (
    "Já existe uma conta local com o username '{name}'. Entra com a tua password "
    "e liga a conta {provider} em Account Settings."
)


# ---------------------------------------------------------------------------
# OAuth — início
# ---------------------------------------------------------------------------

@router.get("/login")
def login():
    url, nonce = _start_oauth("gitea")
    response = RedirectResponse(url=url)
    _set_state_cookie(response, nonce)
    return response


@router.get("/login/github")
def login_github():
    url, nonce = _start_oauth("github")
    response = RedirectResponse(url=url)
    _set_state_cookie(response, nonce)
    return response


@router.post("/api/auth/link/{provider}")
async def start_link(provider: str, request: Request, user=Depends(get_current_user)):
    """
    Início do fluxo para vincular Gitea/GitHub a uma conta já autenticada.
    O frontend chama isto com o Bearer token e depois navega para o URL.
    """
    if provider not in ("gitea", "github"):
        raise HTTPException(status_code=404, detail="Fornecedor desconhecido.")

    url, nonce = _start_oauth(provider, link_user_id=user["id"])
    response = JSONResponse({"url": url})
    _set_state_cookie(response, nonce)
    return response


# ---------------------------------------------------------------------------
# OAuth — Gitea
# ---------------------------------------------------------------------------

@router.get("/auth/callback")
async def auth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    oauth_nonce: Optional[str] = Cookie(default=None),
    cur=Depends(get_db),
):
    if error or not code:
        return _login_error("Login com Gitea cancelado.")

    try:
        logged_in_user_id = _check_state(state, oauth_nonce, "gitea")
    except HTTPException as e:
        return _login_error(str(e.detail))

    async with httpx.AsyncClient(timeout=20) as client:
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

        access_token = token_resp.json().get("access_token") if token_resp.status_code == 200 else None

        if not access_token:
            return _login_error("Falha ao obter token do Gitea.")

        user_resp = await client.get(
            f"{GITEA_URL}/api/v1/user",
            headers={"Authorization": f"token {access_token}"},
        )

        if user_resp.status_code != 200:
            return _login_error("Falha ao ler o perfil do Gitea.")

        gitea_user = user_resp.json()

    gitea_id     = gitea_user["id"]
    username     = gitea_user["login"]
    email        = (gitea_user.get("email") or "").lower()
    display_name = gitea_user.get("full_name") or username
    avatar_url   = gitea_user.get("avatar_url", "")

    await cur.execute("SELECT id, username FROM users WHERE gitea_id = %s", (gitea_id,))
    existing = await cur.fetchone()

    if logged_in_user_id:
        # Vincular a uma conta já autenticada.
        if existing and existing["id"] != logged_in_user_id:
            return _login_error("Esta conta Gitea já está vinculada a outro utilizador.")

        await cur.execute(
            "UPDATE users SET gitea_id = %s, gitea_token = %s, avatar_url = %s WHERE id = %s",
            (gitea_id, access_token, avatar_url, logged_in_user_id),
        )
        await cur.execute("SELECT username FROM users WHERE id = %s", (logged_in_user_id,))
        row = await cur.fetchone()

        if not row:
            return _login_error("Utilizador não encontrado.")

        return _finish(logged_in_user_id, row["username"])

    if existing:
        await cur.execute(
            "UPDATE users SET gitea_token = %s, avatar_url = %s, last_login = NOW() WHERE id = %s",
            (access_token, avatar_url, existing["id"]),
        )
        return _finish(existing["id"], existing["username"])

    # Conta nova. NÃO se junta automaticamente a uma conta local com o mesmo
    # username/email: quem a criou podia não ser o dono desta conta Gitea.
    if not await _unique_username(cur, username):
        return _login_error(_USERNAME_TAKEN.format(name=username, provider="Gitea"))

    await cur.execute(
        """
        INSERT INTO users (gitea_id, username, email, display_name, avatar_url, gitea_token, last_login)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """,
        (gitea_id, username, email, display_name, avatar_url, access_token),
    )
    await cur.execute("SELECT LAST_INSERT_ID() as id")
    user_id = (await cur.fetchone())["id"]

    return _finish(user_id, username)


# ---------------------------------------------------------------------------
# OAuth — GitHub
# ---------------------------------------------------------------------------

@router.get("/auth/github/callback")
async def github_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    oauth_nonce: Optional[str] = Cookie(default=None),
    cur=Depends(get_db),
):
    if error or not code:
        return _login_error("Login com GitHub cancelado.")

    try:
        logged_in_user_id = _check_state(state, oauth_nonce, "github")
    except HTTPException as e:
        return _login_error(str(e.detail))

    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )

        access_token = token_resp.json().get("access_token") if token_resp.status_code == 200 else None

        if not access_token:
            return _login_error("Falha ao obter token do GitHub.")

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}

        user_resp = await client.get("https://api.github.com/user", headers=headers)

        if user_resp.status_code != 200:
            return _login_error("Falha ao ler o perfil do GitHub.")

        gh_user = user_resp.json()

        primary_email = ""
        email_resp = await client.get("https://api.github.com/user/emails", headers=headers)

        if email_resp.status_code == 200 and isinstance(email_resp.json(), list):
            primary_email = next(
                (e.get("email", "") for e in email_resp.json() if e.get("primary") and e.get("verified")),
                "",
            ).lower()

    gh_id = str(gh_user["id"])
    gh_username = gh_user["login"]
    display_name = gh_user.get("name") or gh_username
    avatar_url = gh_user.get("avatar_url", "")

    await cur.execute("SELECT id, username FROM users WHERE github_id = %s", (gh_id,))
    existing = await cur.fetchone()

    if logged_in_user_id:
        if existing and existing["id"] != logged_in_user_id:
            return _login_error("Esta conta GitHub já está vinculada a outro utilizador.")

        await cur.execute(
            "UPDATE users SET github_id=%s, github_username=%s, github_token=%s, avatar_url=%s WHERE id=%s",
            (gh_id, gh_username, access_token, avatar_url, logged_in_user_id),
        )
        await cur.execute("SELECT username FROM users WHERE id = %s", (logged_in_user_id,))
        row = await cur.fetchone()

        if not row:
            return _login_error("Utilizador não encontrado.")

        return _finish(logged_in_user_id, row["username"])

    if existing:
        await cur.execute(
            "UPDATE users SET github_username=%s, github_token=%s, avatar_url=%s, last_login=NOW() WHERE id=%s",
            (gh_username, access_token, avatar_url, existing["id"]),
        )
        return _finish(existing["id"], existing["username"])

    if not await _unique_username(cur, gh_username):
        return _login_error(_USERNAME_TAKEN.format(name=gh_username, provider="GitHub"))

    await cur.execute(
        """
        INSERT INTO users (github_id, github_username, username, email, display_name, avatar_url, github_token, last_login)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (gh_id, gh_username, gh_username, primary_email, display_name, avatar_url, access_token),
    )
    await cur.execute("SELECT LAST_INSERT_ID() as id")
    user_id = (await cur.fetchone())["id"]

    return _finish(user_id, gh_username)
