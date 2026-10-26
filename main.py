from __future__ import annotations

import asyncio
import inspect
import random
import re
import time
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register

from .backend import MemeBackend
from .parser import match_meme, option_help, parse_options, strip_prefix, tokenize
from .state import MemeState


TOOLS = {
    "水平翻转", "左翻", "右翻", "竖直翻转", "上翻", "下翻", "旋转", "缩放", "裁剪",
    "灰度图", "黑白", "反相", "反色", "横向拼接", "纵向拼接", "gif分解", "gif合成",
    "gif倒放", "倒放", "gif变速",
}
MANAGE = {"禁用表情", "启用表情", "全局禁用表情", "全局启用表情"}
BUILTINS = MANAGE | TOOLS | {
    "表情包制作", "表情列表", "表情帮助", "表情详情", "表情示例", "表情搜索",
    "随机表情", "图片操作", "图片工具", "取消表情", "表情指令", "表情面板", "表情菜单",
}
STAT_PATTERN = re.compile(r"^(我的)?(全局)?(今日|今天|本日|日|本周|周|本月|月|本年|年)?表情调用统计(?:\s+(.*))?$", re.S)


@dataclass
class Inputs:
    images: list[tuple[str, bytes]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Pending:
    meme: Any
    inputs: Inputs
    expires: float


class MemeMessageFilter(filter.CustomFilter):
    """Wake AstrBot only for inputs belonging to an active meme plugin.

    AstrBot evaluates filters before invoking handlers and wakes the bot for
    every matching handler. An ALL listener would therefore wake ordinary chat
    even when the handler later returns without a result.
    """

    plugins: weakref.WeakSet = weakref.WeakSet()

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return any(plugin._accepts(event) for plugin in tuple(self.plugins))


@register("astrbot_plugin_memes", "MemeCrafters contributors / AstrBot port", "Python 引擎表情包制作、图片面板与图片工具", "1.2.1")
class MemesPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.backend: MemeBackend | None = None
        self.startup_error = ""
        self.http: aiohttp.ClientSession | None = None
        self.data_dir = Path(StarTools.get_data_dir("astrbot_plugin_memes"))
        self.state = MemeState(self.data_dir / "memes.sqlite3")
        self.jobs = asyncio.Semaphore(max(1, min(8, int(config.get("max_concurrent_jobs", 2)))))
        self.pending: dict[tuple[str, str], Pending] = {}
        self.resource_task: asyncio.Task | None = None
        MemeMessageFilter.plugins.add(self)

    async def initialize(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=float(self.config.get("download_timeout", 30))))
        try:
            logger.info("[memes] 正在准备独立的 Python 表情引擎环境，首次启动需要安装依赖")
            self.backend = await asyncio.to_thread(
                MemeBackend, runtime_dir=self.data_dir / "python-engine",
                python_executable=self.config.get("engine_python_executable", ""),
            )
            logger.info("[memes] 已加载 %s 个表情模板", len(self.backend.memes))
            if self.config.get("check_resources_on_startup", True):
                self.resource_task = asyncio.create_task(self._check_resources())
        except Exception as exc:
            self.startup_error = str(exc)
            logger.exception("[memes] 生成引擎加载失败，请检查插件依赖")

    async def _check_resources(self):
        try:
            logger.info("[memes] 正在检查并下载表情资源，首次启动可能较慢")
            await asyncio.to_thread(self.backend.check_resources)
            logger.info("[memes] 表情资源检查完成")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[memes] 表情资源检查失败，请检查网络和 meme-generator 配置")

    async def terminate(self):
        MemeMessageFilter.plugins.discard(self)
        if self.resource_task and not self.resource_task.done():
            self.resource_task.cancel()
            await asyncio.gather(self.resource_task, return_exceptions=True)
        if self.http:
            await self.http.close()
        closer = getattr(self.backend, "close", None)
        if closer:
            await asyncio.to_thread(closer)
        self.pending.clear()
        self.state.close()

    def _scope(self, event: AstrMessageEvent) -> str:
        scene = f"group:{event.get_group_id()}" if event.get_group_id() else f"private:{event.get_sender_id()}"
        return f"{event.get_platform_id()}:{event.get_self_id()}:{scene}"

    def _user(self, event: AstrMessageEvent) -> str:
        return f"{event.get_platform_id()}:{event.get_self_id()}:{event.get_sender_id()}"

    def _accepts(self, event: AstrMessageEvent) -> bool:
        """Recognize commands synchronously before AstrBot's wake stage."""
        segments = event.get_messages()
        raw = (
            self._flatten(segments, {}, self_id=event.get_self_id()).strip()
            if segments
            else event.message_str.strip()
        )
        pending = self.pending.get((self._scope(event), self._user(event)))
        if pending and pending.expires > time.monotonic() and raw:
            return True
        text = strip_prefix(raw, self.config.get("command_prefixes", [""]))
        if not text:
            return False
        first = text.split(maxsplit=1)[0]
        if first in BUILTINS or STAT_PATTERN.fullmatch(text):
            return True
        return self.backend is not None and any(
            self._allowed(event, match.meme)
            for match in match_meme(self.backend.memes, text)
        )

    def _allowed(self, event, meme) -> bool:
        return meme.key not in self.config.get("disabled_memes", []) and self.state.enabled(self._scope(event), meme.key)

    def _is_admin(self, event, global_change=False) -> bool:
        if event.is_admin():
            return True
        if global_change or not self.config.get("allow_group_admin", True) or not event.get_group_id():
            return False
        # Only OneBot's authenticated sender metadata carries QQ group roles.
        if event.get_platform_name() != "aiocqhttp":
            return False
        raw = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw, dict):
            sender = raw.get("sender", {})
            return isinstance(sender, dict) and sender.get("role") in {"admin", "owner"}
        return False

    @staticmethod
    def _flatten(segments, sources, *, self_id="", reply=False):
        chunks = []
        for segment in segments or []:
            if isinstance(segment, Plain):
                chunks.append(segment.text)
            elif isinstance(segment, Image):
                marker = f"\ue000memes_image_{len(sources)}\ue001"
                sources[marker] = segment
                chunks.append(f" {marker} ")
            elif isinstance(segment, At) and not reply:
                uid = str(segment.qq)
                if uid != str(self_id):
                    chunks.append(f" @{uid} ")
        return "".join(chunks)

    async def _message(self, event, include_reply=False):
        sources = {}
        segments = event.get_messages()
        text = self._flatten(segments, sources, self_id=event.get_self_id()).strip()
        # Some platforms provide only message_str for plain messages.
        if not segments:
            text = event.message_str.strip()
        reply_parts = []
        for segment in segments if include_reply else []:
            if not isinstance(segment, Reply):
                continue
            chain = getattr(segment, "chain", None)
            if chain:
                reply_parts.append(self._flatten(chain, sources, reply=True))
            elif getattr(segment, "message_str", None):
                reply_parts.append(segment.message_str)
            elif event.get_platform_name() == "aiocqhttp" and getattr(event, "bot", None):
                # OneBot may initially provide only a reply ID.
                response = await event.bot.get_msg(message_id=segment.id)
                raw_chain = response.get("message", [])
                decoded = []
                if isinstance(raw_chain, list):
                    for item in raw_chain:
                        data = item.get("data", {})
                        if item.get("type") == "image":
                            decoded.append(Image(file=data.get("file", ""), url=data.get("url", "")))
                        elif item.get("type") == "text":
                            decoded.append(Plain(data.get("text", "")))
                reply_parts.append(self._flatten(decoded, sources, reply=True))
        return text, " ".join(reply_parts), sources

    async def _download(self, url: str) -> bytes:
        if not self.http:
            raise ValueError("插件尚未初始化，请稍后重试")
        limit = max(1, int(self.config.get("max_image_size_mb", 20))) * 1024 * 1024
        async with self.http.get(url) as response:
            response.raise_for_status()
            if response.content_length and response.content_length > limit:
                raise ValueError("图片过大，请发送较小的图片")
            output = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                output.extend(chunk)
                if len(output) > limit:
                    raise ValueError("图片过大，请发送较小的图片")
        return bytes(output)

    async def _image_bytes(self, image: Image) -> bytes:
        source = image.url or image.file or ""
        if source.startswith(("https://", "http://")):
            return await self._download(source)
        path = await image.convert_to_file_path()
        limit = max(1, int(self.config.get("max_image_size_mb", 20))) * 1024 * 1024
        def read():
            with Path(path).open("rb") as handle:
                data = handle.read(limit + 1)
            if len(data) > limit:
                raise ValueError("图片过大，请发送较小的图片")
            return data
        return await asyncio.to_thread(read)

    async def _avatar(self, event, uid: str) -> tuple[str, bytes]:
        name = event.get_sender_name() if uid == str(event.get_sender_id()) else uid
        if event.get_platform_name() == "aiocqhttp" and uid.isdigit():
            bot = getattr(event, "bot", None)
            if bot:
                try:
                    if event.get_group_id():
                        info = await bot.get_group_member_info(group_id=int(event.get_group_id()), user_id=int(uid), no_cache=False)
                        name = info.get("card") or info.get("nickname") or name
                    else:
                        info = await bot.get_stranger_info(user_id=int(uid), no_cache=False)
                        name = info.get("nickname") or name
                except Exception:
                    logger.debug("[memes] 无法读取头像昵称，使用用户 ID")
            return name, await self._download(f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640")
        if uid == str(event.get_sender_id()):
            getter = getattr(event, "get_sender_avatar_url", None)
            if getter:
                url = getter()
                if inspect.isawaitable(url):
                    url = await url
                if url:
                    return name, await self._download(url)
        raise ValueError("当前平台暂不支持获取这个用户的头像，请直接发送或引用图片")

    async def _collect(self, event, tokens, sources) -> Inputs:
        inputs = Inputs()
        for token in tokens:
            if token in sources:
                inputs.images.append(("", await self._image_bytes(sources[token])))
            elif token == "自己":
                inputs.images.append(await self._avatar(event, str(event.get_sender_id())))
            elif token.startswith("@") and len(token) > 1:
                inputs.images.append(await self._avatar(event, token[1:]))
            elif token.startswith("#") and len(token) > 1:
                inputs.names.append(token[1:])
            else:
                inputs.texts.append(token)
            if len(inputs.images) > 50:
                raise ValueError("一次最多输入 50 张图片")
        return inputs

    async def _job(self, function, *args):
        try:
            await asyncio.wait_for(self.jobs.acquire(), timeout=1)
        except asyncio.TimeoutError:
            raise ValueError("表情制作任务较多，请稍后重试") from None
        try:
            return await asyncio.to_thread(function, *args)
        finally:
            self.jobs.release()

    def _lookup(self, name):
        if not name:
            raise ValueError("请提供表情关键词或模板 key，例如：表情详情 petpet")
        found = self.backend.find(name.strip())
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ValueError("关键词对应多个模板，请改用 key：" + "、".join(m.key for m in found))
        candidates = self.backend.search(name)[:5]
        suffix = "，你可能在找：" + "、".join(m.key for m in candidates) if candidates else ""
        raise ValueError(f"找不到表情 {name}{suffix}")

    def _summary(self, meme):
        return f"{meme.key}（{' / '.join(meme.info.keywords)}）"

    @staticmethod
    def _panel_item(meme):
        params = meme.info.params
        return {
            "key": meme.key, "keywords": list(meme.info.keywords),
            "min_images": params.min_images, "max_images": params.max_images,
            "min_texts": params.min_texts, "max_texts": params.max_texts,
        }

    async def _panel_result(self, event, kind, payload):
        prefixes = self.config.get("command_prefixes", [""])
        payload = {"prefix": next((p for p in prefixes if p), ""), **payload}
        font_path = self.config.get("panel_font_path", "")
        if font_path:
            payload["font_path"] = font_path
        data = await self._job(self.backend.render_panel, kind, payload)
        return event.chain_result([Image.fromBytes(data)])

    async def _produce(self, event, meme, inputs):
        params = meme.info.params
        if not inputs.images and params.min_images == 1 and self.config.get("use_sender_when_no_image", False):
            inputs.images.append(await self._avatar(event, str(event.get_sender_id())))
        if len(inputs.images) == 1 and params.min_images == 2 and self.config.get("use_sender_for_two_images", True):
            inputs.images.insert(0, await self._avatar(event, str(event.get_sender_id())))
        if not inputs.texts and params.min_texts and self.config.get("use_default_when_no_text", False):
            inputs.texts = list(params.default_texts)
        policy = self.config.get("params_mismatch_policy", {})
        missing = []
        for kind, values, minimum, maximum in [
            ("image", inputs.images, params.min_images, params.max_images),
            ("text", inputs.texts, params.min_texts, params.max_texts),
        ]:
            label = "图片" if kind == "image" else "文字"
            if len(values) > maximum:
                action = policy.get(f"too_much_{kind}", "prompt")
                if action == "ignore":
                    return
                if action == "drop":
                    del values[maximum:]
                else:
                    raise ValueError(f"{label}数量不符，需要 {minimum}～{maximum}，实际 {len(values)}")
            if len(values) < minimum:
                action = policy.get(f"too_few_{kind}", "prompt")
                if action == "ignore":
                    return
                if action == "get":
                    missing.append(f"{minimum - len(values)} 份{label}")
                else:
                    raise ValueError(f"{label}数量不符，需要 {minimum}～{maximum}，实际 {len(values)}")
        if missing:
            if len(self.pending) >= 100:
                raise ValueError("等待中的表情任务过多，请稍后重试")
            timeout = max(5, min(300, int(self.config.get("input_timeout", 60))))
            self.pending[(self._scope(event), self._user(event))] = Pending(meme, inputs, time.monotonic() + timeout)
            yield event.plain_result(f"请在 {timeout} 秒内补充 {'、'.join(missing)}；发送 取消表情 可取消")
            return
        for index, name in enumerate(inputs.names):
            if index < len(inputs.images):
                inputs.images[index] = (name, inputs.images[index][1])
        data = await self._job(self.backend.generate, meme, inputs.images, inputs.texts, inputs.options)
        self.state.record(self._scope(event), self._user(event), meme.key)
        yield event.chain_result([Image.fromBytes(data)])

    async def _dispatch(self, event, text, reply, sources):
        # split also accepts newlines and tabs between the command and arguments.
        parts = text.split(maxsplit=1)
        head, rest = parts[0], parts[1] if len(parts) == 2 else ""
        scope, user = self._scope(event), self._user(event)
        pending_key = (scope, user)
        if head == "取消表情":
            self.pending.pop(pending_key, None)
            yield event.plain_result("已取消表情输入")
        elif head in {"表情指令", "表情面板", "表情菜单"} or (head == "表情帮助" and not rest):
            yield await self._panel_result(event, "help", {"total": sum(self._allowed(event, m) for m in self.backend.memes)})
        elif head in {"表情包制作", "表情列表"}:
            memes = [m for m in self.backend.memes if self._allowed(event, m)]
            size = 30
            pages = max(1, (len(memes) + size - 1) // size)
            if rest and not rest.isdigit():
                raise ValueError("用法：表情列表 [页码]")
            page = int(rest) if rest else 1
            if not 1 <= page <= pages:
                raise ValueError(f"页码应为 1～{pages}")
            yield await self._panel_result(event, "catalog", {
                "page": page, "pages": pages, "total": len(memes),
                "items": [self._panel_item(m) for m in memes[(page - 1) * size:page * size]],
            })
        elif head == "表情搜索":
            if not rest:
                raise ValueError("用法：表情搜索 关键词")
            query_parts = rest.rsplit(maxsplit=1)
            page = 1
            if len(query_parts) == 2 and query_parts[1].isdigit():
                rest, page = query_parts[0], int(query_parts[1])
            found = [m for m in self.backend.search(rest) if self._allowed(event, m)]
            pages = max(1, (len(found) + 29) // 30)
            if not 1 <= page <= pages:
                raise ValueError(f"页码应为 1～{pages}")
            yield await self._panel_result(event, "search", {
                "query": rest, "page": page, "pages": pages, "total": len(found),
                "items": [self._panel_item(m) for m in found[(page - 1) * 30:page * 30]],
            })
        elif head in {"表情详情", "表情帮助", "表情示例"}:
            meme = self._lookup(rest)
            p = meme.info.params
            yield await self._panel_result(event, "detail", {
                **self._panel_item(meme), "tags": sorted(getattr(meme.info, "tags", [])),
                "default_texts": list(p.default_texts),
                "options": [option_help(option) for option in p.options],
                "enabled": self._allowed(event, meme),
            })
            preview = await self._job(self.backend.preview, meme)
            yield event.chain_result([Image.fromBytes(preview)])
        elif head in MANAGE:
            global_change = head.startswith("全局")
            if not self._is_admin(event, global_change):
                raise ValueError("此操作需要 AstrBot 管理员权限" if global_change else "此操作需要 AstrBot 管理员或 QQ 群管理员权限")
            names = tokenize(rest)
            if not names:
                raise ValueError("请提供表情关键词或模板 key")
            memes = [self._lookup(name) for name in names]
            enabled = "启用" in head
            for meme in memes:
                if global_change:
                    self.state.set_global(meme.key, enabled)
                else:
                    self.state.set_enabled(scope, meme.key, enabled)
            yield event.plain_result(f"已{head}：" + "、".join(m.key for m in memes))
        elif STAT_PATTERN.fullmatch(text):
            match = STAT_PATTERN.fullmatch(text)
            mine, globally, period, key = match.groups()
            since = 0.0
            now = datetime.now().astimezone()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if period in {"本周", "周"}:
                start -= timedelta(days=start.weekday())
            elif period in {"本月", "月"}:
                start = start.replace(day=1)
            elif period in {"本年", "年"}:
                start = start.replace(month=1, day=1)
            if period:
                since = start.timestamp()
            meme_key = self._lookup(key).key if key else None
            rows = self.state.statistics(scope=None if globally else scope, user=user if mine else None, since=since, key=meme_key)
            by_key = {m.key: m for m in self.backend.memes}
            yield await self._panel_result(event, "statistics", {
                "title": "表情调用统计",
                "subtitle": " · ".join(item for item in ["我的" if mine else "", "全局" if globally else "当前会话", period or "全部时间"] if item),
                "rows": [{"key": key, "label": "/".join(by_key[key].info.keywords[:2]) if key in by_key else key, "count": count} for key, count in rows],
            })
        elif head in {"图片操作", "图片工具"}:
            yield await self._panel_result(event, "tools", {})
        elif head in TOOLS:
            inputs = await self._collect(event, tokenize(rest) + tokenize(reply), sources)
            if not inputs.images:
                raise ValueError("请发送或引用需要处理的图片")
            output = await self._job(self.backend.operate, head, [data for _, data in inputs.images], " ".join(inputs.texts))
            if isinstance(output, str):
                yield event.plain_result(output)
            else:
                frames = output if isinstance(output, list) else [output]
                maximum = max(1, min(50, int(self.config.get("max_output_images", 10))))
                if len(frames) > maximum:
                    raise ValueError(f"操作产生 {len(frames)} 张图片，超过发送上限 {maximum}；请调高 max_output_images 或减少帧数")
                for data in frames:
                    yield event.chain_result([Image.fromBytes(data)])
        elif head == "随机表情":
            inputs = await self._collect(event, tokenize(rest) + tokenize(reply), sources)
            candidates = [m for m in self.backend.memes if self._allowed(event, m)
                and m.info.params.min_images <= len(inputs.images) <= m.info.params.max_images
                and m.info.params.min_texts <= len(inputs.texts) <= m.info.params.max_texts]
            if not candidates:
                raise ValueError("找不到符合图片和文字数量的可用表情")
            meme = random.choice(candidates)
            if self.config.get("random_show_info", True):
                yield event.plain_result("随机表情：" + self._summary(meme))
            async for result in self._produce(event, meme, inputs):
                yield result
        else:
            matches = match_meme(self.backend.memes, text)
            matches = [m for m in matches if self._allowed(event, m.meme)]
            if matches:
                if len(matches) > 1:
                    raise ValueError("关键词对应多个模板，请使用 key：" + "、".join(m.meme.key for m in matches))
                match = matches[0]
                tokens, options = await self._job(
                    parse_options, match.meme,
                    list(match.arguments) + tokenize(match.remainder) + tokenize(reply),
                )
                inputs = await self._collect(event, tokens, sources)
                inputs.texts = list(match.texts) + inputs.texts
                inputs.names = list(match.names) + inputs.names
                inputs.options = {**match.options, **options}
                self.pending.pop(pending_key, None)
                async for result in self._produce(event, match.meme, inputs):
                    yield result
            elif pending_key in self.pending:
                pending = self.pending.pop(pending_key)
                if not self._allowed(event, pending.meme):
                    raise ValueError("该表情已禁用，已取消等待输入")
                extra = await self._collect(event, tokenize(text) + tokenize(reply), sources)
                pending.inputs.images.extend(extra.images)
                pending.inputs.texts.extend(extra.texts)
                pending.inputs.names.extend(extra.names)
                async for result in self._produce(event, pending.meme, pending.inputs):
                    yield result

    @filter.custom_filter(MemeMessageFilter)
    async def on_message(self, event: AstrMessageEvent):
        # The custom filter recognizes dynamic keywords before AstrBot wakes.
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value.expires > now}
        handled = False
        try:
            raw, reply, sources = await self._message(event)
            text = strip_prefix(raw, self.config.get("command_prefixes", [""]))
            pending_key = (self._scope(event), self._user(event))
            if text is None and pending_key in self.pending:
                text = raw
            if text is None or not text:
                return
            first = text.split(maxsplit=1)[0]
            is_builtin = first in BUILTINS or bool(STAT_PATTERN.fullmatch(text))
            if not self.backend:
                if is_builtin:
                    event.stop_event()
                    yield event.plain_result("表情生成引擎未就绪，请检查依赖安装和启动日志。" + (f"\n{self.startup_error}" if self.startup_error else ""))
                return
            matches = match_meme(self.backend.memes, text)
            if not is_builtin and pending_key not in self.pending and not any(self._allowed(event, m.meme) for m in matches):
                return
            handled = True
            _, reply, sources = await self._message(event, include_reply=True)
            async for result in self._dispatch(event, text, reply, sources):
                event.stop_event()
                yield result
        except (ValueError, aiohttp.ClientError, TimeoutError, OSError) as exc:
            if handled:
                event.stop_event()
                yield event.plain_result(f"表情制作失败：{exc}")
        except Exception:
            logger.exception("[memes] 处理表情指令失败")
            if handled:
                event.stop_event()
                yield event.plain_result("表情处理失败，请查看 AstrBot 日志中的 [memes] 记录")
