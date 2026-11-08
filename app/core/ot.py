"""
Operational Transform simplificado para texto simples.

Tipos de operação:
  {"type": "insert", "pos": int, "text": str}
  {"type": "delete", "pos": int, "len": int}

Cada cliente envia a sua operação com a revision que tinha quando a criou.
O servidor transforma a operação contra todas as que foram aplicadas
desde essa revision, depois aplica-a e faz broadcast a todos.

As posições são offsets em unidades UTF-16 (é o que o Monaco/JavaScript usa),
por isso o DocState converte-as quando o texto tem caracteres fora do BMP
(emoji, etc.).

Regras de empate (têm de ser iguais às do cliente em collab.js):
  - dois inserts na mesma posição: fica primeiro o do utilizador com id menor;
  - insert estritamente dentro de um intervalo apagado em simultâneo: o insert
    é engolido pelo delete (dos dois lados).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MAX_HISTORY = 2000


class StaleRevision(Exception):
    """O cliente está demasiado atrasado (ou adiantado) — tem de ressincronizar."""


class InvalidOp(Exception):
    pass


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class Op:
    type: Literal["insert", "delete"]
    pos: int
    text: str = ""          # insert
    length: int = 0         # delete (unidades UTF-16)
    user_id: int = 0

    def to_dict(self) -> dict:
        d = {"type": self.type, "pos": self.pos}
        if self.type == "insert":
            d["text"] = self.text
        else:
            d["len"] = self.length
        return d

    @staticmethod
    def from_dict(d: dict) -> "Op":
        if not isinstance(d, dict):
            raise InvalidOp("op inválida")

        op_type = d.get("type")
        pos = d.get("pos", 0)

        if op_type not in ("insert", "delete"):
            raise InvalidOp("tipo de op desconhecido")

        if not isinstance(pos, int) or isinstance(pos, bool) or pos < 0:
            raise InvalidOp("posição inválida")

        if op_type == "insert":
            text = d.get("text", "")
            if not isinstance(text, str):
                raise InvalidOp("texto inválido")
            return Op(type="insert", pos=pos, text=text)

        length = d.get("len", 0)
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise InvalidOp("comprimento inválido")

        return Op(type="delete", pos=pos, length=length)


def u16len(text: str) -> int:
    """Comprimento em unidades UTF-16."""
    if text.isascii():
        return len(text)
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


# ---------------------------------------------------------------------------
# Transformação: transform(client, server) → client'
# Devolve a versão de `client` para ser aplicada DEPOIS de `server`.
# ---------------------------------------------------------------------------

def transform(client: Op, server: Op) -> Op:
    if client.type == "insert":
        return _transform_insert_vs(client, server)
    return _transform_delete_vs(client, server)


def _transform_insert_vs(client: Op, server: Op) -> Op:
    pos = client.pos
    text = client.text

    if server.type == "insert":
        if server.pos < pos or (server.pos == pos and server.user_id < client.user_id):
            pos += u16len(server.text)

    else:
        s, sl = server.pos, server.length

        if s < pos:
            if pos < s + sl:
                # insert dentro do intervalo apagado — é engolido
                pos = s
                text = ""
            else:
                pos -= sl

    return Op(type="insert", pos=pos, text=text, user_id=client.user_id)


def _transform_delete_vs(client: Op, server: Op) -> Op:
    pos = client.pos
    length = client.length

    if server.type == "insert":
        if server.pos <= pos:
            pos += u16len(server.text)
        elif server.pos < pos + length:
            # insert dentro do nosso intervalo — o delete engole-o
            length += u16len(server.text)

    else:
        s, sl = server.pos, server.length

        if s + sl <= pos:
            pos -= sl
        elif s >= pos + length:
            pass
        else:
            overlap = min(s + sl, pos + length) - max(s, pos)
            pos = min(pos, s)
            length = max(0, length - overlap)

    return Op(type="delete", pos=pos, length=length, user_id=client.user_id)


# ---------------------------------------------------------------------------
# Documento em memória (por projeto + ficheiro)
# ---------------------------------------------------------------------------

def _has_astral(text: str) -> bool:
    return (not text.isascii()) and any(ord(ch) > 0xFFFF for ch in text)


@dataclass
class DocState:
    content:  str = ""
    revision: int = 0

    # Ops aplicadas desde `base_revision` (history[i] levou de base+i a base+i+1).
    history: list[Op] = field(default_factory=list)
    base_revision: int = 0

    # True quando há alterações em memória ainda não gravadas na BD.
    dirty: bool = False
    last_editor: int | None = None

    _astral: bool = False

    def set_content(self, content: str) -> None:
        self.content = content
        self._astral = _has_astral(content)

    def reset(self, content: str) -> None:
        """Substitui o conteúdo (save externo). Invalida o histórico OT."""
        self.set_content(content)
        self.revision += 1
        self.history.clear()
        self.base_revision = self.revision
        self.dirty = False

    def length(self) -> int:
        return u16len(self.content) if self._astral else len(self.content)

    def _index(self, u16pos: int) -> int:
        """Converte offset UTF-16 em índice de code point."""
        if not self._astral:
            return min(u16pos, len(self.content))

        units = 0
        for i, ch in enumerate(self.content):
            if units >= u16pos:
                return i
            units += 2 if ord(ch) > 0xFFFF else 1

        return len(self.content)

    def apply(self, op: Op) -> None:
        """Aplica op ao conteúdo e guarda no histórico."""
        if op.pos > self.length():
            raise InvalidOp("posição fora do documento")

        if op.type == "insert":
            if op.text:
                i = self._index(op.pos)
                self.content = self.content[:i] + op.text + self.content[i:]

                if not self._astral and _has_astral(op.text):
                    self._astral = True

        else:
            if op.length:
                start = self._index(op.pos)
                end = self._index(op.pos + op.length)
                self.content = self.content[:start] + self.content[end:]

        self.history.append(op)
        self.revision += 1
        self.dirty = True

        if len(self.history) > MAX_HISTORY:
            drop = len(self.history) - MAX_HISTORY // 2
            del self.history[:drop]
            self.base_revision += drop

    def transform_since(self, op: Op, since_revision: int) -> Op:
        """Transforma op contra todas as ops desde since_revision."""
        if since_revision > self.revision or since_revision < self.base_revision:
            raise StaleRevision()

        for server_op in self.history[since_revision - self.base_revision:]:
            op = transform(op, server_op)

        return op
