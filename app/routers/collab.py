import asyncio
import json
from collections import defaultdict
from typing import Dict, Set

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from core.database import get_db, get_pool
from core.ot import DocState, Op
from core.security import decode_jwt
from datetime import datetime


router = APIRouter(tags=["collab"])


CURSOR_COLORS = [
    "#E03030",
    "#2563EB",
    "#16A34A",
    "#D97706",
    "#7C3AED",
    "#0891B2",
    "#BE185D",
    "#059669",
]


class ProjectRoom:
    def __init__(self, project_id: int):
        self.project_id = project_id
        self.docs: Dict[str, DocState] = {}
        self.users: Dict[int, dict] = {}
        self.watchers: Dict[str, Set[int]] = defaultdict(set)
        self._color_idx = 0
        self._lock = asyncio.Lock()

        # Quando True, a sala está congelada durante um pull/reload.
        self.reloading = False

    def next_color(self) -> str:
        color = CURSOR_COLORS[self._color_idx % len(CURSOR_COLORS)]
        self._color_idx += 1
        return color

    def get_doc(self, file_key: str) -> DocState:
        if file_key not in self.docs:
            self.docs[file_key] = DocState()
        return self.docs[file_key]


_rooms: Dict[int, ProjectRoom] = {}
_save_tasks: Dict[tuple, asyncio.Task] = {}

# Projetos que estão neste momento em pull/reload.
_reloading_projects: Set[int] = set()


def is_project_reloading(project_id: int) -> bool:
    """
    Pode ser usado por outras rotas para bloquear alterações
    enquanto existe um pull a correr.
    """
    return project_id in _reloading_projects


async def _cancel_project_saves(project_id: int):
    """
    Cancela e aguarda o fim de todos os autosaves pendentes deste projeto.
    """
    tasks = []

    for key in list(_save_tasks.keys()):
        if key[0] == project_id:
            task = _save_tasks.pop(key, None)
            if task:
                task.cancel()
                tasks.append(task)

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _scheduled_save(project_id: int, file_key: str, content: str, user_id: int):
    try:
        await asyncio.sleep(0.5)

        # Se entretanto começou um pull, não guardar conteúdo antigo.
        if project_id in _reloading_projects:
            return

        await save_file_content(project_id, file_key, content, user_id)

    except asyncio.CancelledError:
        pass

    except Exception as e:
        print(f"Erro auto-save: {e}")

    finally:
        _save_tasks.pop((project_id, file_key), None)


def get_room(project_id: int) -> ProjectRoom:
    if project_id not in _rooms:
        _rooms[project_id] = ProjectRoom(project_id)

    room = _rooms[project_id]

    # Se entretanto começou um pull, garante que a sala fica congelada.
    if project_id in _reloading_projects:
        room.reloading = True

    return room


async def sync_doc_content(project_id: int, filename: str, content: str):
    """
    Sincroniza o conteúdo de um ficheiro na sala colaborativa.
    É chamado, por exemplo, depois de guardar um ficheiro via REST.
    """
    if project_id in _reloading_projects:
        return

    if project_id in _rooms:
        room = _rooms[project_id]

        if room.reloading:
            return

        async with room._lock:
            doc = room.get_doc(filename)
            doc.content = content
            doc.revision += 1
            doc.history.clear()

            current_revision = doc.revision

        notify_room(project_id, {
            "type": "init",
            "file": filename,
            "content": content,
            "revision": current_revision,
            "users": []
        })


async def rename_doc_in_room(project_id: int, old_name: str, new_name: str):
    if project_id in _reloading_projects:
        return

    if project_id in _rooms:
        room = _rooms[project_id]

        if room.reloading:
            return

        async with room._lock:
            if old_name in room.docs:
                room.docs[new_name] = room.docs.pop(old_name)

            if old_name in room.watchers:
                room.watchers[new_name] = room.watchers.pop(old_name)

            for uid, user_info in room.users.items():
                if user_info.get("file") == old_name:
                    user_info["file"] = new_name


def notify_room(project_id: int, msg: dict):
    if project_id in _rooms:
        room = _rooms[project_id]
        asyncio.create_task(broadcast_to_room(room, msg))


async def send(ws: WebSocket, data: dict):
    try:
        await ws.send_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


async def broadcast_to_file(room: ProjectRoom, file_key: str, data: dict, exclude: int = -1):
    for uid in list(room.watchers.get(file_key, set())):
        if uid == exclude:
            continue

        u = room.users.get(uid)
        if u:
            await send(u["ws"], data)


async def broadcast_to_room(room: ProjectRoom, data: dict, exclude: int = -1):
    for uid, u in list(room.users.items()):
        if uid == exclude:
            continue

        await send(u["ws"], data)


async def freeze_room_for_pull(project_id: int):
    """
    Congela totalmente o estado colaborativo do projeto antes do pull.

    - marca o projeto como reloading;
    - cancela autosaves pendentes;
    - limpa documentos colaborativos em memória;
    - impede novas operações de colaboração.
    """
    _reloading_projects.add(project_id)
    await _cancel_project_saves(project_id)

    room = _rooms.get(project_id)

    if room:
        async with room._lock:
            room.reloading = True
            room.docs.clear()
            room.watchers.clear()


async def finish_pull_reload(project_id: int):
    """
    Termina o pull e força os clientes a recarregar.

    Deve ser chamado depois de:
    - git pull/reset/clean;
    - atualização da base de dados;
    - commit da transação.
    """
    room = _rooms.get(project_id)

    if room:
        async with room._lock:
            room.reloading = True
            room.docs.clear()
            room.watchers.clear()

        # Avisa todos os utilizadores ligados.
        await broadcast_to_room(room, {"type": "project_reloaded"})

        # Pequena margem para a mensagem sair antes de fechar sockets.
        await asyncio.sleep(0.3)

        async with room._lock:
            users = list(room.users.values())
            room.users.clear()
            room.docs.clear()
            room.watchers.clear()

        for u in users:
            try:
                await u["ws"].close(code=4100, reason="Pull finished")
            except Exception:
                pass

        _rooms.pop(project_id, None)

    _reloading_projects.discard(project_id)


async def abort_pull_reload(project_id: int):
    """
    Deve ser chamado se o pull falhar.

    Descongela a sala e manda os clientes recarregar para voltarem
    a ler o estado atual da base de dados.
    """
    room = _rooms.get(project_id)

    if room:
        async with room._lock:
            room.reloading = False
            room.docs.clear()
            room.watchers.clear()

        notify_room(project_id, {"type": "project_reloaded"})

    _reloading_projects.discard(project_id)


async def load_file_content(project_id: int, filename: str) -> str:
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT content FROM project_files WHERE project_id = %s AND filename = %s",
                (project_id, filename),
            )
            row = await cur.fetchone()
            await conn.commit()

            return (row[0] or "") if row else ""


async def save_file_content(project_id: int, filename: str, content: str, user_id: int = None):
    """
    Guarda conteúdo na tabela project_files.

    Não guarda se o projeto estiver em pull/reload.
    """
    if project_id in _reloading_projects:
        return

    room = _rooms.get(project_id)
    if room and room.reloading:
        return

    try:
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

                if user_id:
                    await cur.execute(
                        """
                        SELECT saved_at FROM project_file_history
                        WHERE project_id = %s AND filename = %s
                        ORDER BY saved_at DESC LIMIT 1
                        """,
                        (project_id, filename),
                    )
                    last_hist = await cur.fetchone()
                    now = datetime.utcnow()

                    if not last_hist or (now - last_hist[0]).total_seconds() > 300:
                        await cur.execute(
                            """
                            INSERT INTO project_file_history (project_id, filename, user_id, content, saved_at)
                            VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())
                            """,
                            (project_id, filename, user_id, content),
                        )

                await conn.commit()

    except Exception as e:
        # FK inexistente pode acontecer em projetos acabados de apagar.
        if "1452" in str(e):
            pass
        else:
            print(f"[Aviso] Erro no auto-save em background: {e}")


@router.websocket("/ws/projects/{project_id}/collab")
async def collab_ws(
    project_id: int,
    websocket: WebSocket,
    token: str = Query(...),
):
    try:
        payload = decode_jwt(token)
        user_id = int(payload["sub"])
        username = payload["username"]

    except Exception:
        await websocket.close(code=4001, reason="Token inválido.")
        return

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    CASE WHEN owner_id = %s THEN 'owner' ELSE pc.role END as user_role
                FROM projects p
                LEFT JOIN project_collaborators pc
                    ON pc.project_id = p.id AND pc.user_id = %s
                WHERE p.id = %s AND (p.owner_id = %s OR pc.id IS NOT NULL)
                """,
                (user_id, user_id, project_id, user_id),
            )
            row = await cur.fetchone()

            if not row:
                await conn.commit()
                await websocket.close(code=4003, reason="Sem acesso ao projeto.")
                return

            role = row[0]
            await conn.commit()

    await websocket.accept()

    room = get_room(project_id)
    color = room.next_color()

    async with room._lock:
        room.users[user_id] = {
            "ws": websocket,
            "username": username,
            "color": color,
            "file": None,
            "role": role,
        }

    if not room.reloading and project_id not in _reloading_projects:
        await broadcast_to_room(room, {
            "type": "user_join",
            "user_id": user_id,
            "username": username,
            "color": color,
        }, exclude=user_id)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            # Durante pull/reload, ignora tudo exceto ping.
            if room.reloading or project_id in _reloading_projects:
                if mtype == "ping":
                    await send(websocket, {"type": "pong"})
                continue

            if mtype == "ping":
                await send(websocket, {"type": "pong"})

            elif mtype == "join":
                file_key = msg.get("file", "main.tex")

                async with room._lock:
                    old_file = room.users[user_id].get("file")

                    if old_file:
                        room.watchers[old_file].discard(user_id)

                        if not room.watchers[old_file]:
                            room.docs.pop(old_file, None)

                    room.users[user_id]["file"] = file_key
                    room.watchers[file_key].add(user_id)

                    doc = room.get_doc(file_key)

                    if not doc.content and doc.revision == 0:
                        doc.content = await load_file_content(project_id, file_key)

                    users_in_file = [
                        {
                            "user_id": uid,
                            "username": room.users[uid]["username"],
                            "color": room.users[uid]["color"],
                        }
                        for uid in room.watchers[file_key]
                        if uid != user_id and uid in room.users
                    ]

                await send(websocket, {
                    "type": "init",
                    "file": file_key,
                    "content": doc.content,
                    "revision": doc.revision,
                    "users": users_in_file,
                })

            elif mtype == "op":
                if room.users[user_id].get("role") == "viewer":
                    continue

                file_key = msg.get("file", "main.tex")
                client_revision = msg.get("revision", 0)
                raw_op = msg.get("op", {})

                async with room._lock:
                    doc = room.get_doc(file_key)
                    op = Op.from_dict(raw_op)
                    op.user_id = user_id

                    if client_revision < doc.revision:
                        op = doc.transform_since(op, client_revision)

                    doc.apply(op)
                    new_revision = doc.revision

                    task_key = (project_id, file_key)

                    if task_key in _save_tasks:
                        _save_tasks[task_key].cancel()

                    _save_tasks[task_key] = asyncio.create_task(
                        _scheduled_save(project_id, file_key, doc.content, user_id)
                    )

                await send(websocket, {
                    "type": "ack",
                    "file": file_key,
                    "revision": new_revision,
                })

                await broadcast_to_file(room, file_key, {
                    "type": "op",
                    "file": file_key,
                    "revision": new_revision,
                    "op": op.to_dict(),
                    "user_id": user_id,
                }, exclude=user_id)

            elif mtype == "cursor":
                file_key = room.users[user_id].get("file", "main.tex")
                pos = msg.get("pos", 0)

                await broadcast_to_file(room, file_key, {
                    "type": "cursor",
                    "file": file_key,
                    "pos": pos,
                    "user_id": user_id,
                    "username": username,
                    "color": color,
                }, exclude=user_id)

    except (WebSocketDisconnect, Exception):
        pass

    finally:
        async with room._lock:
            user_info = room.users.get(user_id, {})
            file_key = user_info.get("file")

            if file_key:
                # Não guardar conteúdo antigo se estivermos no meio de um pull.
                if not room.reloading and project_id not in _reloading_projects:
                    doc = room.docs.get(file_key)

                    if doc and doc.content:
                        asyncio.create_task(
                            save_file_content(project_id, file_key, doc.content, user_id)
                        )

                room.watchers[file_key].discard(user_id)

                if not room.watchers[file_key]:
                    room.docs.pop(file_key, None)

            room.users.pop(user_id, None)

        if not room.reloading and project_id not in _reloading_projects:
            await broadcast_to_room(room, {
                "type": "user_leave",
                "user_id": user_id,
            })

        if not room.users:
            _rooms.pop(project_id, None)
            await _cancel_project_saves(project_id)