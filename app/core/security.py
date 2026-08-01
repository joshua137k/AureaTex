import bcrypt
from datetime import datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from core.config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET
from core.database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security    = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


def get_password_hash(password: str) -> str:
    hashed_bytes = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    return hashed_bytes.decode('utf-8')


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_jwt(user_id: int, username: str) -> str:
    payload = {
        "sub":      str(user_id),
        "username": username,
        "exp":      datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        print(f"❌ PyJWT Erro (Expirado): {e}")
        raise HTTPException(status_code=401, detail="Token expirado.")
    except jwt.InvalidTokenError as e:
        print(f"❌ PyJWT Erro (Inválido): {e}")
        raise HTTPException(status_code=401, detail=f"Token JWT Inválido: {str(e)}")


# ---------------------------------------------------------------------------
# Dependency — utilizador autenticado
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    cur=Depends(get_db),
):
    if not credentials:
        print("❌ ERRO AUTENTICAÇÃO: Cabeçalho 'Authorization' não recebido.")
        raise HTTPException(status_code=401, detail="Não autenticado.")

    payload = decode_jwt(credentials.credentials)
    user_id = payload.get("sub")

    await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = await cur.fetchone()

    if not user:
        print(f"❌ ERRO DB: O Token diz que és o ID {user_id}, mas ele não existe na base de dados!")
        raise HTTPException(status_code=401, detail="Utilizador não encontrado.")

    return user