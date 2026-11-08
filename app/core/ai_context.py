"""
Texto de um projeto pronto a colar num chat de IA (botão "Copy for AI") ou a
devolver por MCP: árvore de ficheiros + conteúdo dos ficheiros de texto.
"""

import os
import re

from core.storage import is_binary_content

# Limite do texto gerado (≈ 375k tokens). Acima disto nenhum chat aceita.
MAX_CONTEXT_CHARS = 1_500_000

_LANG = {
    ".tex": "latex", ".cls": "latex", ".sty": "latex", ".ltx": "latex",
    ".bib": "bibtex", ".bst": "bibtex",
    ".md": "markdown", ".py": "python", ".m": "matlab", ".js": "javascript",
    ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".sh": "bash",
    ".csv": "csv", ".txt": "text",
}


def fence_lang(filename: str) -> str:
    return _LANG.get(os.path.splitext(filename)[1].lower(), "")


def fenced(filename: str, content: str) -> str:
    """Bloco de código markdown; a cerca cresce se o conteúdo tiver ```."""
    longest = max((len(m) for m in re.findall(r"`{3,}", content)), default=2)
    fence = "`" * (longest + 1)
    body = content if content.endswith("\n") else content + "\n"
    return f"{fence}{fence_lang(filename)}\n{body}{fence}\n"


def in_scope(filename: str, path: str) -> bool:
    """`path` vazio = projeto todo; senão uma pasta ou um ficheiro."""
    if not path:
        return True
    path = path.strip("/")
    return filename == path or filename.startswith(path + "/")


def render_tree(rows: list[dict], root_label: str) -> str:
    """Árvore estilo `tree` a partir de [{filename, is_main, binary}]."""
    root: dict = {}

    for r in rows:
        node = root
        parts = r["filename"].split("/")
        for p in parts[:-1]:
            node = node.setdefault(p + "/", {})
        if parts[-1] != ".keep":
            node[parts[-1]] = r

    key = lambda k: (not k.endswith("/"), k.lower())
    lines = [root_label]

    def walk(node: dict, prefix: str):
        names = sorted(node, key=key)
        for i, name in enumerate(names):
            last = i == len(names) - 1
            child = node[name]
            label = name

            if not name.endswith("/"):
                tags = []
                if child.get("is_main"):
                    tags.append("main")
                if child.get("binary"):
                    tags.append("binary")
                if tags:
                    label += f"  ({', '.join(tags)})"

            lines.append(prefix + ("└── " if last else "├── ") + label)

            if name.endswith("/"):
                walk(child, prefix + ("    " if last else "│   "))

    walk(root, "")
    return "\n".join(lines)


async def build_context(cur, project_id: int, mode: str = "full", path: str = "") -> dict:
    """
    mode: "tree" (só estrutura) ou "full" (estrutura + ficheiros de texto).
    path: pasta ou ficheiro a incluir (vazio = projeto todo).
    """
    await cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
    project = await cur.fetchone()

    await cur.execute(
        """
        SELECT filename, is_main, LEFT(content, 200) AS head, CHAR_LENGTH(content) AS size
        FROM project_files WHERE project_id = %s ORDER BY filename
        """,
        (project_id,),
    )
    all_rows = await cur.fetchall()

    rows = [
        {
            "filename": r["filename"],
            "is_main": bool(r["is_main"]),
            "binary": is_binary_content(r["head"] or ""),
            "size": r["size"] or 0,
        }
        for r in all_rows
        if in_scope(r["filename"], path)
    ]

    main = next((r["filename"] for r in all_rows if r["is_main"]), None)
    name = project["name"] if project else f"project {project_id}"

    out = [f"# LaTeX project: {name}"]
    if main:
        out.append(f"Main file: `{main}`")
    if path:
        out.append(f"Scope: `{path.strip('/')}`")
    out.append("")
    out.append("## File tree")
    out.append("```")
    out.append(render_tree(rows, (path.strip("/") + "/") if path else "./"))
    out.append("```")

    included, skipped = 0, []

    if mode == "full":
        out.append("")
        out.append("## Files")
        total = sum(len(x) + 1 for x in out)

        text_rows = [r for r in rows if not r["binary"] and not r["filename"].endswith("/.keep") and r["filename"] != ".keep"]
        # Ficheiro principal primeiro, depois por caminho.
        text_rows.sort(key=lambda r: (not r["is_main"], r["filename"].lower()))

        for r in text_rows:
            if total + r["size"] > MAX_CONTEXT_CHARS:
                skipped.append(r["filename"])
                continue

            await cur.execute(
                "SELECT content FROM project_files WHERE project_id = %s AND filename = %s",
                (project_id, r["filename"]),
            )
            row = await cur.fetchone()
            content = (row or {}).get("content") or ""

            block = f"\n### {r['filename']}\n" + fenced(r["filename"], content)
            out.append(block)
            total += len(block)
            included += 1

        if skipped:
            out.append(f"\n(Not included, too large: {', '.join(skipped)})")

    text = "\n".join(out).rstrip() + "\n"

    return {
        "text": text,
        "files": len(rows),
        "included": included,
        "skipped": skipped,
        "chars": len(text),
    }
