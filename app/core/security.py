import asyncio
import ipaddress
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET
from core.database import get_db

security = HTTPBearer(auto_error=False)

# bcrypt só usa os primeiros 72 bytes (e as versões recentes rejeitam mais).
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LEN   = 10   # só se aplica a passwords novas


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------

def _verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _hash_password_sync(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode()


async def verify_password(plain: str, hashed: str) -> bool:
    """bcrypt é lento de propósito — corre fora do event loop."""
    if not plain or len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False

    if not hashed:
        # Utilizador inexistente: faz o mesmo trabalho, para o tempo de
        # resposta não revelar se o username existe.
        await asyncio.to_thread(_verify_password_sync, plain, _DUMMY_HASH)
        return False

    return await asyncio.to_thread(_verify_password_sync, plain, hashed)


async def get_password_hash(password: str) -> str:
    return await asyncio.to_thread(_hash_password_sync, password)


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"A password deve ter pelo menos {MIN_PASSWORD_LEN} caracteres.",
        )

    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail="A password é demasiado longa.")


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_jwt(user_id: int, username: str) -> str:
    now = _now()
    payload = {
        "sub":      str(user_id),
        "username": username,
        "typ":      "access",
        "iat":      now,
        # Instante exato em ms: o iat só tem segundos, e uma revogação no
        # mesmo segundo deixava passar tokens antigos.
        "iat_ms":   int(now.timestamp() * 1000),
        "exp":      now + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido.")

    # Tokens de outros fins (ex.: state do OAuth) não servem para autenticar.
    if payload.get("typ", "access") != "access":
        raise HTTPException(status_code=401, detail="Token inválido.")

    return payload


def create_state_token(data: dict, minutes: int = 10) -> str:
    """Token curto e assinado para o parâmetro `state` do OAuth."""
    payload = {
        **data,
        "typ":   "oauth_state",
        "nonce": secrets.token_urlsafe(16),
        "exp":   _now() + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_state_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Pedido de login inválido ou expirado. Tenta de novo.")

    if payload.get("typ") != "oauth_state":
        raise HTTPException(status_code=400, detail="Pedido de login inválido.")

    return payload


# ---------------------------------------------------------------------------
# Dependency — utilizador autenticado
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    cur=Depends(get_db),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Não autenticado.")

    payload = decode_jwt(credentials.credentials)

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Token inválido.")

    await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = await cur.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="Utilizador não encontrado.")

    if token_revoked(payload, user):
        raise HTTPException(status_code=401, detail="Sessão terminada. Entra de novo.")

    return user


async def revoke_sessions(cur, user: dict) -> str:
    """
    Invalida todos os JWT emitidos até agora para este utilizador e devolve um
    novo token para a sessão atual. (Os tokens MCP são independentes.)
    """
    now_ms = int(time.time() * 1000)
    cutoff = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).replace(tzinfo=None)

    await cur.execute(
        "UPDATE users SET tokens_valid_after = %s WHERE id = %s",
        (cutoff, user["id"]),
    )
    return create_jwt(user["id"], user["username"])


def token_revoked(payload: dict, user: dict) -> bool:
    """
    True se o token foi emitido antes de users.tokens_valid_after (mudança de
    password ou "terminar sessão em todos os dispositivos").
    """
    cutoff = user.get("tokens_valid_after")

    if not cutoff:
        return False

    # A coluna guarda UTC sem fuso, com milissegundos.
    cutoff_ms = int(round(cutoff.replace(tzinfo=timezone.utc).timestamp() * 1000))

    try:
        if "iat_ms" in payload:
            return int(payload["iat_ms"]) < cutoff_ms
        # Tokens antigos (sem iat_ms): só segundos.
        return int(payload.get("iat")) * 1000 < cutoff_ms - 999
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Rate limiting simples em memória (processo único)
# ---------------------------------------------------------------------------

def _parse_networks(value: str):
    nets = []
    for item in value.split(","):
        item = item.strip()
        if item:
            try:
                nets.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                print(f"⚠️  TRUSTED_PROXIES: valor inválido ignorado: {item}")
    return nets


# Só se confia em cabeçalhos de IP (CF-Connecting-IP, X-Forwarded-For) quando
# o pedido chega de um proxy conhecido. Por omissão: o próprio host e a rede
# do Docker (é por aí que o cloudflared chega). Quem ligar diretamente à porta
# 8111 já não consegue inventar um IP para fugir ao limite de tentativas.
_TRUSTED_PROXIES = _parse_networks(
    os.getenv("TRUSTED_PROXIES", "127.0.0.0/8,::1/128,172.16.0.0/12")
)


def _is_trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_PROXIES)


def client_ip(request: Request) -> str:
    """IP real do cliente (atrás do túnel Cloudflare vem em cabeçalhos)."""
    peer = request.client.host if request.client else "unknown"

    if not _is_trusted_proxy(peer):
        return peer

    for header in ("cf-connecting-ip", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            return value.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    return peer


class RateLimiter:
    """Janela deslizante: no máximo `limit` eventos por `window` segundos."""

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._events: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque:
        q = self._events[key]

        while q and now - q[0] > self.window:
            q.popleft()

        return q

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        q = self._prune(key, now)

        if not q:
            self._events.pop(key, None)
            return False

        return len(q) >= self.limit

    def hit(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now).append(now)

        # Evita crescimento sem limite se alguém varrer milhares de chaves.
        if len(self._events) > 20000:
            for k in list(self._events.keys()):
                if not self._prune(k, now):
                    self._events.pop(k, None)

    def reset(self, key: str) -> None:
        self._events.pop(key, None)

    def check(self, key: str, detail: str = "Demasiadas tentativas. Tenta mais tarde.") -> None:
        if self.is_blocked(key):
            raise HTTPException(status_code=429, detail=detail)
