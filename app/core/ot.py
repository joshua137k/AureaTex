"""
Operational Transform simplificado para texto simples.

Tipos de operação:
  {"type": "insert", "pos": int, "text": str}
  {"type": "delete", "pos": int, "len": int}
  {"type": "cursor", "pos": int, "user_id": int, "username": str, "color": str}

Cada cliente envia a sua operação com a revision que tinha quando a criou.
O servidor transforma a operação contra todas as que foram aplicadas
desde essa revision, depois aplica-a e faz broadcast a todos.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class Op:
    type: Literal["insert", "delete", "cursor"]
    pos: int
    text: str = ""          # insert
    length: int = 0         # delete
    user_id: int = 0        # cursor
    username: str = ""      # cursor
    color: str = ""         # cursor

    def to_dict(self) -> dict:
        d = {"type": self.type, "pos": self.pos}
        if self.type == "insert":
            d["text"] = self.text
        elif self.type == "delete":
            d["len"] = self.length
        elif self.type == "cursor":
            d["user_id"]  = self.user_id
            d["username"] = self.username
            d["color"]    = self.color
        return d

    @staticmethod
    def from_dict(d: dict) -> "Op":
        return Op(
            type     = d["type"],
            pos      = d.get("pos", 0),
            text     = d.get("text", ""),
            length   = d.get("len",  0),
            user_id  = d.get("user_id",  0),
            username = d.get("username", ""),
            color    = d.get("color",    ""),
        )


# ---------------------------------------------------------------------------
# Transformação: transform(op_client, op_server) → op_client'
# Devolve a versão de op_client transformada para ser aplicada
# DEPOIS de op_server já ter sido aplicada.
# ---------------------------------------------------------------------------

def transform(client: Op, server: Op) -> Op:
    """Transforma client contra server. Devolve client'."""

    if client.type == "cursor" or server.type == "cursor":
        return client  # cursores não afetam o conteúdo

    if client.type == "insert":
        return _transform_insert_vs(client, server)
    elif client.type == "delete":
        return _transform_delete_vs(client, server)
    return client


def _transform_insert_vs(client: Op, server: Op) -> Op:
    pos = client.pos
    if server.type == "insert":
        if server.pos < pos or (server.pos == pos and server.user_id < client.user_id):
            pos += len(server.text)
    elif server.type == "delete":
        if server.pos < pos:
            pos = max(server.pos, pos - server.length)
    return Op(type="insert", pos=pos, text=client.text, user_id=client.user_id)


def _transform_delete_vs(client: Op, server: Op) -> Op:
    pos    = client.pos
    length = client.length

    if server.type == "insert":
        if server.pos <= pos:
            pos += len(server.text)
    elif server.type == "delete":
        s, sl = server.pos, server.length
        if s + sl <= pos:
            # server delete antes do nosso — deslocamos
            pos -= sl
        elif s >= pos + length:
            # server delete depois do nosso — sem efeito
            pass
        else:
            # sobreposição: recalcula posição e comprimento
            overlap_start  = max(s, pos)
            overlap_end    = min(s + sl, pos + length)
            overlap        = max(0, overlap_end - overlap_start)
            pos    = min(pos, s)
            length = max(0, length - overlap)

    return Op(type="delete", pos=pos, length=length, user_id=client.user_id)


# ---------------------------------------------------------------------------
# Documento em memória (por projeto + ficheiro)
# ---------------------------------------------------------------------------

@dataclass
class DocState:
    content:   str        = ""
    revision:  int        = 0
    history:   list[Op]   = field(default_factory=list)   # ops aplicadas

    def apply(self, op: Op) -> None:
        """Aplica op ao conteúdo e guarda no histórico."""
        if op.type == "insert":
            self.content = self.content[:op.pos] + op.text + self.content[op.pos:]
        elif op.type == "delete":
            end = min(op.pos + op.length, len(self.content))
            self.content = self.content[:op.pos] + self.content[end:]
        # cursor não altera conteúdo
        self.history.append(op)
        self.revision += 1

    def transform_since(self, op: Op, since_revision: int) -> Op:
        """Transforma op contra todas as ops desde since_revision."""
        for server_op in self.history[since_revision:]:
            op = transform(op, server_op)
        return op