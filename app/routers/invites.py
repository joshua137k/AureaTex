"""
Convites por link público.

Fluxo:
  1. Dono gera link  →  POST /api/projects/{id}/invite
     Devolve { token, url }

  2. Convidado abre o link  →  GET /app/join?token=...
     (servido pelo frontend estático — ver main.py)

  3. Convidado aceita       →  POST /api/invites/{token}/accept
     - Adiciona-o a project_collaborators
     - Redireciona para o editor

  4. Dono revoga link       →  DELETE /api/projects/{id}/invite
  5. Dono lista convites    →  GET    /api/projects/{id}/invite
  6. Listar projetos partilhados comigo  →  GET /api/shared
  7. Sair de projeto partilhado          →  DELETE /api/shared/{project_id}
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config import APP_URL
from core.database import get_db
from core.security import get_current_user

from routers.collab import kick_user

router = APIRouter(tags=["invites"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _assert_owner(project_id: int, user_id: int, cur) -> dict:
    await cur.execute(
        "SELECT id, name FROM projects WHERE id = %s AND owner_id = %s",
        (project_id, user_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Projeto não encontrado ou sem permissão.")
    return row


async def _can_access(project_id: int, user_id: int, cur) -> dict:
    """Devolve o projeto se o utilizador for dono ou colaborador."""
    await cur.execute(
        """
        SELECT p.*, u.username AS owner_username
        FROM projects p
        JOIN users u ON u.id = p.owner_id
        WHERE p.id = %s AND (
            p.owner_id = %s
            OR EXISTS (
                SELECT 1 FROM project_collaborators pc
                WHERE pc.project_id = p.id AND pc.user_id = %s
            )
        )
        """,
        (project_id, user_id, user_id),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Sem acesso a este projeto.")
    return row


# ---------------------------------------------------------------------------
# 1. Gerar link de convite
# ---------------------------------------------------------------------------

class InviteCreate(BaseModel):
    role: str = "editor"   # "editor" ou "viewer"


@router.post("/api/projects/{project_id}/invite")
async def create_invite(
    project_id: int,
    body: InviteCreate,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_owner(project_id, user["id"], cur)

    if body.role not in ("editor", "viewer"):
        raise HTTPException(status_code=400, detail="Role inválido. Usa 'editor' ou 'viewer'.")

    # Desativar links anteriores do mesmo role para este projeto (um link ativo por role)
    await cur.execute(
        "UPDATE project_invite_links SET active = 0 WHERE project_id = %s AND role = %s",
        (project_id, body.role),
    )

    token = secrets.token_urlsafe(32)
    await cur.execute(
        "INSERT INTO project_invite_links (project_id, token, created_by, role) VALUES (%s, %s, %s, %s)",
        (project_id, token, user["id"], body.role),
    )

    invite_url = f"{APP_URL}/app/join?token={token}"
    return {"token": token, "url": invite_url, "role": body.role}


# ---------------------------------------------------------------------------
# 2. Ver convites activos de um projeto
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project_id}/invite")
async def list_invites(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_owner(project_id, user["id"], cur)

    await cur.execute(
        """
        SELECT il.token, il.role, il.created_at,
               u.username AS created_by_username
        FROM project_invite_links il
        JOIN users u ON u.id = il.created_by
        WHERE il.project_id = %s AND il.active = 1
        ORDER BY il.created_at DESC
        """,
        (project_id,),
    )
    links = await cur.fetchall()

    return [
        {**lnk, "url": f"{APP_URL}/app/join?token={lnk['token']}"}
        for lnk in links
    ]


# ---------------------------------------------------------------------------
# 3. Revogar todos os convites activos de um projeto
# ---------------------------------------------------------------------------

@router.delete("/api/projects/{project_id}/invite", status_code=204)
async def revoke_invite(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_owner(project_id, user["id"], cur)
    await cur.execute(
        "UPDATE project_invite_links SET active = 0 WHERE project_id = %s",
        (project_id,),
    )


# ---------------------------------------------------------------------------
# 4. Aceitar convite (o convidado chama esta rota)
# ---------------------------------------------------------------------------

@router.post("/api/invites/{token}/accept")
async def accept_invite(
    token: str,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    # Validar token
    await cur.execute(
        """
        SELECT il.project_id, il.role, p.name AS project_name, p.owner_id
        FROM project_invite_links il
        JOIN projects p ON p.id = il.project_id
        WHERE il.token = %s AND il.active = 1
        """,
        (token,),
    )
    invite = await cur.fetchone()
    if not invite:
        raise HTTPException(status_code=404, detail="Link de convite inválido ou expirado.")

    # O dono não precisa de se convidar a si próprio
    if invite["owner_id"] == user["id"]:
        return {
            "project_id":   invite["project_id"],
            "project_name": invite["project_name"],
            "role":         "owner",
            "already_member": True,
        }

    # Inserir ou ignorar se já colaborador
    await cur.execute(
        """
        INSERT INTO project_collaborators (project_id, user_id, role)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE role = VALUES(role)
        """,
        (invite["project_id"], user["id"], invite["role"]),
    )

    return {
        "project_id":   invite["project_id"],
        "project_name": invite["project_name"],
        "role":         invite["role"],
        "already_member": False,
    }


# ---------------------------------------------------------------------------
# 5. Pré-visualizar convite sem aceitar (para mostrar o nome do projeto)
# ---------------------------------------------------------------------------

@router.get("/api/invites/{token}")
async def preview_invite(token: str, cur=Depends(get_db)):
    if len(token) > 64:
        raise HTTPException(status_code=404, detail="Link de convite inválido ou expirado.")

    await cur.execute(
        """
        SELECT p.name AS project_name, p.id AS project_id,
               u.display_name AS owner_name, il.role
        FROM project_invite_links il
        JOIN projects p ON p.id = il.project_id
        JOIN users u    ON u.id = il.created_by
        WHERE il.token = %s AND il.active = 1
        """,
        (token,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link de convite inválido ou expirado.")
    return row


# ---------------------------------------------------------------------------
# 6. Listar projetos partilhados comigo
# ---------------------------------------------------------------------------

@router.get("/api/shared")
async def list_shared(user=Depends(get_current_user), cur=Depends(get_db)):
    await cur.execute(
        """
        SELECT p.id, p.name, p.description, p.template,
               p.updated_at, p.created_at,
               pc.role,
               u.display_name  AS owner_name,
               u.username      AS owner_username
        FROM project_collaborators pc
        JOIN projects p ON p.id  = pc.project_id
        JOIN users    u ON u.id  = p.owner_id
        WHERE pc.user_id = %s
        ORDER BY p.updated_at DESC
        """,
        (user["id"],),
    )
    return await cur.fetchall()


# ---------------------------------------------------------------------------
# 7. Sair de um projeto partilhado
# ---------------------------------------------------------------------------

@router.delete("/api/shared/{project_id}", status_code=204)
async def leave_shared(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await cur.execute(
        "DELETE FROM project_collaborators WHERE project_id = %s AND user_id = %s",
        (project_id, user["id"]),
    )
    await kick_user(project_id, user["id"])


# ---------------------------------------------------------------------------
# 8. Listar colaboradores de um projeto (para o dono ver)
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project_id}/collaborators")
async def list_collaborators(
    project_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_owner(project_id, user["id"], cur)
    await cur.execute(
        """
        SELECT u.id, u.username, u.display_name, u.avatar_url, pc.role, pc.added_at
        FROM project_collaborators pc
        JOIN users u ON u.id = pc.user_id
        WHERE pc.project_id = %s
        ORDER BY pc.added_at
        """,
        (project_id,),
    )
    return await cur.fetchall()


# ---------------------------------------------------------------------------
# 9. Remover colaborador (dono remove alguém)
# ---------------------------------------------------------------------------

@router.delete("/api/projects/{project_id}/collaborators/{collab_user_id}", status_code=204)
async def remove_collaborator(
    project_id: int,
    collab_user_id: int,
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_owner(project_id, user["id"], cur)
    await cur.execute(
        "DELETE FROM project_collaborators WHERE project_id = %s AND user_id = %s",
        (project_id, collab_user_id),
    )
    await cur.connection.commit()

    # Quem perdeu o acesso deixa de poder continuar a editar na sessão aberta.
    await kick_user(project_id, collab_user_id)