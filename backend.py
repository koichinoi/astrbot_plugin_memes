"""Host-side bridge to the isolated Python meme-generator environment.

Only the worker imports the drawing engine or Pillow. AstrBot retains its own
dependency versions while meme-generator 0.1.14 uses its supported environment.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace


_BOOTSTRAP_LOCK = threading.Lock()


def _namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


class RemoteMeme:
    def __init__(self, backend, catalog):
        self.key = catalog["key"]
        self.info = _namespace(catalog["info"])
        self._backend = backend

    def parse_options(self, tokens):
        result = self._backend._rpc("parse", key=self.key, tokens=tokens)
        return result[0], result[1]


class MemeBackend:
    def __init__(self, runtime_dir: Path | str | None = None, python_executable: str = ""):
        self.runtime_dir = Path(runtime_dir or Path.cwd() / "data/plugin_data/astrbot_plugin_memes/python-engine").resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._request_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._closed = False
        self._ids = itertools.count(1)
        self._panel_cache: OrderedDict[str, bytes] = OrderedDict()
        self._panel_lock = threading.Lock()
        self._worker_file = Path(__file__).with_name("engine_worker.py")
        self._log_path = self.runtime_dir / "engine.log"
        self._log = self._log_path.open("ab", buffering=0)
        try:
            self.python = self._ensure_python(python_executable)
            self._process = self._spawn()
            catalog = self._rpc("catalog")
            if not isinstance(catalog, list):
                raise RuntimeError("Python 表情引擎返回了无效的模板列表")
            self.memes = sorted([RemoteMeme(self, item) for item in catalog], key=lambda item: item.key)
            self._by_key = {meme.key: meme for meme in self.memes}
            self._names: dict[str, list[RemoteMeme]] = {}
            for meme in self.memes:
                names = {meme.key, *meme.info.keywords}
                for shortcut in meme.info.shortcuts:
                    names.add(shortcut.key)
                    if shortcut.humanized:
                        names.add(shortcut.humanized)
                for name in {name.casefold() for name in names if name}:
                    self._names.setdefault(name, []).append(meme)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _environment():
        env = os.environ.copy()
        # Explicit interpreter selection must not inherit AstrBot's module paths.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def _ensure_python(self, configured: str) -> str:
        if configured:
            executable = shutil.which(configured) or str(Path(configured).expanduser())
            if not Path(executable).is_file():
                raise RuntimeError("找不到 engine_python_executable 指定的 Python，请填写引擎环境的解释器路径")
            # A venv's bin/python may be a symlink. Resolving it would select
            # the base interpreter and lose the isolated site-packages.
            return os.path.abspath(executable)
        venv = self.runtime_dir / "venv"
        executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        requirements = Path(__file__).with_name("engine_requirements.txt")
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        marker = self.runtime_dir / "installed-requirements.sha256"
        with _BOOTSTRAP_LOCK:
            if executable.is_file() and marker.is_file() and marker.read_text().strip() == digest:
                return str(executable)
            install_log = self.runtime_dir / "install.log"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                with install_log.open("ab") as log:
                    if not executable.is_file():
                        subprocess.run(
                            [sys.executable, "-m", "venv", str(venv)],
                            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180,
                            creationflags=flags, env=self._environment(),
                        )
                    subprocess.run(
                        [str(executable), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
                        stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900,
                        creationflags=flags, env=self._environment(),
                    )
                marker.write_text(digest, encoding="ascii")
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(
                    f"Python 表情引擎环境安装失败，请查看 {install_log}；也可配置 engine_python_executable 使用已安装引擎的环境"
                ) from exc
        return str(executable)

    def _spawn(self):
        with self._process_lock:
            if self._closed:
                raise RuntimeError("Python 表情引擎已关闭")
            process = subprocess.Popen(
                [self.python, "-u", str(self._worker_file), "--runtime-dir", str(self.runtime_dir / "engine-data")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                text=True, encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=self._environment(),
            )
            self._processes.add(process)
        return process

    def _rpc(self, method: str, *, _process=None, _lock=None, **payload):
        process = _process if _process is not None else self._process
        lock = _lock if _lock is not None else self._request_lock
        with lock:
            if self._closed or process.poll() is not None:
                raise RuntimeError(f"Python 表情引擎未运行，请重载插件并查看 {self._log_path}")
            request_id = next(self._ids)
            try:
                process.stdin.write(json.dumps({"id": request_id, "method": method, **payload}, ensure_ascii=False) + "\n")
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"Python 表情引擎已退出，请查看 {self._log_path}")
                response = json.loads(line)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"无法与 Python 表情引擎通信，请查看 {self._log_path}") from exc
            if response.get("id") != request_id:
                raise RuntimeError("Python 表情引擎响应次序错误，请重载插件")
            if not response.get("ok"):
                raise ValueError(response.get("error", "Python 表情引擎处理失败"))
            return response.get("result")

    def find(self, name):
        return list(self._names.get(name.strip().casefold(), []))

    def search(self, query):
        query = query.strip().casefold()
        return [meme for meme in self.memes if any(query in value.casefold() for value in
                [meme.key, *meme.info.keywords, *meme.info.tags,
                 *[s.humanized or s.key for s in meme.info.shortcuts]])]

    def generate(self, meme, images: list[tuple[str, bytes]], texts, options):
        result = self._rpc("generate", key=meme.key, images=[{"name": name, "data": base64.b64encode(data).decode("ascii")} for name, data in images], texts=texts, options=options)
        return base64.b64decode(result, validate=True)

    def preview(self, meme):
        return base64.b64decode(self._rpc("preview", key=meme.key), validate=True)

    def render_panel(self, kind: str, payload: dict) -> bytes:
        cache_key = hashlib.sha256(json.dumps([kind, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self._panel_lock:
            cached = self._panel_cache.get(cache_key)
            if cached is not None:
                self._panel_cache.move_to_end(cache_key)
                return cached
        data = base64.b64decode(self._rpc("panel", kind=kind, payload=payload), validate=True)
        with self._panel_lock:
            self._panel_cache[cache_key] = data
            self._panel_cache.move_to_end(cache_key)
            while len(self._panel_cache) > 16:
                self._panel_cache.popitem(last=False)
        return data

    def operate(self, command, images, argument="") -> bytes | list[bytes] | str:
        result = self._rpc("operate", command=command, images=[base64.b64encode(data).decode("ascii") for data in images], argument=argument)
        if result["kind"] == "text":
            return result["data"]
        if result["kind"] == "images":
            return [base64.b64decode(data, validate=True) for data in result["data"]]
        return base64.b64decode(result["data"], validate=True)

    def check_resources(self):
        # Downloading the whole catalog must not occupy the generation worker.
        process = self._spawn()
        try:
            return self._rpc("resources", _process=process, _lock=threading.Lock())
        finally:
            self._stop_process(process)

    def _stop_process(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()
        with self._process_lock:
            self._processes.discard(process)

    def close(self):
        with self._process_lock:
            self._closed = True
            processes = tuple(self._processes)
        for process in processes:
            self._stop_process(process)
        if not self._log.closed:
            self._log.close()
