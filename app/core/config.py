import os
import secrets

# Todos os segredos vêm de variáveis de ambiente (docker-compose / .env).
# Nada de passwords ou chaves escritas aqui: este ficheiro está no git.

# Gitea OAuth
GITEA_URL     = os.getenv("GITEA_URL",        "http://localhost:3000").rstrip("/")
CLIENT_ID     = os.getenv("GITEA_CLIENT_ID")
CLIENT_SECRET = os.getenv("GITEA_CLIENT_SECRET")
APP_URL       = os.getenv("APP_URL",           "http://localhost:8111").rstrip("/")
REDIRECT_URI  = f"{APP_URL}/auth/callback"

# GitHub OAuth
GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

# JWT — sem valor por omissão conhecido: um segredo público permitiria a
# qualquer pessoa forjar sessões. Se faltar, gera-se um aleatório (as sessões
# deixam de valer sempre que a app reinicia).
JWT_SECRET      = os.getenv("JWT_SECRET") or ""
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_DAYS = 30

if len(JWT_SECRET) < 32:
    print("WARNING: JWT_SECRET em falta ou curto — a usar um segredo aleatório temporário. "
          "Define JWT_SECRET (ex.: openssl rand -base64 48).")
    JWT_SECRET = secrets.token_urlsafe(48)

# MariaDB — a password tem de vir do ambiente.
DB_HOST     = os.getenv("DB_HOST",     "mariadb")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_USER     = os.getenv("DB_USER",     "aureatex")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME",     "aureatex")

if not DB_PASSWORD:
    print("WARNING: DB_PASSWORD não definido — a ligação à base de dados vai falhar.")

BASE_PROJETOS_DIR = "/projetos"
STATIC_DIR        = "/app/static"

# Limites
MAX_REQUEST_BYTES     = 64 * 1024 * 1024    # corpo máximo de qualquer pedido
MAX_FILE_CHARS        = 14 * 1024 * 1024    # um ficheiro (base64 incluído) — abaixo do max_allowed_packet
MAX_ZIP_BYTES         = 50 * 1024 * 1024    # ZIP enviado
MAX_ZIP_UNPACKED      = 200 * 1024 * 1024   # total descomprimido
MAX_ZIP_ENTRIES       = 3000
COMPILE_TIMEOUT_SECS  = 90     # o túnel Cloudflare corta pedidos aos 100 s
