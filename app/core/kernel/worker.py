"""
NôveaTex — Ponto de entrada do processo de execução isolado.
"""

from __future__ import annotations

import multiprocessing as mp
import traceback
import os
import pwd
from typing import Any, Dict

from .engine import NoveaRuntimeError, Session


def _drop_privileges(workspace_dir: str):
    """
    Desce os privilégios do processo para o utilizador 'nobody'.
    Isto impede que o código do utilizador consiga ler o código da tua API 
    ou ficheiros fora do workspace dele.
    """
    try:
        # Muda para a pasta do projeto para evitar manter lock na pasta da API
        os.chdir(workspace_dir)
        
        # Só funciona em Linux (dentro do teu Docker). Ignora no Windows (Dev).
        if hasattr(os, 'setuid'):
            # Busca o UID e GID do utilizador mais restrito do Linux ('nobody')
            nobody = pwd.getpwnam('nobody')
            
            # Remove permissões de grupo e força a descida de privilégios
            os.setgroups([])
            os.setgid(nobody.pw_gid)
            os.setuid(nobody.pw_uid)
    except Exception as e:
        print(f"[Sandbox] Aviso: Não foi possível descer privilégios do processo: {e}")


def worker_main(cmd_queue: "mp.Queue[Dict[str, Any]]",
                 evt_queue: "mp.Queue[Dict[str, Any]]",
                 workspace_dir: str) -> None:
                 
    # 1. 🚨 Corta os privilégios do OS ANTES de criar a sessão e fazer eval!
    _drop_privileges(workspace_dir)

    def emit(msg_type: str, payload: dict) -> None:
        evt_queue.put({"type": msg_type, **payload})

    session = Session(workspace_dir=workspace_dir, emit=emit)

    while True:
        msg = cmd_queue.get()
        if msg is None:
            break

        msg_type = msg.get("type")

        if msg_type == "execute":
            job_id = msg.get("jobId")
            code = msg.get("code", "")
            try:
                value = session.execute(code)
                evt_queue.put({"type": "job:result", "jobId": job_id, "value": value})
                evt_queue.put({
                    "type": "scope:snapshot", "jobId": job_id,
                    "snapshot": session.snapshot(),
                })
                evt_queue.put({
                    "type": "workspace:update", "jobId": job_id,
                    "scope": session.display_scope(),
                })
            except NoveaRuntimeError as exc:
                evt_queue.put({"type": "job:error", "jobId": job_id, "message": str(exc)})
            except Exception as exc:  # noqa: BLE001
                evt_queue.put({
                    "type": "job:error", "jobId": job_id,
                    "message": f"Erro interno: {exc}\n{traceback.format_exc(limit=2)}",
                })
            finally:
                evt_queue.put({"type": "job:done", "jobId": job_id})

        elif msg_type == "rehydrate":
            session.rehydrate(msg.get("snapshot"))
            evt_queue.put({"type": "workspace:update", "scope": session.display_scope()})
