"""
"Copy for AI": devolve o projeto (ou uma pasta/ficheiro) como texto markdown
para o editor copiar para a área de transferência.
"""

from fastapi import APIRouter, Depends, Query

from core.ai_context import build_context
from core.database import get_db
from core.security import get_current_user

from routers.collab import flush_project
from routers.files import _assert_project_owner

router = APIRouter(prefix="/api/projects", tags=["ai"])


@router.get("/{project_id}/ai-context")
async def ai_context(
    project_id: int,
    mode: str = Query(default="full", pattern="^(tree|full)$"),
    path: str = Query(default="", max_length=255),
    user=Depends(get_current_user),
    cur=Depends(get_db),
):
    await _assert_project_owner(project_id, user["id"], cur, require_edit=False)

    # Inclui o que ainda só estiver na memória da colaboração.
    await flush_project(project_id)

    return await build_context(cur, project_id, mode, path)
