"""
NôveaTex — Motor de execução do dialeto Nôvea.

Equivalente Python ao antigo `server/workers/sessionWorker.js` do Nôvea.
Cada `Session` mantém um "workspace" (dicionário de variáveis) que persiste
entre execuções, tal como o `scope` do worker Node original, e expõe as
mesmas funções de conveniência (plot, imread, fft, etc.), agora implementadas
com NumPy / Pillow / SciPy em vez de mathjs / pngjs / node-wav.
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .translator import ASSIGN_RE, NoveaSyntaxError, _split_top_level, parse_program, to_python_expr

# ---------------------------------------------------------------------------
# Builtins seguros expostos ao `eval`/`exec` do utilizador
# ---------------------------------------------------------------------------

_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "len": len, "range": range,
    "round": round, "sum": sum, "list": list, "float": float, "int": int,
    "str": str, "bool": bool, "True": True, "False": False, "None": None,
    "enumerate": enumerate, "zip": zip, "sorted": sorted,
}

_ELEMENTWISE_FUNCS = [
    "sin", "cos", "tan", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
    "exp", "log", "log10", "log2", "sqrt", "abs", "round", "floor", "ceil",
    "sign", "square",
]

# Aliases MATLAB → NumPy (nomes que o utilizador escreve no script)
_ALIASES = {
    "asin": "arcsin", "acos": "arccos", "atan": "arctan",
}


def to_plain(value: Any) -> Any:
    """Converte estruturas NumPy em tipos nativos de Python (serializáveis em JSON)."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, complex):
        sign = "+" if value.imag >= 0 else ""
        return f"{value.real:.6g}{sign}{value.imag:.6g}i"
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    return value


def format_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    plain = to_plain(value)
    if isinstance(plain, float):
        return f"{plain:.8g}"
    if isinstance(plain, list):
        return json.dumps(plain)
    return str(plain)


class NoveaRuntimeError(RuntimeError):
    pass


class Session:
    """Uma sessão de execução — o equivalente ao `scope` global do worker Node."""

    def __init__(self, workspace_dir: str, emit: Callable[[str, dict], None]):
        self.workspace_dir = workspace_dir
        self.emit = emit
        self.scope: Dict[str, Any] = {}
        self.plot_traces: List[dict] = []
        self.plot_layout: dict = {}
        self._install_math_functions()

    # -- Instalação de funções elementwise e utilitários gerais -----------

    def _install_math_functions(self) -> None:
        for name in _ELEMENTWISE_FUNCS:
            fn = getattr(np, name, None)
            if fn is not None:
                self.scope[name] = fn
        for alias, target in _ALIASES.items():
            self.scope[alias] = getattr(np, target)

        self.scope["linspace"] = lambda start, end, n=100: np.linspace(start, end, int(round(n)))
        self.scope["zeros"] = lambda *dims: np.zeros(dims if len(dims) > 1 else dims[0])
        self.scope["ones"] = lambda *dims: np.ones(dims if len(dims) > 1 else dims[0])
        self.scope["length"] = lambda v: int(np.asarray(v).size) if hasattr(v, "__len__") or isinstance(v, np.ndarray) else 1
        self.scope["size"] = lambda v: list(np.asarray(v).shape)
        self.scope["mean"] = np.mean
        self.scope["std"] = np.std
        self.scope["sum"] = np.sum
        self.scope["max"] = np.max
        self.scope["min"] = np.min
        self.scope["pi"] = np.pi
        self.scope["e"] = np.e

    # -- Funções específicas de uma execução (plot, imagens, áudio) -------

    def _install_execution_functions(self) -> None:
        s = self.scope

        def plot(x, y=None, **opts):
            if y is None:
                y, x = x, np.arange(len(np.atleast_1d(x)))
            px, py = to_plain(x), to_plain(y)
            px = px if isinstance(px, list) else [px]
            py = py if isinstance(py, list) else [py]
            mode = opts.get("mode") or ("markers" if len(px) == 1 else "lines")
            
            trace = {
                "x": px, "y": py, "type": "scatter", "mode": mode,
                "name": f"série {len(self.plot_traces) + 1}"
            }
            if mode == "markers":
                trace["marker"] = {"size": 12}
                
            self.plot_traces.append(trace)
            return None

        def plot3(x, y, z, **opts):
            mode = opts.get("mode", "lines")
            trace = {
                "x": to_plain(x), "y": to_plain(y), "z": to_plain(z),
                "type": "scatter3d", "mode": mode,
                "name": f"série 3D {len(self.plot_traces) + 1}"
            }
            if mode == "markers":
                trace["marker"] = {"size": 4}
                
            self.plot_traces.append(trace)
            return None

        def surf(x, y=None, z=None):
            actual_z = z if z is not None else x
            actual_x = x if z is not None else None
            actual_y = y if z is not None else None
            
            trace = {
                "z": to_plain(actual_z), "type": "surface",
                "name": f"superfície {len(self.plot_traces) + 1}"
            }
            if actual_x is not None: trace["x"] = to_plain(actual_x)
            if actual_y is not None: trace["y"] = to_plain(actual_y)
                
            self.plot_traces.append(trace)
            return None

        def clf():
            self.plot_traces.clear()
            return None

        def drawnow():
            self.emit("job:plot", {"traces": list(self.plot_traces), "layout": dict(self.plot_layout)})
            return None

        def pause(ms):
            time.sleep(max(0.0, float(ms)) / 1000.0)
            return None

        def xlim(arr):
            self.plot_layout.setdefault("xaxis", {})["range"] = to_plain(arr)
            return None

        def ylim(arr):
            self.plot_layout.setdefault("yaxis", {})["range"] = to_plain(arr)
            return None

        def title(t):
            self.plot_layout["title"] = t
            return None

        def xlabel(t):
            self.plot_layout.setdefault("xaxis", {})["title"] = t
            return None

        def ylabel(t):
            self.plot_layout.setdefault("yaxis", {})["title"] = t
            return None

        def clc():
            self.emit("job:clc", {})
            return None

        def clear():
            for key in list(self.scope.keys()):
                if not callable(self.scope[key]):
                    del self.scope[key]
            return None

        def _resolve(filename):
            path = os.path.join(self.workspace_dir, filename)
            if not os.path.abspath(path).startswith(os.path.abspath(self.workspace_dir)):
                raise NoveaRuntimeError("Acesso a ficheiro fora do projeto negado.")
            return path

        def imread(filename):
            from PIL import Image
            path = _resolve(filename)
            if not os.path.exists(path):
                raise NoveaRuntimeError(f"Imagem não encontrada: {filename}")
            img = Image.open(path).convert("RGB")
            return np.array(img)

        def rgb2gray(matrix):
            mat = np.asarray(to_plain(matrix), dtype=float)
            return mat[..., 0] * 0.299 + mat[..., 1] * 0.587 + mat[..., 2] * 0.114

        def imshow(matrix):
            mat = np.asarray(to_plain(matrix))
            is_rgb = mat.ndim == 3
            if is_rgb:
                self.plot_traces.append({"z": mat.tolist(), "type": "image", "name": "Imagem"})
            else:
                self.plot_traces.append({
                    "z": mat.tolist(), "type": "heatmap",
                    "colorscale": "Greys", "showscale": False, "name": "Imagem",
                })
            self.plot_layout.setdefault("xaxis", {})["visible"] = False
            self.plot_layout.setdefault("yaxis", {})["visible"] = False
            self.plot_layout["yaxis"]["autorange"] = "reversed"
            self.plot_layout["xaxis"]["scaleanchor"] = "y"
            return None

        def imfilter(matrix_obj, kernel_obj):
            img = np.asarray(to_plain(matrix_obj), dtype=float)
            kernel = np.asarray(to_plain(kernel_obj), dtype=float)
            kh, kw = kernel.shape
            pad_h, pad_w = kh // 2, kw // 2
            padded = np.pad(img, ((pad_h, pad_h), (pad_w, pad_w)))
            out = np.zeros_like(img)
            h, w = img.shape
            for y in range(h):
                for x in range(w):
                    region = padded[y:y + kh, x:x + kw]
                    out[y, x] = np.sum(region * kernel)
            return out

        def edge(matrix_obj, method="sobel"):
            img = np.asarray(to_plain(matrix_obj), dtype=float)
            gx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=float)
            gy = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=float)
            res_x = imfilter(img, gx)
            res_y = imfilter(img, gy)
            mag = np.sqrt(res_x ** 2 + res_y ** 2)
            return np.where(mag > 100, 255, 0)

        def imbinarize(matrix_obj, threshold=128):
            img = np.asarray(to_plain(matrix_obj), dtype=float)
            return np.where(img > threshold, 255, 0)

        def audioread(filename):
            from scipy.io import wavfile
            path = _resolve(filename)
            if not os.path.exists(path):
                raise NoveaRuntimeError(f"Áudio não encontrado: {filename}")
            rate, data = wavfile.read(path)
            self.scope["fs"] = rate
            if data.ndim > 1:
                data = data[:, 0]
            return data.astype(float)

        def fft(array_obj):
            signal = np.asarray(to_plain(array_obj), dtype=float)
            n = 2 ** int(np.floor(np.log2(max(2, signal.size))))
            sliced = signal[:n]
            spectrum = np.fft.fft(sliced)
            return np.abs(spectrum)

        def readmatrix(filename):
            path = _resolve(filename)
            if not os.path.exists(path):
                raise NoveaRuntimeError(f"Ficheiro não encontrado: {filename}")
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            return np.array([
                [float(v) for v in line.split(",")]
                for line in content.strip().splitlines()
            ])

        s.update({
            "plot": plot, "plot3": plot3, "surf": surf, "clf": clf, "drawnow": drawnow,
            "pause": pause, "xlim": xlim, "ylim": ylim, "title": title,
            "xlabel": xlabel, "ylabel": ylabel, "clc": clc, "clear": clear,
            "imread": imread, "rgb2gray": rgb2gray, "imshow": imshow,
            "imfilter": imfilter, "edge": edge, "imbinarize": imbinarize,
            "audioread": audioread, "fft": fft, "readmatrix": readmatrix,
        })

    # -- Avaliação de expressões e statements ------------------------------

    def _eval_globals(self) -> dict:
        return {"__builtins__": _SAFE_BUILTINS, "np": np, "novea_T": np.transpose}

    def _eval_expr(self, expr: str) -> Any:
        py_expr = to_python_expr(expr)
        try:
            return eval(py_expr, self._eval_globals(), self.scope)  # noqa: S307
        except NoveaSyntaxError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NoveaRuntimeError(f"Erro ao avaliar '{expr.strip()}': {exc}") from exc

    def _exec_stmt(self, raw: str) -> Any:
        code = raw.rstrip(";").strip()
        if not code:
            return None
        m = ASSIGN_RE.match(code)
        if m:
            name, expr = m.group(1), m.group(2)
            value = self._eval_expr(expr)
            self.scope[name] = value
            return value
        return self._eval_expr(code)

    def _matlab_range(self, range_str: str) -> List[Any]:
        parts_raw = _split_top_level(range_str, ":")
        values = [self._eval_expr(p) for p in parts_raw]
        if len(values) == 2:
            start, stop = values
            step = 1
        elif len(values) == 3:
            start, step, stop = values
        else:
            raise NoveaRuntimeError(f"Intervalo inválido: {range_str}")
        if step == 0:
            raise NoveaRuntimeError("Intervalo com passo zero.")
        count = int(np.floor((stop - start) / step + 1e-9)) + 1
        if count <= 0:
            return []
        return [start + k * step for k in range(count)]

    def _run_nodes(self, nodes: List[dict]) -> Any:
        last = None
        for node in nodes:
            kind = node["type"]
            if kind == "stmt":
                last = self._exec_stmt(node["code"])
            elif kind == "for":
                values = self._matlab_range(node["range"])
                for v in values:
                    self.scope[node["var"]] = v
                    last = self._run_nodes(node["body"])
            elif kind == "while":
                guard = 0
                while self._eval_expr(node["cond"]):
                    last = self._run_nodes(node["body"])
                    guard += 1
                    if guard > 2_000_000:
                        raise NoveaRuntimeError("Possível loop infinito — execução interrompida.")
            elif kind == "if":
                done = False
                for cond, body in node["branches"]:
                    if self._eval_expr(cond):
                        last = self._run_nodes(body)
                        done = True
                        break
                if not done and node["else"] is not None:
                    last = self._run_nodes(node["else"])
        return last

    # -- API pública --------------------------------------------------------

    def execute(self, code: str) -> Optional[str]:
        if "__" in code:
            raise NoveaRuntimeError("Segurança: O uso de propriedades internas (__) não é permitido.")
        if "import " in code or "import\t" in code or "import\n" in code:
            raise NoveaRuntimeError("Segurança: Instrução 'import' não é permitida.")

        self.plot_traces = []
        self.plot_layout = {}
        self._install_execution_functions()

        program = parse_program(code)
        last = self._run_nodes(program)

        if self.plot_traces:
            self.emit("job:plot", {"traces": self.plot_traces, "layout": self.plot_layout})

        return format_value(last)

    def display_scope(self) -> dict:
        return {k: to_plain(v) for k, v in self.scope.items() if not callable(v)}

    def snapshot(self) -> str:
        return json.dumps(self.display_scope())

    def rehydrate(self, snapshot: Optional[str]) -> None:
        self.scope = {}
        self._install_math_functions()
        if snapshot:
            try:
                data = json.loads(snapshot)
            except (TypeError, ValueError):
                data = {}
            for k, v in data.items():
                self.scope[k] = np.array(v) if isinstance(v, list) else v
