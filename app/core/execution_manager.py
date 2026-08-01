"""
NôveaTex — Gestor de sessões de execução de código.

Equivalente Python ao antigo `server/sessionManager.js`: mantém um processo
(`multiprocessing.Process`) por ligação ativa, reencaminha jobs, e reinicia
o processo em caso de timeout, cancelamento, ou crash — preservando o último
snapshot do workspace para "rehydration" imediata, tal como o original.
"""

from __future__ import annotations

import multiprocessing as mp
from typing import Optional

from .kernel.worker import worker_main

# Tempo máximo (segundos) que um job pode correr antes do kernel ser reiniciado
# à força — protege contra `while True` etc. vindos do script do utilizador.
JOB_TIMEOUT_SECONDS = 20


class ExecutionSession:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self.last_snapshot: Optional[str] = None
        self.cmd_queue: mp.Queue = mp.Queue()
        self.evt_queue: mp.Queue = mp.Queue()
        self.process: Optional[mp.Process] = None
        self._start_process()

    def _start_process(self) -> None:
        self.process = mp.Process(
            target=worker_main,
            args=(self.cmd_queue, self.evt_queue, self.workspace_dir),
            daemon=True,
        )
        self.process.start()
        if self.last_snapshot:
            self.cmd_queue.put({"type": "rehydrate", "snapshot": self.last_snapshot})

    def execute(self, job_id: str, code: str) -> None:
        self.cmd_queue.put({"type": "execute", "jobId": job_id, "code": code})

    def restart(self) -> None:
        """Reinicia o kernel (equivalente a `restartWorker` no original)."""
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
        self.cmd_queue = mp.Queue()
        self.evt_queue = mp.Queue()
        self._start_process()

    def close(self) -> None:
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
