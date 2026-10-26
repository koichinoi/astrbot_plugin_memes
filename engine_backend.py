"""Framework-independent bridge to the Python meme-generator 0.1 engine.

Image tools are implemented with Pillow, including animation frame timings.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import math
import re
from types import SimpleNamespace
from typing import Any


class PythonMeme:
    """Expose the plugin's catalog shape while retaining Python engine objects."""

    def __init__(self, native: Any):
        self.native = native
        self.key = native.key
        params = native.params_type
        self.info = SimpleNamespace(
            keywords=list(native.keywords),
            shortcuts=list(native.shortcuts),
            tags=set(native.tags),
            params=SimpleNamespace(
                min_images=params.min_images,
                max_images=params.max_images,
                min_texts=params.min_texts,
                max_texts=params.max_texts,
                default_texts=list(params.default_texts),
                args_type=params.args_type,
                options=list(params.args_type.parser_options) if params.args_type else [],
            ),
        )


class EngineBackend:
    def __init__(self):
        # Keep dependency failures inside plugin initialization so AstrBot can
        # still load the plugin and report an actionable installation error.
        import meme_generator
        version = str(getattr(meme_generator, "__version__", ""))
        if not version.startswith("0.1."):
            raise RuntimeError(
                "此插件需要 Python 引擎 meme-generator==0.1.14，"
                f"当前检测到 {version or '未知版本'}；请在引擎环境中安装 engine_requirements.txt 的依赖"
            )
        from PIL import Image, ImageOps
        from meme_generator.exception import MemeGeneratorException

        self._engine = meme_generator
        self._Image = Image
        self._ImageOps = ImageOps
        self._engine_error = MemeGeneratorException
        self.memes = sorted(
            [PythonMeme(meme) for meme in meme_generator.get_memes()], key=lambda meme: meme.key
        )
        self._by_key = {meme.key: meme for meme in self.memes}
        self._names: dict[str, list[PythonMeme]] = {}
        for meme in self.memes:
            names = {meme.key, *meme.info.keywords}
            for shortcut in meme.info.shortcuts:
                names.add(shortcut.key)
                if shortcut.humanized:
                    names.add(shortcut.humanized)
            for name in {name.casefold() for name in names}:
                self._names.setdefault(name, []).append(meme)

    def find(self, name: str) -> list[PythonMeme]:
        return list(self._names.get(name.strip().casefold(), []))

    def search(self, query: str) -> list[PythonMeme]:
        query = query.strip().casefold()
        if not query:
            return []
        result = []
        for meme in self.memes:
            names = [meme.key, *meme.info.keywords, *meme.info.tags]
            names.extend(shortcut.humanized or shortcut.key for shortcut in meme.info.shortcuts)
            if any(query in str(name).casefold() for name in names):
                result.append(meme)
        return result

    @staticmethod
    def _bytes(result: Any) -> bytes:
        if isinstance(result, bytes):
            return result
        if isinstance(result, BytesIO):
            # The upstream encoder leaves its stream positioned at the end.
            return result.getvalue()
        raise ValueError("Python 引擎未返回有效图片")

    def _render(self, function: Any, **kwargs: Any) -> bytes:
        try:
            return self._bytes(function(**kwargs))
        except self._engine_error as exc:
            raise ValueError(getattr(exc, "message", str(exc))) from exc
        except FileNotFoundError as exc:
            raise ValueError("缺少表情素材，请等待资源下载完成或检查网络后重启插件") from exc

    def generate(
        self, meme: PythonMeme, images: list[tuple[str, bytes]], texts: list[str], options: dict[str, Any]
    ) -> bytes:
        args = dict(options)
        args["user_infos"] = [{"name": name} for name, _ in images]
        return self._render(
            meme.native, images=[data for _, data in images], texts=list(texts), args=args
        )

    def preview(self, meme: PythonMeme) -> bytes:
        return self._render(meme.native.generate_preview)

    def check_resources(self) -> None:
        from meme_generator.download import check_resources

        # AstrBot calls this synchronous bridge in a worker thread.
        asyncio.run(check_resources())

    @staticmethod
    def _number(argument: str, default: float | None = None) -> float | None:
        if not argument.strip():
            return default
        try:
            value = float(argument)
        except ValueError as exc:
            raise ValueError("请输入有效的数字参数") from exc
        if not math.isfinite(value):
            raise ValueError("数字参数必须是有限数值")
        return value

    def _decode(self, data: bytes) -> Any:
        try:
            with self._Image.open(BytesIO(data)) as image:
                frames, durations = [], []
                for index in range(getattr(image, "n_frames", 1)):
                    image.seek(index)
                    frames.append(image.convert("RGBA"))
                    durations.append(max(20, int(image.info.get("duration", 100))))
                return SimpleNamespace(
                    frames=frames,
                    durations=durations,
                    loop=int(image.info.get("loop", 0)),
                    width=image.width,
                    height=image.height,
                    average_duration=sum(durations) / len(durations) / 1000,
                )
        except (OSError, ValueError, EOFError) as exc:
            raise ValueError("图片解码出错，请提供有效图片") from exc

    def _encode(self, frames: list[Any], durations: list[int] | None = None, loop: int = 0) -> bytes:
        output = BytesIO()
        if durations is None:
            frames[0].save(output, format="PNG")
        else:
            frames[0].save(
                output, format="GIF", save_all=True, append_images=frames[1:],
                duration=durations, loop=loop, disposal=2, optimize=False,
            )
        return output.getvalue()

    def _transform(self, image: Any, operation: Any) -> bytes:
        frames = [operation(frame) for frame in image.frames]
        return self._encode(frames, image.durations if len(frames) > 1 else None, image.loop)

    def _merge(self, images: list[Any], horizontal: bool) -> bytes:
        width = sum(image.width for image in images) if horizontal else max(image.width for image in images)
        height = max(image.height for image in images) if horizontal else sum(image.height for image in images)
        animated = any(len(image.frames) > 1 for image in images)
        total = max(sum(image.durations) for image in images if len(image.frames) > 1) if animated else 100
        boundaries = {0, total}
        for image in images:
            if len(image.frames) == 1:
                continue
            elapsed = 0
            while elapsed < total:
                for duration in image.durations:
                    elapsed += duration
                    if elapsed < total:
                        boundaries.add(elapsed)
        times = sorted(boundaries)
        output_frames = []
        for time in times[:-1]:
            canvas = self._Image.new("RGBA", (width, height))
            x = y = 0
            for image in images:
                position = time % sum(image.durations)
                index = 0
                while index < len(image.durations) - 1 and position >= image.durations[index]:
                    position -= image.durations[index]
                    index += 1
                canvas.alpha_composite(image.frames[index], (x, y))
                if horizontal:
                    x += image.width
                else:
                    y += image.height
            output_frames.append(canvas)
        durations = [end - start for start, end in zip(times, times[1:])]
        return self._encode(output_frames, durations if animated else None)

    def operate(self, command: str, images: list[bytes], argument: str = "") -> bytes | list[bytes] | str:
        aliases = {
            "左翻": "水平翻转", "右翻": "水平翻转", "上翻": "竖直翻转", "下翻": "竖直翻转",
            "黑白": "灰度图", "反色": "反相", "倒放": "gif倒放",
        }
        command = aliases.get(command.casefold(), command.casefold())
        multiple = command in {"横向拼接", "纵向拼接", "gif合成"}
        if not images:
            return "请附带图片，或引用一条包含图片的消息"
        if not multiple and len(images) != 1:
            return "此操作需要且只能使用 1 张图片"
        argument = argument.strip()
        try:
            decoded = [self._decode(data) for data in images]
            image = decoded[0]
            if command == "水平翻转":
                return self._transform(image, self._ImageOps.mirror)
            if command == "竖直翻转":
                return self._transform(image, self._ImageOps.flip)
            if command == "旋转":
                angle = self._number(argument, 90)
                return self._transform(image, lambda frame: frame.rotate(angle, expand=True))
            if command in {"灰度图", "反相"}:
                def color(frame):
                    rgb = frame.convert("RGB")
                    output = (self._ImageOps.grayscale(rgb) if command == "灰度图" else self._ImageOps.invert(rgb)).convert("RGBA")
                    output.putalpha(frame.getchannel("A"))
                    return output
                return self._transform(image, color)
            if command in {"横向拼接", "纵向拼接"}:
                return self._merge(decoded, command == "横向拼接")
            if command == "gif分解":
                return [self._encode([frame]) for frame in image.frames]
            if command == "gif合成":
                duration = self._number(argument, 0.1)
                if duration < 0.02:
                    return "GIF 帧间隔必须大于或等于 0.02 秒"
                width = max(item.width for item in decoded)
                height = max(item.height for item in decoded)
                frames = []
                for item in decoded:
                    for frame in item.frames:
                        canvas = self._Image.new("RGBA", (width, height))
                        canvas.alpha_composite(frame, ((width - item.width) // 2, (height - item.height) // 2))
                        frames.append(canvas)
                return self._encode(frames, [round(duration * 1000)] * len(frames))
            if command == "gif倒放":
                if len(image.frames) < 2:
                    return "请提供 GIF 动图"
                return self._encode(image.frames[::-1], image.durations[::-1], image.loop)
            if command == "缩放":
                return self._resize(image, argument)
            if command == "裁剪":
                return self._crop(image, argument)
            if command == "gif变速":
                return self._speed(image, argument)
            return "未知图片操作"
        except (ValueError, OverflowError, ZeroDivisionError, OSError) as exc:
            return str(exc) or "图片操作参数无效"

    def _resize(self, image: Any, argument: str) -> bytes | str:
        size = re.fullmatch(r"(\d{1,4})?[*xX, ](\d{1,4})?", argument)
        percent = re.fullmatch(r"(\d{1,3})%", argument)
        width = height = None
        if size and any(size.groups()):
            width, height = (int(value) if value else None for value in size.groups())
            if width is None:
                width = round(image.width * height / image.height)
            if height is None:
                height = round(image.height * width / image.width)
        elif percent:
            ratio = int(percent.group(1)) / 100
            width, height = int(image.width * ratio), int(image.height * ratio)
        else:
            return "请使用正确的尺寸格式，如：100x100、100x、50%"
        if width <= 0 or height <= 0:
            return "缩放后的宽高必须大于 0"
        return self._transform(image, lambda frame: frame.resize((width, height), self._Image.Resampling.LANCZOS))

    def _crop(self, image: Any, argument: str) -> bytes | str:
        bounds = re.fullmatch(r"(\d{1,4})[, ](\d{1,4})[, ](\d{1,4})[, ](\d{1,4})", argument)
        size = re.fullmatch(r"(\d{1,4})[*xX, ](\d{1,4})", argument)
        ratio = re.fullmatch(r"(\d{1,2})[:：比](\d{1,2})", argument)
        if bounds:
            left, top, right, bottom = map(int, bounds.groups())
        else:
            if size:
                width, height = map(int, size.groups())
            elif ratio:
                x, y = map(int, ratio.groups())
                if not x or not y:
                    return "裁剪比例必须大于 0"
                scale = min(image.width / x, image.height / y)
                width, height = int(x * scale), int(y * scale)
            else:
                return "请使用正确的裁剪格式，如：0,0,100,100、100x100、2:1"
            left, top = (image.width - width) // 2, (image.height - height) // 2
            right, bottom = left + width, top + height
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            return "裁剪范围必须位于原图内，且宽高大于 0"
        return self._transform(image, lambda frame: frame.crop((left, top, right, bottom)))

    def _speed(self, image: Any, argument: str) -> bytes | str:
        number = r"\d{0,3}\.?\d{1,3}"
        fps = re.fullmatch(rf"({number})fps", argument, re.I)
        interval = re.fullmatch(rf"({number})(m?)s", argument, re.I)
        speed = re.fullmatch(rf"({number})(?:x|倍速?)", argument, re.I)
        percent = re.fullmatch(rf"({number})%", argument)
        if fps:
            value = float(fps.group(1))
            if value <= 0:
                return "FPS 必须大于 0"
            durations = [1000 / value] * len(image.frames)
        elif interval:
            duration = float(interval.group(1)) * (1 if interval.group(2).lower() == "m" else 1000)
            durations = [duration] * len(image.frames)
        elif speed or percent:
            value = float((speed or percent).group(1))
            if value <= 0:
                return "播放倍率必须大于 0"
            factor = value if speed else value / 100
            durations = [duration / factor for duration in image.durations]
        else:
            return "请使用正确的倍率格式，如：0.5x、50%、20FPS、0.05s"
        if min(durations) < 20:
            return "GIF 帧间隔必须大于或等于 0.02 秒（不超过 50 FPS）"
        if len(image.frames) < 2:
            return "请提供 GIF 动图"
        return self._encode(image.frames, [round(value) for value in durations], image.loop)
