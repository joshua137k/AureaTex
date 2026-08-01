"""

  Cliente → Servidor:
    {"type": "execute", "jobId": "...", "code": "..."}
    {"type": "cancel",  "jobId": "..."}

  Servidor → Cliente:
    {"type": "job:started",      "jobId": "..."}
    {"type": "job:result",       "jobId": "...", "value": "..." | null}
    {"type": "job:plot",         "jobId": "...", "traces": [...], "layout": {...}}
    {"type": "job:clc",          "jobId": "..."}
    {"type": "job:error",        "jobId": "...", "message": "..."}
    {"type": "job:done",         "jobId": "..."}
    {"type": "job:cancelled",    "jobId": "..."}
    {"type": "workspace:update", "scope": {...}}
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
from typing import Dict

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from core.config import BASE_PROJETOS_DIR
from core.execution_manager import JOB_TIMEOUT_SECONDS, ExecutionSession
from core.security import decode_jwt

router = APIRouter(tags=["execute"])


@router.websocket("/ws/projects/{project_id}/execute")
async def execute_ws(project_id: int, websocket: WebSocket, token: str = Query(...)):
    try:
        decode_jwt(token)
    except Exception:
        await websocket.close(code=4001, reason="Token inválido.")
        return

    await websocket.accept()

    workspace_dir = os.path.join(BASE_PROJETOS_DIR, str(project_id))
    os.makedirs(workspace_dir, exist_ok=True)
    session = ExecutionSession(workspace_dir)

    loop = asyncio.get_event_loop()
    running = True
    job_timers: Dict[str, asyncio.Task] = {}

    async def send(payload: dict) -> None:
        try:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    async def pump_events() -> None:
        while running:
            try:
                # Tenta ler da fila instantaneamente sem bloquear a Thread!
                msg = session.evt_queue.get_nowait()
            except queue.Empty:
                # Se não há mensagens, cede o controlo do event loop aos outros utilizadores
                await asyncio.sleep(0.05)
                continue

            if msg.get("type") == "scope:snapshot":
                session.last_snapshot = msg.get("snapshot")
                continue
            if msg.get("type") in ("job:done", "job:error"):
                timer = job_timers.pop(msg.get("jobId"), None)
                if timer:
                    timer.cancel()
            await send(msg)

    pump_task = asyncio.create_task(pump_events())

    async def watch_timeout(job_id: str) -> None:
        try:
            await asyncio.sleep(JOB_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        session.restart()
        await send({
            "type": "job:error", "jobId": job_id,
            "message": f"Tempo limite de {JOB_TIMEOUT_SECONDS}s excedido — "
                       f"execução cancelada e kernel reiniciado. O workspace foi preservado.",
        })
        await send({"type": "job:done", "jobId": job_id})

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "execute":
                job_id = msg.get("jobId")
                await send({"type": "job:started", "jobId": job_id})
                session.execute(job_id, msg.get("code", ""))
                job_timers[job_id] = asyncio.create_task(watch_timeout(job_id))

            elif mtype == "cancel":
                job_id = msg.get("jobId")
                timer = job_timers.pop(job_id, None)
                if timer:
                    timer.cancel()
                session.restart()
                await send({"type": "job:cancelled", "jobId": job_id})

    except WebSocketDisconnect:
        pass
    finally:
        running = False
        pump_task.cancel()
        for timer in job_timers.values():
            timer.cancel()
        session.close()
