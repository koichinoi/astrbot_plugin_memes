"""Local JSON-lines worker for the isolated Python meme-generator runtime.

This entry point deliberately has no AstrBot imports. Its stdout is reserved
for replies; engine progress, diagnostics and tracebacks are sent to stderr.
"""

from __future__ import annotations

import base64
import argparse
from contextlib import redirect_stdout
from enum import Enum
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import traceback
from typing import Any


def configure_runtime(runtime_dir: Path) -> None:
    """Bind upstream's directories before its package initializer executes.

    Upstream looks up Windows registry folders rather than APPDATA, so simply
    changing environment variables would still use the user's global config.
    Loading only its independent dirs module lets us preserve its helpers while
    assigning private locations for this plugin's engine configuration.
    """
    package_spec = importlib.util.find_spec("meme_generator")
    if package_spec is None or not package_spec.submodule_search_locations:
        raise RuntimeError("未安装 Python 引擎，请安装 engine_requirements.txt")
    package_dir = Path(next(iter(package_spec.submodule_search_locations)))
    spec = importlib.util.spec_from_file_location("meme_generator.dirs", package_dir / "dirs.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("无法载入 Python 引擎目录设置")
    module = importlib.util.module_from_spec(spec)
    sys.modules["meme_generator.dirs"] = module
    spec.loader.exec_module(module)
    root = runtime_dir.resolve()
    for attribute, directory in (
        ("BASE_CONFIG_DIR", "config"),
        ("BASE_CACHE_DIR", "cache"),
        ("BASE_DATA_DIR", "data"),
    ):
        target = root / directory
        target.mkdir(parents=True, exist_ok=True)
        setattr(module, attribute, target)


def _json_safe(value: Any) -> Any:
    """Copy engine metadata to plain JSON values without leaking native objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("引擎返回了非有限数值")
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    raise TypeError(f"引擎返回了无法传输的类型：{type(value).__name__}")


def _encode(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise TypeError("引擎未返回图片字节")
    return base64.b64encode(data).decode("ascii")


def _decode(data: Any) -> bytes:
    if not isinstance(data, str):
        raise ValueError("图片数据必须使用 base64 文字")
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("图片 base64 数据无效") from exc


def _catalog(meme: Any) -> dict[str, Any]:
    params = meme.info.params
    return {
        "key": str(meme.key),
        "info": {
            "keywords": list(meme.info.keywords),
            "tags": sorted(meme.info.tags),
            "shortcuts": [
                {
                    "key": str(shortcut.key),
                    "args": _json_safe(shortcut.args),
                    "humanized": shortcut.humanized,
                }
                for shortcut in meme.info.shortcuts
            ],
            "params": {
                "min_images": int(params.min_images),
                "max_images": int(params.max_images),
                "min_texts": int(params.min_texts),
                "max_texts": int(params.max_texts),
                "default_texts": list(params.default_texts),
                "options": [
                    {
                        "names": list(option.names),
                        "args": [
                            {"name": str(arg.name), "value": str(arg.value)}
                            for arg in option.args or []
                        ],
                        "help_text": option.help_text,
                        "default": _json_safe(option.default),
                        "dest": option.dest,
                    }
                    for option in params.options
                ],
            },
        },
    }


class Worker:
    def __init__(self) -> None:
        self._backend: Any = None
        self._by_key: dict[str, Any] = {}

    def backend(self) -> Any:
        if self._backend is None:
            from engine_backend import EngineBackend

            self._backend = EngineBackend()
            self._by_key = {meme.key: meme for meme in self._backend.memes}
        return self._backend

    def meme(self, key: Any) -> Any:
        self.backend()
        if not isinstance(key, str) or key not in self._by_key:
            raise ValueError(f"不存在的表情模板：{key}")
        return self._by_key[key]

    def dispatch(self, request: dict[str, Any]) -> Any:
        method = request.get("method")
        if method == "shutdown":
            return None
        backend = self.backend()
        if method == "version":
            return importlib.metadata.version("meme-generator")
        if method == "catalog":
            return [_catalog(meme) for meme in backend.memes]
        if method == "panel":
            from panel import render_panel

            return _encode(render_panel(request.get("kind", "help"), request.get("payload", {})))
        if method == "generate":
            images = [
                (str(image.get("name", "")), _decode(image.get("data")))
                for image in request.get("images", [])
            ]
            return _encode(backend.generate(
                self.meme(request.get("key")), images,
                request.get("texts", []), request.get("options", {}),
            ))
        if method == "preview":
            return _encode(backend.preview(self.meme(request.get("key"))))
        if method == "operate":
            result = backend.operate(
                request.get("command", ""),
                [_decode(image) for image in request.get("images", [])],
                request.get("argument", ""),
            )
            if isinstance(result, bytes):
                return {"kind": "image", "data": _encode(result)}
            if isinstance(result, list):
                return {"kind": "images", "data": [_encode(image) for image in result]}
            if isinstance(result, str):
                return {"kind": "text", "data": result}
            raise TypeError("图片操作返回了无法识别的结果")
        if method == "parse":
            from parser import parse_options

            return _json_safe(parse_options(
                self.meme(request.get("key")), request.get("tokens", []),
            ))
        if method == "resources":
            backend.check_resources()
            return None
        raise ValueError(f"未知的引擎操作：{method}")


def serve(input_stream: Any, output_stream: Any) -> None:
    worker = Worker()
    for line in input_stream:
        request: Any = None
        request_id: Any = None
        shutdown = False
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("引擎请求必须是 JSON 对象")
            request_id = request.get("id")
            if request_id is not None and not isinstance(request_id, (str, int)):
                raise ValueError("引擎请求编号必须是文字或整数")
            shutdown = request.get("method") == "shutdown"
            with redirect_stdout(sys.stderr):
                result = worker.dispatch(request)
            response = {"id": request_id, "ok": True, "result": _json_safe(result)}
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {
                "id": request_id if isinstance(request_id, (str, int)) else None,
                "ok": False,
                "error": str(exc) or type(exc).__name__,
            }
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
        output_stream.write(encoded + "\n")
        output_stream.flush()
        if shutdown:
            break


def main() -> None:
    # Duplicate the protocol pipe before redirecting fd 1, so native libraries
    # that bypass Python's sys.stdout also cannot corrupt JSON replies.
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    input_stream = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--runtime-dir", type=Path)
        arguments = parser.parse_args()
        if arguments.runtime_dir is not None:
            with redirect_stdout(sys.stderr):
                configure_runtime(arguments.runtime_dir)
        serve(input_stream, protocol)
    finally:
        protocol.close()


if __name__ == "__main__":
    main()
