"""
NôveaTex — Tradutor de sintaxe estilo MATLAB/Nôvea para Python + NumPy.

Substitui o antigo `sessionWorker.js` (que delegava a avaliação de expressões
ao mathjs, correndo dentro de um worker_thread do Node). Aqui não há nenhuma
biblioteca de expressões pronta como o mathjs, por isso este módulo faz um
trabalho equivalente: normaliza a sintaxe do dialeto (`.^`, `.*`, `./`,
transposição com `'`, literais de matriz `[1, 2; 3, 4]`, intervalos `a:b:c`)
para expressões Python válidas, que depois são avaliadas com `eval`/`exec`
usando o "scope" do utilizador como dicionário de variáveis locais.

Limitações conhecidas (tal como no original, isto é um MVP, não um parser
completo de MATLAB):
  - Não há indexação `A(i)` — os parênteses servem apenas para chamadas de
    função e agrupamento. Os colchetes `[...]` são sempre literais de matriz.
  - Strings devem preferencialmente usar aspas duplas (`"texto"`), porque a
    plica simples (`'`) é interpretada como operador de transposição sempre
    que aparece logo a seguir a um identificador, número ou `)`/`]`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Deteção de statements de controlo de fluxo
# ---------------------------------------------------------------------------

FOR_RE = re.compile(r"^for\s+([A-Za-z_]\w*)\s*=\s*(.+)$")
WHILE_RE = re.compile(r"^while\s+(.+)$")
IF_RE = re.compile(r"^if\s+(.+)$")
ELSEIF_RE = re.compile(r"^elseif\s+(.+)$")
ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)\s*=(?!=)\s*(.+)$")


class NoveaSyntaxError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 1. Separar código em statements (junta matrizes multi-linha, tira comentários)
# ---------------------------------------------------------------------------

def split_statements(code: str) -> List[str]:
    # remove comentários (%...) preservando strings simples seria ideal, mas
    # tal como no original mantemos a heurística simples: '%' inicia comentário.
    lines = [re.sub(r"%.*$", "", line) for line in code.split("\n")]

    statements: List[str] = []
    current = ""
    depth = 0
    for line in lines:
        trimmed = line.strip()
        if not trimmed and depth == 0:
            continue
        for ch in line:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
        current = (current + "\n" + line) if current else line
        if depth <= 0:
            statements.append(current.strip())
            current = ""
            depth = 0
    if current.strip():
        statements.append(current.strip())
    return [s for s in statements if s.strip()]


# ---------------------------------------------------------------------------
# 2. Utilitários de parsing de expressões (split respeitando parênteses/colchetes)
# ---------------------------------------------------------------------------

def _split_top_level(s: str, sep: str) -> List[str]:
    parts: List[str] = []
    depth = 0
    cur = ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _find_matching(s: str, start: int, open_c: str, close_c: str) -> int:
    depth = 0
    for k in range(start, len(s)):
        if s[k] == open_c:
            depth += 1
        elif s[k] == close_c:
            depth -= 1
            if depth == 0:
                return k
    raise NoveaSyntaxError("Colchete/parêntese não fechado na expressão.")


# ---------------------------------------------------------------------------
# 3. Literais de matriz: [1, 2; 3, 4]  →  np.array([[1, 2], [3, 4]])
# ---------------------------------------------------------------------------

def _matrix_from_inner(inner: str) -> str:
    inner = inner.strip()
    if not inner:
        return "np.array([])"
    rows = _split_top_level(inner, ";")
    row_reprs = []
    for row in rows:
        row = row.strip()
        cols = _split_top_level(row, ",") if "," in row else row.split()
        cols = [_convert_matrix_literals(c.strip()) for c in cols if c.strip()]
        row_reprs.append("[" + ", ".join(cols) + "]")
    if len(row_reprs) == 1:
        return f"np.array({row_reprs[0]})"
    return "np.array([" + ", ".join(row_reprs) + "])"


def _convert_matrix_literals(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "[":
            j = _find_matching(s, i, "[", "]")
            out.append(_matrix_from_inner(s[i + 1:j]))
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# 4. Transposição: X'  →  novea_T(X)      (X)'  →  novea_T((X))
# ---------------------------------------------------------------------------

_IDENT_TAIL_RE = re.compile(r"([A-Za-z_]\w*(?:\.\w+)*)$")


def _wrap_transpose(s: str) -> str:
    out = ""
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "'" and out:
            m = _IDENT_TAIL_RE.search(out)
            if m:
                start = m.start()
                ident = m.group(1)
                out = out[:start] + f"novea_T({ident})"
                i += 1
                continue
            if out.endswith(")"):
                depth = 0
                k = len(out) - 1
                while k >= 0:
                    if out[k] == ")":
                        depth += 1
                    elif out[k] == "(":
                        depth -= 1
                        if depth == 0:
                            break
                    k -= 1
                if k >= 0:
                    out = out[:k] + f"novea_T({out[k:]})"
                    i += 1
                    continue
        out += c
        i += 1
    return out


# ---------------------------------------------------------------------------
# 5. Ponto de entrada: expressão do dialeto → expressão Python válida
# ---------------------------------------------------------------------------

def to_python_expr(expr: str) -> str:
    s = _convert_matrix_literals(expr)
    s = s.replace(".^", "**").replace(".*", "*").replace("./", "/")
    s = re.sub(r"(?<!\*)\^(?!\*)", "**", s)
    s = _wrap_transpose(s)
    return s


# ---------------------------------------------------------------------------
# 6. Parser de blocos (for / while / if-elseif-else / end) → árvore de nós
# ---------------------------------------------------------------------------

Node = Dict[str, Any]


def parse_block(stmts: List[str], i: int) -> Tuple[List[Node], int, str]:
    nodes: List[Node] = []
    n = len(stmts)
    while i < n:
        raw = stmts[i]
        s = raw.rstrip(";").strip()

        if s == "end":
            return nodes, i + 1, "end"
        if s == "else" or ELSEIF_RE.match(s):
            return nodes, i, "branch"

        m = FOR_RE.match(s)
        if m:
            body, i, _ = parse_block(stmts, i + 1)
            nodes.append({"type": "for", "var": m.group(1), "range": m.group(2), "body": body})
            continue

        m = WHILE_RE.match(s)
        if m:
            body, i, _ = parse_block(stmts, i + 1)
            nodes.append({"type": "while", "cond": m.group(1), "body": body})
            continue

        m = IF_RE.match(s)
        if m:
            branches = []
            body, i, term = parse_block(stmts, i + 1)
            branches.append((m.group(1), body))
            else_body = None
            while term == "branch":
                s2 = stmts[i].rstrip(";").strip()
                if s2 == "else":
                    else_body, i, term = parse_block(stmts, i + 1)
                    break
                m2 = ELSEIF_RE.match(s2)
                if not m2:
                    raise NoveaSyntaxError(f"Estrutura if/elseif/else malformada perto de: {s2}")
                body2, i, term = parse_block(stmts, i + 1)
                branches.append((m2.group(1), body2))
            nodes.append({"type": "if", "branches": branches, "else": else_body})
            continue

        nodes.append({"type": "stmt", "code": raw})
        i += 1

    return nodes, i, "eof"


def parse_program(code: str) -> List[Node]:
    stmts = split_statements(code)
    nodes, _, _ = parse_block(stmts, 0)
    return nodes
