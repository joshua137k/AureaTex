"""
Colaboração em tempo real (WebSocket + OT).

Regras do estado em memória (a "cache"):
  - Um documento só existe em memória enquanto alguém o tem aberto. Quando o
    último utilizador sai, é gravado e descartado — a BD é sempre a fonte de
    verdade para ficheiros que ninguém está a editar.
  - Só documentos com alterações por gravar (`dirty`) são escritos na BD, e
    sempre com UPDATE: um ficheiro apagado nunca é "ressuscitado" pela cache.
  - Qualquer escrita externa (REST, plugin VS Code, restauro de histórico,
    referências) passa por sync_doc_content()/drop_doc() para que a cópia em
    memória nunca fique desatualizada.

Tudo isto assume UM único processo (uvicorn --workers 1).
"""

import asyncio
import itertools
import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.config import MAX_FILE_CHARS
from core.database import get_pool
from core.ot import DocState, InvalidOp, Op, StaleRevision
from core.paths import InvalidFilename, normalize_filename
from core.security import decode_jwt, token_revoked


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

SAVE_DEBOUNCE_SECS = 0.8
HISTORY_INTERVAL_SECS = 300
MAX_OP_TEXT = 2 * 1024 * 1024

_conn_ids = itertools.count(1)


class Conn:
    """Uma ligação WebSocket (um separador do browser)."""

    def __init__(self, ws: WebSocket, user_id: int, username: str, role: str, color: str):
        self.id = next(_conn_ids)
        self.ws = ws
        self.user_id = user_id
        self.username = username
        self.role = role
        self.color = color
        self.file: Optional[str] = None


class ProjectRoom:
    def __init__(self, project_id: int):
        self.project_id = project_id
        self.docs: Dict[str, DocState] = {}
        self.conns: Dict[int, Conn] = {}
        self.watchers: Dict[str, Set[int]] = defaultdict(set)
        self._color_idx = 0

        # Protege docs/conns/watchers.
        self._lock = asyncio.Lock()
        # Serializa as escritas na BD (nunca adquirir com _lock já na mão).
        self._save_lock = asyncio.Lock()

        # Quando True, a sala está congelada durante um pull/reload.
        self.reloading = False

    def next_color(self) -> str:
        color = CURSOR_COLORS[self._color_idx % len(CURSOR_COLORS)]
        self._color_idx += 1
        return color


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


def get_room(project_id: int) -> ProjectRoom:
    if project_id not in _rooms:
        _rooms[project_id] = ProjectRoom(project_id)

    room = _rooms[project_id]

    # Se entretanto começou um pull, garante que a sala fica congelada.
    if project_id in _reloading_projects:
        room.reloading = True

    return room


# ---------------------------------------------------------------------------
# Envio de mensagens
# ---------------------------------------------------------------------------

async def send(ws: WebSocket, data: dict):
    try:
        await ws.send_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


async def broadcast_to_file(room: ProjectRoom, file_key: str, data: dict, exclude: int = -1):
    for cid in list(room.watchers.get(file_key, set())):
        if cid == exclude:
            continue

        conn = room.conns.get(cid)
        if conn:
            await send(conn.ws, data)


async def broadcast_to_room(room: ProjectRoom, data: dict, exclude: int = -1):
    for cid, conn in list(room.conns.items()):
        if cid == exclude:
            continue

        await send(conn.ws, data)


def notify_room(project_id: int, msg: dict):
    room = _rooms.get(project_id)

    if room:
        asyncio.create_task(broadcast_to_room(room, msg))


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

async def load_file_content(project_id: int, filename: str) -> Optional[str]:
    """Conteúdo do ficheiro na BD, ou None se o ficheiro não existir."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT content FROM project_files WHERE project_id = %s AND filename = %s",
                (project_id, filename),
            )
            row = await cur.fetchone()
            await conn.commit()

            if not row:
                return None

            return row[0] or ""


async def record_history(cur, project_id: int, filename: str, content: str, user_id: Optional[int]):
    """Guarda uma versão no histórico, no máximo uma a cada 5 minutos por ficheiro."""
    if not user_id or content.startswith("data:"):
        return

    await cur.execute(
        """
        SELECT TIMESTAMPDIFF(SECOND, MAX(saved_at), UTC_TIMESTAMP())
        FROM project_file_history
        WHERE project_id = %s AND filename = %s
        """,
        (project_id, filename),
    )
    row = await cur.fetchone()
    elapsed = (row[0] if not isinstance(row, dict) else list(row.values())[0]) if row else None

    if elapsed is None or elapsed > HISTORY_INTERVAL_SECS:
        await cur.execute(
            """
            INSERT INTO project_file_history (project_id, filename, user_id, content, saved_at)
            VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())
            """,
            (project_id, filename, user_id, content),
        )


async def _persist(project_id: int, filename: str, content: str, user_id: Optional[int]) -> None:
    """
    Grava o conteúdo de um documento aberto. Usa UPDATE de propósito: se o
    ficheiro foi apagado entretanto, não volta a ser criado.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE project_files
                    SET content = %s, updated_at = NOW()
                    WHERE project_id = %s AND filename = %s
                    """,
                    (content, project_id, filename),
                )
                await record_history(cur, project_id, filename, content, user_id)
                await cur.execute(
                    "UPDATE projects SET updated_at = NOW() WHERE id = %s",
                    (project_id,),
                )

            await conn.commit()

        except Exception:
            await conn.rollback()
            raise


async def _flush_doc(room: ProjectRoom, file_key: str) -> None:
    """Grava um documento se tiver alterações pendentes."""
    if room.reloading or room.project_id in _reloading_projects:
        return

    async with room._save_lock:
        async with room._lock:
            doc = room.docs.get(file_key)

            if not doc or not doc.dirty:
                return

            content = doc.content
            editor = doc.last_editor
            doc.dirty = False

        try:
            # shield: um cancelamento do autosave não interrompe a escrita a meio.
            await asyncio.shield(_persist(room.project_id, file_key, content, editor))

        except asyncio.CancelledError:
            raise

        except Exception as e:
            print(f"[collab] Erro ao gravar {room.project_id}/{file_key}: {e}")

            async with room._lock:
                doc = room.docs.get(file_key)
                if doc:
                    doc.dirty = True


async def _scheduled_save(room: ProjectRoom, file_key: str):
    try:
        await asyncio.sleep(SAVE_DEBOUNCE_SECS)
        await _flush_doc(room, file_key)

    except asyncio.CancelledError:
        pass

    finally:
        key = (room.project_id, file_key)
        if _save_tasks.get(key) is asyncio.current_task():
            _save_tasks.pop(key, None)


def _schedule_save(room: ProjectRoom, file_key: str) -> None:
    key = (room.project_id, file_key)
    old = _save_tasks.get(key)

    if old:
        old.cancel()

    _save_tasks[key] = asyncio.create_task(_scheduled_save(room, file_key))


def _cancel_save(project_id: int, file_key: str) -> None:
    task = _save_tasks.pop((project_id, file_key), None)

    if task:
        task.cancel()


async def _cancel_project_saves(project_id: int):
    """Cancela e aguarda o fim de todos os autosaves pendentes deste projeto."""
    tasks = []

    for key in list(_save_tasks.keys()):
        if key[0] == project_id:
            task = _save_tasks.pop(key, None)
            if task:
                task.cancel()
                tasks.append(task)

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def flush_project(project_id: int) -> None:
    """
    Grava na BD todas as alterações pendentes do projeto.
    Chamado antes de compilar, fazer push ou exportar.
    """
    room = _rooms.get(project_id)

    if not room:
        return

    for file_key in list(room.docs.keys()):
        await _flush_doc(room, file_key)


# ---------------------------------------------------------------------------
# API para as outras rotas
# ---------------------------------------------------------------------------

async def sync_doc_content(project_id: int, filename: str, content: str):
    """
    Um ficheiro foi escrito por fora do WebSocket (REST, plugin, restauro...).
    Atualiza a cópia em memória, se existir, e avisa os clientes ligados.
    """
    if project_id in _reloading_projects:
        return

    room = _rooms.get(project_id)

    if not room or room.reloading:
        return

    revision = 0

    async with room._lock:
        doc = room.docs.get(filename)

        if doc:
            _cancel_save(project_id, filename)
            doc.reset(content)
            revision = doc.revision

    # Binários não interessam ao editor de texto dos outros clientes.
    if content.startswith("data:"):
        return

    notify_room(project_id, {
        "type": "init",
        "file": filename,
        "content": content,
        "revision": revision,
        "users": [],
    })


async def drop_doc(project_id: int, filename: str):
    """Um ficheiro foi apagado: descarta a cópia em memória sem a gravar."""
    room = _rooms.get(project_id)

    if not room:
        return

    async with room._lock:
        _cancel_save(project_id, filename)
        room.docs.pop(filename, None)

        for cid in room.watchers.pop(filename, set()):
            conn = room.conns.get(cid)
            if conn and conn.file == filename:
                conn.file = None


async def rename_doc_in_room(project_id: int, old_name: str, new_name: str):
    if project_id in _reloading_projects:
        return

    room = _rooms.get(project_id)

    if not room or room.reloading:
        return

    reschedule = False

    async with room._lock:
        _cancel_save(project_id, old_name)

        if old_name in room.docs:
            doc = room.docs.pop(old_name)
            room.docs[new_name] = doc
            reschedule = doc.dirty

        if old_name in room.watchers:
            room.watchers[new_name] = room.watchers.pop(old_name)

        for conn in room.conns.values():
            if conn.file == old_name:
                conn.file = new_name

    if reschedule:
        _schedule_save(room, new_name)


class room_edit:
    """
    Contexto para editar um ficheiro de forma atómica em relação ao collab:

        async with room_edit(project_id) as edit:
            current = edit.live_content(filename)     # None se ninguém o tem aberto
            ...
            await edit.append(filename, text, user_id)  # só se estiver aberto

    Enquanto o contexto está aberto ninguém consegue abrir/editar ficheiros da
    sala, por isso ler da BD e escrever de volta é seguro.
    """

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.room = _rooms.get(project_id)

    async def __aenter__(self):
        if self.room:
            await self.room._lock.acquire()
        return self

    async def __aexit__(self, *exc):
        if self.room:
            self.room._lock.release()

    def live_content(self, filename: str) -> Optional[str]:
        if not self.room or self.room.reloading:
            return None

        doc = self.room.docs.get(filename)
        return doc.content if doc else None

    async def append(self, filename: str, text: str, user_id: Optional[int] = None) -> None:
        """Acrescenta texto a um documento aberto, como uma op normal de OT."""
        doc = self.room.docs[filename]

        op = Op(type="insert", pos=doc.length(), text=text, user_id=0)
        doc.apply(op)
        doc.last_editor = user_id

        _schedule_save(self.room, filename)

        asyncio.create_task(broadcast_to_file(self.room, filename, {
            "type": "op",
            "file": filename,
            "revision": doc.revision,
            "op": op.to_dict(),
            "user_id": 0,
        }))


async def close_room(project_id: int, code: int = 4004, reason: str = "Project closed"):
    """Fecha todas as ligações de um projeto (ex.: projeto apagado)."""
    await _cancel_project_saves(project_id)

    room = _rooms.pop(project_id, None)

    if not room:
        return

    async with room._lock:
        conns = list(room.conns.values())
        room.conns.clear()
        room.docs.clear()
        room.watchers.clear()

    for conn in conns:
        try:
            await conn.ws.close(code=code, reason=reason)
        except Exception:
            pass


async def kick_user(project_id: int, user_id: int):
    """Fecha as ligações de um utilizador que perdeu o acesso ao projeto."""
    room = _rooms.get(project_id)

    if not room:
        return

    for conn in [c for c in room.conns.values() if c.user_id == user_id]:
        try:
            await conn.ws.close(code=4003, reason="Access revoked")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Pull / reload
# ---------------------------------------------------------------------------

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
        # Espera por alguma escrita que já estivesse a meio.
        async with room._save_lock:
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
            conns = list(room.conns.values())
            room.conns.clear()
            room.docs.clear()
            room.watchers.clear()

        for conn in conns:
            try:
                await conn.ws.close(code=4100, reason="Pull finished")
            except Exception:
                pass

        if _rooms.get(project_id) is room:
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

            for conn in room.conns.values():
                conn.file = None

        notify_room(project_id, {"type": "project_reloaded"})

    _reloading_projects.discard(project_id)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

async def _authenticate(websocket: WebSocket, project_id: int, token: Optional[str]):
    """Devolve (user_id, username, role) ou None se não tiver acesso."""
    try:
        payload = decode_jwt(token or "")
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=4001, reason="Token inválido.")
        return None

    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    CASE WHEN p.owner_id = %s THEN 'owner' ELSE pc.role END AS user_role,
                    u.username,
                    u.tokens_valid_after
                FROM projects p
                JOIN users u ON u.id = %s
                LEFT JOIN project_collaborators pc
                    ON pc.project_id = p.id AND pc.user_id = %s
                WHERE p.id = %s AND (p.owner_id = %s OR pc.id IS NOT NULL)
                """,
                (user_id, user_id, user_id, project_id, user_id),
            )
            row = await cur.fetchone()
            await conn.commit()

    if row and token_revoked(payload, {"tokens_valid_after": row[2]}):
        await websocket.close(code=4001, reason="Sessão terminada.")
        return None

    if not row:
        await websocket.close(code=4003, reason="Sem acesso ao projeto.")
        return None

    return user_id, row[1], row[0]


async def _leave_file(room: ProjectRoom, conn: Conn) -> None:
    """Tira a ligação do ficheiro atual; grava e descarta o doc se ficar sem ninguém."""
    old_file = conn.file

    if not old_file:
        return

    async with room._lock:
        conn.file = None
        room.watchers[old_file].discard(conn.id)
        empty = not room.watchers[old_file]

    if not empty:
        return

    await _flush_doc(room, old_file)

    async with room._lock:
        if not room.watchers.get(old_file):
            doc = room.docs.get(old_file)

            # Se a gravação falhou, mantém em memória para tentar outra vez.
            if doc is None or not doc.dirty:
                room.docs.pop(old_file, None)
                room.watchers.pop(old_file, None)
            else:
                _schedule_save(room, old_file)


async def _handle_join(room: ProjectRoom, conn: Conn, msg: dict) -> None:
    try:
        file_key = normalize_filename(msg.get("file") or "")
    except InvalidFilename:
        await send(conn.ws, {"type": "error", "error": "invalid_file"})
        return

    if conn.file and conn.file != file_key:
        await _leave_file(room, conn)

    async with room._lock:
        if room.reloading:
            return

        doc = room.docs.get(file_key)

        if doc is None:
            # Ninguém tem o ficheiro aberto: a BD é a fonte de verdade.
            content = await load_file_content(room.project_id, file_key)

            if content is None:
                await send(conn.ws, {"type": "error", "error": "file_not_found", "file": file_key})
                return

            doc = DocState()
            doc.set_content(content)
            room.docs[file_key] = doc

        conn.file = file_key
        room.watchers[file_key].add(conn.id)

        users_in_file = [
            {
                "user_id": cid,
                "username": room.conns[cid].username,
                "color": room.conns[cid].color,
            }
            for cid in room.watchers[file_key]
            if cid != conn.id and cid in room.conns
        ]

        init = {
            "type": "init",
            "file": file_key,
            "content": doc.content,
            "revision": doc.revision,
            "users": users_in_file,
        }

    await send(conn.ws, init)


async def _handle_op(room: ProjectRoom, conn: Conn, msg: dict) -> None:
    if conn.role == "viewer":
        return

    file_key = msg.get("file")

    # Só se aceitam ops para o ficheiro em que a ligação fez "join".
    if not file_key or file_key != conn.file:
        return

    resync = None

    async with room._lock:
        doc = room.docs.get(file_key)

        if doc is None:
            return

        try:
            op = Op.from_dict(msg.get("op"))
            op.user_id = conn.id

            if op.type == "insert" and (
                len(op.text) > MAX_OP_TEXT or len(doc.content) + len(op.text) > MAX_FILE_CHARS
            ):
                raise InvalidOp("demasiado grande")

            client_revision = msg.get("revision", 0)
            if not isinstance(client_revision, int):
                raise InvalidOp("revision inválida")

            op = doc.transform_since(op, client_revision)
            doc.apply(op)
            doc.last_editor = conn.user_id
            new_revision = doc.revision

        except (InvalidOp, StaleRevision):
            # O cliente está dessincronizado: volta a receber o estado completo.
            resync = {
                "type": "init",
                "file": file_key,
                "content": doc.content,
                "revision": doc.revision,
                "users": [],
            }

        else:
            _schedule_save(room, file_key)

    if resync:
        await send(conn.ws, resync)
        return

    await send(conn.ws, {
        "type": "ack",
        "file": file_key,
        "revision": new_revision,
    })

    await broadcast_to_file(room, file_key, {
        "type": "op",
        "file": file_key,
        "revision": new_revision,
        "op": op.to_dict(),
        "user_id": conn.id,
    }, exclude=conn.id)


@router.websocket("/ws/projects/{project_id}/collab")
async def collab_ws(
    project_id: int,
    websocket: WebSocket,
    token: Optional[str] = None,
):
    await websocket.accept()

    # O token vem na primeira mensagem ({"type":"auth"}) para não ficar em URLs
    # nem em logs. A query string continua a ser aceite por compatibilidade.
    if not token:
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=15))
            token = first.get("token") if first.get("type") == "auth" else None
        except Exception:
            token = None

    auth = await _authenticate(websocket, project_id, token)

    if not auth:
        return

    user_id, username, role = auth

    room = get_room(project_id)
    conn = Conn(websocket, user_id, username, role, room.next_color())

    async with room._lock:
        room.conns[conn.id] = conn

    await send(websocket, {
        "type": "welcome",
        "client_id": conn.id,
        "role": role,
    })

    if not room.reloading and project_id not in _reloading_projects:
        await broadcast_to_room(room, {
            "type": "user_join",
            "user_id": conn.id,
            "username": username,
            "color": conn.color,
        }, exclude=conn.id)

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except ValueError:
                continue

            if not isinstance(msg, dict):
                continue

            mtype = msg.get("type")

            if mtype == "ping":
                await send(websocket, {"type": "pong"})
                continue

            # Durante pull/reload, ignora tudo exceto ping.
            if room.reloading or project_id in _reloading_projects:
                continue

            if mtype == "join":
                await _handle_join(room, conn, msg)

            elif mtype == "leave":
                await _leave_file(room, conn)

            elif mtype == "op":
                await _handle_op(room, conn, msg)

            elif mtype == "cursor":
                pos = msg.get("pos", 0)

                if conn.file and isinstance(pos, int):
                    await broadcast_to_file(room, conn.file, {
                        "type": "cursor",
                        "file": conn.file,
                        "pos": max(0, pos),
                        "user_id": conn.id,
                        "username": username,
                        "color": conn.color,
                    }, exclude=conn.id)

    except WebSocketDisconnect:
        pass

    except Exception as e:
        print(f"[collab] Ligação terminada com erro: {e!r}")

    finally:
        try:
            await _leave_file(room, conn)
        except Exception as e:
            print(f"[collab] Erro ao sair do ficheiro: {e!r}")

        async with room._lock:
            room.conns.pop(conn.id, None)
            empty = not room.conns

        if not room.reloading and project_id not in _reloading_projects:
            await broadcast_to_room(room, {
                "type": "user_leave",
                "user_id": conn.id,
            })

        if empty and _rooms.get(project_id) is room and not room.reloading:
            # Última ligação: garante que nada fica por gravar antes de fechar a sala.
            await flush_project(project_id)

            if not room.conns:
                await _cancel_project_saves(project_id)

                if _rooms.get(project_id) is room:
                    _rooms.pop(project_id, None)
