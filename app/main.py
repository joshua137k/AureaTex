import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import STATIC_DIR
from core.database import get_pool
from routers import auth, collab, compile, files, projects, users, invites, references, execute, vscode

app = FastAPI(title="JoshuaTeX API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(files.router)
app.include_router(compile.router)
app.include_router(users.router)
app.include_router(collab.router)
app.include_router(invites.router)
app.include_router(references.router)
app.include_router(execute.router)
app.include_router(vscode.router)

# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    index_path = os.path.join(STATIC_DIR, "login", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "JoshuaTeX API online. Coloca os HTML em /app/static/"}


@app.get("/app/{page}")
def serve_page(page: str):
    path = os.path.join(STATIC_DIR, f"{page}/index.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail=f"Página '{page}' não encontrada.")


# ---------------------------------------------------------------------------
# Startup — criar tabelas
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    gitea_id      INT UNIQUE,
                    username      VARCHAR(100) NOT NULL UNIQUE,
                    email         VARCHAR(255),
                    password_hash VARCHAR(255),
                    display_name  VARCHAR(255),
                    avatar_url    VARCHAR(512),
                    gitea_token   TEXT,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_login    DATETIME
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    owner_id    INT NOT NULL,
                    name        VARCHAR(255) NOT NULL,
                    description TEXT,
                    template    VARCHAR(50) DEFAULT 'blank',
                    gitea_repo  VARCHAR(255),
                    is_public   TINYINT(1) DEFAULT 0,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_owner_project (owner_id, name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS project_files (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    project_id INT NOT NULL,
                    filename   VARCHAR(255) NOT NULL,
                    content    LONGTEXT,
                    is_main    TINYINT(1) DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_file (project_id, filename)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS project_collaborators (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    project_id INT NOT NULL,
                    user_id    INT NOT NULL,
                    role       ENUM('viewer','editor','owner') DEFAULT 'editor',
                    added_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
                    UNIQUE KEY unique_collab (project_id, user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS compile_log (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    project_id  INT NOT NULL,
                    user_id     INT NOT NULL,
                    success     TINYINT(1) DEFAULT 0,
                    log_output  TEXT,
                    duration_ms INT,
                    compiled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS project_invite_links (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    project_id INT NOT NULL,
                    token      VARCHAR(64) NOT NULL UNIQUE,
                    created_by INT NOT NULL,
                    role       ENUM('viewer','editor') DEFAULT 'editor',
                    active     TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id)    ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            await cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS zotero_user_id VARCHAR(50),
                ADD COLUMN IF NOT EXISTS zotero_api_key  VARCHAR(100)
            """)
    print("✅ Tabelas MariaDB criadas / verificadas.")