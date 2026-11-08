"""
Execução do LaTeX (pdflatex + bibtex/biber) com limites de tempo e recursos.
"""

import asyncio
import hashlib
import os
import resource
import signal
import time

from core.config import COMPILE_TIMEOUT_SECS

# Extensões que, se mudarem entre passagens, obrigam a compilar outra vez.
_RERUN_EXTS = (".aux", ".toc", ".lof", ".lot", ".out", ".nav", ".bbl", ".idx", ".glo")

MAX_PASSES = 4


class CompileTimeout(Exception):
    def __init__(self, log: str):
        super().__init__("timeout")
        self.log = log


class UnsafeBibliography(Exception):
    """O .bcf pede ao biber um ficheiro fora do projeto (ou remoto)."""


# Ao contrário do pdflatex e do bibtex, o biber NÃO respeita openin_any:
# \addbibresource{/projetos/<outro>/main.tex} lia ficheiros de outros
# utilizadores. Antes de o correr, valida-se cada fonte de dados do .bcf.
def check_bcf(project_path: str, base_name: str) -> None:
    import xml.etree.ElementTree as ET

    bcf = os.path.join(project_path, f"{base_name}.bcf")
    base = os.path.realpath(project_path)

    try:
        root = ET.parse(bcf).getroot()
    except (ET.ParseError, OSError):
        raise UnsafeBibliography("Ficheiro .bcf inválido.")

    for el in root.iter():
        tag = el.tag.split("}")[-1]

        # Opções que mudariam onde o biber lê ou escreve ficheiros.
        if tag == "key" and (el.text or "").strip().lower() in (
            "input_directory", "output_directory", "configfile", "tool", "outfile",
        ):
            raise UnsafeBibliography("Opção de biber não permitida no .bcf.")

        if tag != "datasource":
            continue

        path = (el.text or "").strip()

        if el.get("type", "file") != "file" or "://" in path:
            raise UnsafeBibliography(f"Fonte bibliográfica remota não permitida: {path}")

        parts = path.replace("\\", "/").split("/")
        if not path or path.startswith(("/", "~", "\\")) or ".." in parts:
            raise UnsafeBibliography(f"A bibliografia tem de estar dentro do projeto: {path}")

        full = os.path.realpath(os.path.join(base, path))
        if os.path.commonpath([base, full]) != base:
            raise UnsafeBibliography(f"A bibliografia tem de estar dentro do projeto: {path}")


# Variáveis de ambiente que o TeX precisa. O resto (JWT_SECRET, passwords da
# BD, segredos OAuth) não passa para processos que correm código do utilizador.
_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ")


def _limit_resources():
    """Corre no processo filho antes do exec."""
    # CPU: mata processos em ciclo infinito mesmo que o timeout falhe.
    resource.setrlimit(resource.RLIMIT_CPU, (COMPILE_TIMEOUT_SECS, COMPILE_TIMEOUT_SECS + 5))
    # Tamanho máximo de cada ficheiro escrito (PDF, log...): 512 MB.
    resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024, 512 * 1024 * 1024))


async def _run(cmd: list, cwd: str, timeout: float, main_dir: str = "") -> str:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env["HOME"] = "/tmp"
    env["openin_any"] = "p"
    env["openout_any"] = "p"

    # Ficheiro principal numa subpasta (ex.: ZIP com uma pasta a envolver tudo):
    # compila-se na raiz do projeto, mas os \input/\includegraphics/.bib
    # relativos à pasta do ficheiro principal também são encontrados.
    if main_dir and ":" not in main_dir:
        for var in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
            env[var] = f".:./{main_dir}:" + env.get(var, "")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=_limit_resources,
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1.0, timeout))

    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Mata o grupo inteiro: o wait_for sozinho deixava o pdflatex a correr.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass

        raise

    return stdout.decode("utf-8", errors="replace")


def _state_hash(project_path: str) -> str:
    """Hash dos ficheiros auxiliares — muda quando é preciso outra passagem."""
    h = hashlib.md5()

    for root, dirs, files in os.walk(project_path):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))

        for name in sorted(files):
            if not name.endswith(_RERUN_EXTS):
                continue

            path = os.path.join(root, name)

            try:
                with open(path, "rb") as fh:
                    h.update(name.encode())
                    h.update(fh.read())
            except OSError:
                pass

    return h.hexdigest()


def _bib_tool(project_path: str, base_name: str):
    """Devolve o comando de bibliografia necessário, ou None."""
    if os.path.exists(os.path.join(project_path, f"{base_name}.bcf")):
        # --noconf: um biber.conf posto no projeto não pode mudar as regras.
        return ["biber", "--noconf", base_name]

    aux = os.path.join(project_path, f"{base_name}.aux")

    try:
        with open(aux, "r", encoding="utf-8", errors="replace") as fh:
            if "\\bibdata" in fh.read():
                return ["bibtex", base_name]
    except OSError:
        pass

    return None


def output_pdf_name(main_file: str) -> str:
    """O PDF fica sempre na raiz do projeto, com o nome do ficheiro principal."""
    return os.path.splitext(os.path.basename(main_file))[0] + ".pdf"


async def compile_latex(project_path: str, main_file: str, synctex: bool = True) -> tuple[str, bool]:
    """
    Compila main_file dentro de project_path.

    Devolve (log, success). O log é o da última passagem do pdflatex, com o
    log da bibliografia no fim. Lança CompileTimeout se exceder o tempo.
    O PDF fica em project_path/output_pdf_name(main_file).

    Em vez de correr sempre pdflatex 3x + bibtex, repete só enquanto os
    ficheiros auxiliares mudarem (índice, referências, citações).
    """
    deadline = time.monotonic() + COMPILE_TIMEOUT_SECS
    base_name = os.path.splitext(os.path.basename(main_file))[0]
    main_dir = os.path.dirname(main_file)
    pdf_path = os.path.join(project_path, output_pdf_name(main_file))

    cmd = ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode"]
    if synctex:
        cmd.append("-synctex=1")
    cmd += ["--", main_file]

    def pdf_stamp():
        try:
            st = os.stat(pdf_path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    stamp_before = pdf_stamp()
    log = ""
    bib_log = ""

    def remaining() -> float:
        return deadline - time.monotonic()

    try:
        before = await asyncio.to_thread(_state_hash, project_path)
        log = await _run(cmd, project_path, remaining(), main_dir)
        passes = 1

        bib_cmd = await asyncio.to_thread(_bib_tool, project_path, base_name)

        if bib_cmd and bib_cmd[0] == "biber":
            try:
                await asyncio.to_thread(check_bcf, project_path, base_name)
            except UnsafeBibliography as e:
                bib_log = f"ERROR - {e}\nBiber não foi executado.\n"
                bib_cmd = None

        if bib_cmd:
            bib_log = await _run(bib_cmd, project_path, remaining(), main_dir)

        while passes < MAX_PASSES:
            after = await asyncio.to_thread(_state_hash, project_path)

            if after == before and "Rerun to get" not in log and "Rerun LaTeX" not in log:
                break

            before = after
            log = await _run(cmd, project_path, remaining(), main_dir)
            passes += 1

    except asyncio.TimeoutError:
        raise CompileTimeout(log)

    if bib_log:
        log += f"\n--- BIBLIOGRAPHY LOG ---\n{bib_log}\n"

    # Só conta como sucesso um PDF gerado NESTA compilação (antes devolvia-se
    # o PDF antigo quando a compilação falhava por completo).
    stamp_after = pdf_stamp()
    success = stamp_after is not None and stamp_after != stamp_before

    return log, success
