import os

# Gitea OAuth
GITEA_URL     = os.getenv("GITEA_URL",        "https://gitea.joshuadourado.pt").rstrip("/")
CLIENT_ID     = os.getenv("GITEA_CLIENT_ID")
CLIENT_SECRET = os.getenv("GITEA_CLIENT_SECRET")
APP_URL       = os.getenv("APP_URL",           "http://100.103.241.42:8111")
REDIRECT_URI  = f"{APP_URL}/auth/callback"

# GitHub OAuth
GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

# JWT
JWT_SECRET      = os.getenv("JWT_SECRET", "muda-este-secret-em-producao!")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_DAYS = 30

# MariaDB
DB_HOST     = os.getenv("DB_HOST",     "mariadb")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_USER     = os.getenv("DB_USER",     "joshuatex")
DB_PASSWORD = os.getenv("DB_PASSWORD", "joshuatex_pass")
DB_NAME     = os.getenv("DB_NAME",     "joshuatex")

BASE_PROJETOS_DIR = "/projetos"
STATIC_DIR        = "/app/static"