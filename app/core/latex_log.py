"""Resumo de um log do pdflatex/biber: erros e avisos com ficheiro e linha."""

import re

_FILE_OPEN = re.compile(r"\(\./([^\s()]+?\.(?:tex|sty|cls|bib|bbl))")
_LINE_REF = re.compile(r"^l\.(\d+)")
_WARNING = re.compile(r"^((?:LaTeX|Package [\w-]+|Class [\w-]+) Warning): (.*)")
_INPUT_LINE = re.compile(r"on input line (\d+)")


def _current_file(log_lines: list[str], idx: int):
    """Último ficheiro aberto antes da linha idx (aproximação)."""
    for i in range(idx, max(-1, idx - 400), -1):
        found = _FILE_OPEN.findall(log_lines[i])
        if found:
            return found[-1]
    return None


def parse_log(log: str, limit: int = 30) -> dict:
    lines = (log or "").splitlines()
    errors, warnings = [], []
    overfull = 0

    for i, line in enumerate(lines):
        if line.startswith("! "):
            msg = line[2:].strip()
            line_no = None
            for nxt in lines[i + 1:i + 15]:
                m = _LINE_REF.match(nxt)
                if m:
                    line_no = int(m.group(1))
                    msg += "  →  " + nxt[m.end():].strip()[:120]
                    break
            errors.append({"file": _current_file(lines, i), "line": line_no, "message": msg})

        elif line.startswith("ERROR - "):
            errors.append({"file": None, "line": None, "message": "biber: " + line[8:].strip()})

        elif line.startswith(("Overfull", "Underfull")):
            overfull += 1

        else:
            m = _WARNING.match(line)
            if m:
                # Avisos ocupam várias linhas: junta até à linha em branco.
                text = m.group(2)
                for nxt in lines[i + 1:i + 5]:
                    if not nxt.strip():
                        break
                    text += " " + nxt.strip()
                lm = _INPUT_LINE.search(text)
                warnings.append({
                    "file": _current_file(lines, i),
                    "line": int(lm.group(1)) if lm else None,
                    "message": f"{m.group(1)}: {text.strip()}"[:300],
                })

    return {
        "errors": errors[:limit],
        "warnings": warnings[:limit],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "bad_boxes": overfull,
    }


def format_summary(parsed: dict) -> str:
    def fmt(item):
        where = item["file"] or "?"
        if item["line"]:
            where += f":{item['line']}"
        return f"- [{where}] {item['message']}"

    out = [f"{parsed['error_count']} error(s), {parsed['warning_count']} warning(s), {parsed['bad_boxes']} over/underfull box(es)."]
    if parsed["errors"]:
        out.append("\nErrors:")
        out += [fmt(e) for e in parsed["errors"]]
    if parsed["warnings"]:
        out.append("\nWarnings:")
        out += [fmt(w) for w in parsed["warnings"]]
    return "\n".join(out)
