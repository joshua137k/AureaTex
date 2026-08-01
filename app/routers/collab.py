"""

Protocolo (JSON):
  Cliente → Servidor:
    {"type": "join",   "file": "main.tex"}
    {"type": "op",     "file": "main.tex", "revision": N, "op": {...}}
    {"type": "cursor", "file": "main.tex", "pos": N}
    {"type": "ping"}

  Servidor → Cliente(s):
    {"type": "init",    "content": "...", "revision": N, "users": [...]}
    {"type": "op",      "file": "...", "revision": N, "op": {...}, "user_id": N}
    {"type": "cursor",  "file": "...", "pos": N, "user_id": N, "username": "...", "color": "..."}
    {"type": "user_join","user_id": N, "username": "...", "color": "..."}
    {"type": "user_leave","user_id": N}
    {"type": "pong"}
    {"type": "error",   "message": "..."}
"""

import asyncio
import json
from collections import defaultdict
from typing import Dict, Set
import time 
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from core.database import get_db, get_pool
from core.ot import DocState, Op
from core.security import decode_jwt

router = APIRouter(tags=["collab"])



CURSOR_COLORS = [
    "#E03030", "#2563EB", "#16A34A", "#D97706",
    "#7C3AED", "#0891B2", "#BE185D", "#059669",
]



class ProjectRoom:
    def __init__(self, project_id: int):
        self.project_id = project_id
        # file_key → DocState
        self.docs:     Dict[str, DocState] = {}
        # user_id → {ws, username, color, file}
        self.users:    Dict[int, dict]     = {}
        # file_key → set de user_ids
        self.watchers: Dict[str, Set[int]] = defaultdict(set)
        self._color_idx = 0
        self._lock = asyncio.Lock()

    def next_color(self) -> str:
        color = CURSOR_COLORS[self._color_idx % len(CURSOR_COLORS)]
        self._color_idx += 1
        return color

    def get_doc(self, file_key: str) -> DocState:
        if file_key not in self.docs:
            self.docs[file_key] = DocState()
        return self.docs[file_key]


# project_id → ProjectRoom
_rooms: Dict[int, ProjectRoom] = {}
_last_save: Dict[tuple, float] = {} 

def get_room(project_id: int) -> ProjectRoom:
    if project_id not in _rooms:
        _rooms[project_id] = ProjectRoom(project_id)
    return _rooms[project_id]

async def sync_doc_content(project_id: int, filename: str, content: str):
    """Atualiza o doc em RAM e força todos os clientes a recarregar"""
    if project_id in _rooms:
        room = _rooms[project_id]
        
        # Bloqueia a sala para impedir que os WebSockets leiam dados inconsistentes
        async with room._lock:
            doc = room.get_doc(filename)
            doc.content = content
            doc.revision += 1
            doc.history.clear() # Limpa histórico para evitar dessincronização
            current_revision = doc.revision

        # Manda os clientes fazerem reset para este ficheiro (fora do lock)
        notify_room(project_id, {
            "type": "init",
            "file": filename,
            "content": content,
            "revision": current_revision,
            "users": [] # Limpa cursores temporariamente
        })


async def rename_doc_in_room(project_id: int, old_name: str, new_name: str):
    """Migra as estruturas de memória de um ficheiro renomeado."""
    if project_id in _rooms:
        room = _rooms[project_id]
        async with room._lock:
            # 1. Muda a chave do documento e o seu histórico
            if old_name in room.docs:
                room.docs[new_name] = room.docs.pop(old_name)
            
            # 2. Muda a chave da lista de espectadores (watchers)
            if old_name in room.watchers:
                room.watchers[new_name] = room.watchers.pop(old_name)
                
            # 3. Atualiza o estado individual de cada utilizador
            for uid, user_info in room.users.items():
                if user_info.get("file") == old_name:
                    user_info["file"] = new_name


def notify_room(project_id: int, msg: dict):
    """Envia um evento global para todos os utilizadores ligados àquele projeto."""
    if project_id in _rooms:
        room = _rooms[project_id]
        import asyncio
        asyncio.create_task(broadcast_to_room(room, msg))



async def send(ws: WebSocket, data: dict):
    try:
        await ws.send_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


async def broadcast_to_file(room: ProjectRoom, file_key: str, data: dict, exclude: int = -1):
    """Envia data a todos os utilizadores a ver este ficheiro (exceto exclude)."""
    for uid in list(room.watchers.get(file_key, set())):
        if uid == exclude:
            continue
        u = room.users.get(uid)
        if u:
            await send(u["ws"], data)


async def broadcast_to_room(room: ProjectRoom, data: dict, exclude: int = -1):
    """Envia data a todos os utilizadores no projeto."""
    for uid, u in list(room.users.items()):
        if uid == exclude:
            continue
        await send(u["ws"], data)


# ---------------------------------------------------------------------------
# Carregar conteúdo inicial da BD
# ---------------------------------------------------------------------------

async def load_file_content(project_id: int, filename: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT content FROM project_files WHERE project_id = %s AND filename = %s",
                (project_id, filename),
            )
            row = await cur.fetchone()
            return (row[0] or "") if row else ""


async def save_file_content(project_id: int, filename: str, content: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO project_files (project_id, filename, content)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW()
                """,
                (project_id, filename, content),
            )
        await conn.commit()


# ---------------------------------------------------------------------------
# WebSocket endpoint principal
# ---------------------------------------------------------------------------

@router.websocket("/ws/projects/{project_id}/collab")
async def collab_ws(
    project_id: int,
    websocket: WebSocket,
    token: str = Query(...),
):
    # Autenticar via token na query string
    try:
        payload = decode_jwt(token)
        user_id  = int(payload["sub"])
        username = payload["username"]
    except Exception:
        await websocket.close(code=4001, reason="Token inválido.")
        return

    await websocket.accept()

    room  = get_room(project_id)
    color = room.next_color()

    async with room._lock:
        room.users[user_id] = {
            "ws":       websocket,
            "username": username,
            "color":    color,
            "file":     None,
        }

    # Notifica todos que um novo utilizador entrou
    await broadcast_to_room(room, {
        "type":     "user_join",
        "user_id":  user_id,
        "username": username,
        "color":    color,
    }, exclude=user_id)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            # ----------------------------------------------------------------
            # PING / PONG (keepalive)
            # ----------------------------------------------------------------
            if mtype == "ping":
                await send(websocket, {"type": "pong"})

            # ----------------------------------------------------------------
            # JOIN — utilizador abre um ficheiro
            # ----------------------------------------------------------------
            elif mtype == "join":
                file_key = msg.get("file", "main.tex")

                async with room._lock:
                    # Remover de qualquer ficheiro anterior
                    old_file = room.users[user_id].get("file")
                    if old_file:
                        room.watchers[old_file].discard(user_id)

                    room.users[user_id]["file"] = file_key
                    room.watchers[file_key].add(user_id)

                    doc = room.get_doc(file_key)

                    # Carregar da BD se ainda não estiver em memória
                    if not doc.content and doc.revision == 0:
                        doc.content = await load_file_content(project_id, file_key)

                # Enviar estado inicial ao cliente
                users_in_file = [
                    {"user_id": uid, "username": room.users[uid]["username"], "color": room.users[uid]["color"]}
                    for uid in room.watchers[file_key]
                    if uid != user_id and uid in room.users
                ]
                await send(websocket, {
                    "type":     "init",
                    "file":     file_key,
                    "content":  doc.content,
                    "revision": doc.revision,
                    "users":    users_in_file,
                })

            # ----------------------------------------------------------------
            # OP — operação de edição (insert / delete)
            # ----------------------------------------------------------------
            elif mtype == "op":
                file_key        = msg.get("file", "main.tex")
                client_revision = msg.get("revision", 0)
                raw_op          = msg.get("op", {})

                async with room._lock:
                    doc = room.get_doc(file_key)
                    op  = Op.from_dict(raw_op)
                    op.user_id = user_id

                    # Transformar contra ops que o cliente ainda não viu
                    if client_revision < doc.revision:
                        op = doc.transform_since(op, client_revision)

                    # Aplicar ao documento
                    doc.apply(op)
                    new_revision = doc.revision

                # Guardar na BD de forma assíncrona (sem bloquear o broadcast)
                now = time.time()
                last = _last_save.get((project_id, file_key), 0)
                if now - last > 2.0:
                    _last_save[(project_id, file_key)] = now
                    asyncio.create_task(
                        save_file_content(project_id, file_key, doc.content)
                    )

                # Enviar ao próprio (confirmação com revisão actualizada)
                await send(websocket, {
                    "type":     "ack",
                    "file":     file_key,
                    "revision": new_revision,
                })

                # Broadcast transformado para os outros
                await broadcast_to_file(room, file_key, {
                    "type":     "op",
                    "file":     file_key,
                    "revision": new_revision,
                    "op":       op.to_dict(),
                    "user_id":  user_id,
                }, exclude=user_id)

            # ----------------------------------------------------------------
            # CURSOR — posição do cursor
            # ----------------------------------------------------------------
            elif mtype == "cursor":
                file_key = room.users[user_id].get("file", "main.tex")
                pos      = msg.get("pos", 0)

                await broadcast_to_file(room, file_key, {
                    "type":     "cursor",
                    "file":     file_key,
                    "pos":      pos,
                    "user_id":  user_id,
                    "username": username,
                    "color":    color,
                }, exclude=user_id)

    except (WebSocketDisconnect, Exception):
        pass

    finally:
        # Limpeza ao sair
        async with room._lock:
            file_key = room.users.get(user_id, {}).get("file")
            if file_key:
                # FORÇA UM ÚLTIMO SAVE NA BD QUANDO O UTILIZADOR FECHA O FICHEIRO
                doc = room.get_doc(file_key)
                if doc.content:
                    asyncio.create_task(save_file_content(project_id, file_key, doc.content))
                    
                room.watchers[file_key].discard(user_id)
            room.users.pop(user_id, None)

        await broadcast_to_room(room, {
            "type":    "user_leave",
            "user_id": user_id,
        })

        # Limpar sala se vazia
        if not room.users:
            _rooms.pop(project_id, None)
            
            # Limpar todos os timers de debounce deste projeto para evitar memory leak
            chaves_a_remover = [k for k in list(_last_save.keys()) if k[0] == project_id]
            for k in chaves_a_remover:
                _last_save.pop(k, None)