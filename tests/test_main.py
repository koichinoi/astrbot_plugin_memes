"""Routing integration tests with the public AstrBot message contract.

The small SDK doubles keep these tests usable outside an AstrBot deployment.
They exercise the complete plugin handler, parser, and persistent state; actual
SDK loading is also checked separately during release validation.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


PLUGIN_DIR = Path(__file__).resolve().parents[1]


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, file="", url=""):
        self.file, self.url = file, url

    @classmethod
    def fromBytes(cls, data):
        return cls("base64://" + base64.b64encode(data).decode())

    async def convert_to_file_path(self):
        return self.file


class At:
    def __init__(self, qq):
        self.qq = qq


class Reply:
    def __init__(self, id="reply", chain=None, message_str="", sender_id=""):
        self.id = id
        self.chain = chain
        self.message_str = message_str
        self.sender_id = sender_id


class FakeEvent:
    def __init__(self, segments=None, *, text="", user="11", group="100", platform="adapter1", self_id="999", platform_name="aiocqhttp", admin=False, raw=None):
        self.segments = segments if segments is not None else [Plain(text)]
        self.message_str = text or "".join(part.text for part in self.segments if isinstance(part, Plain))
        self.user, self.group, self.platform = user, group, platform
        self.self_id, self.platform_name, self.admin = self_id, platform_name, admin
        self.message_obj = SimpleNamespace(raw_message=raw)
        self.stopped = False

    def get_messages(self):
        return self.segments

    def get_sender_id(self):
        return self.user

    def get_sender_name(self):
        return "发送者"

    def get_group_id(self):
        return self.group

    def get_platform_id(self):
        return self.platform

    def get_platform_name(self):
        return self.platform_name

    def get_self_id(self):
        return self.self_id

    def is_admin(self):
        return self.admin

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return SimpleNamespace(chain=[Plain(text)])

    def chain_result(self, chain):
        return SimpleNamespace(chain=chain)


class StarTools:
    data_dir = None

    @staticmethod
    def get_data_dir(name):
        return StarTools.data_dir


def load_plugin():
    """Import the production module without installing the full bot server."""
    modules = {name: ModuleType(name) for name in ["astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.message_components", "astrbot.api.star"]}
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("memes.test")

    class CustomFilter:
        def __init__(self, raise_error=True):
            self.raise_error = raise_error

    def custom_filter(filter_type):
        def decorate(handler):
            handler.test_filter = filter_type()
            return handler
        return decorate

    modules["astrbot.api.event"].filter = SimpleNamespace(CustomFilter=CustomFilter, custom_filter=custom_filter)
    modules["astrbot.api.event"].AstrMessageEvent = FakeEvent
    components = modules["astrbot.api.message_components"]
    components.Plain, components.Image, components.At, components.Reply = Plain, Image, At, Reply

    class Star:
        def __init__(self, context):
            self.context = context

    star = modules["astrbot.api.star"]
    star.Star, star.Context, star.StarTools = Star, object, StarTools
    star.register = lambda *args, **kwargs: lambda cls: cls
    name = "memes_handler_test"
    package = ModuleType(name)
    package.__path__ = [str(PLUGIN_DIR)]
    modules[name] = package
    backend = ModuleType(f"{name}.backend")
    backend.MemeBackend = object
    modules[backend.__name__] = backend
    spec = importlib.util.spec_from_file_location(f"{name}.main", PLUGIN_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


main = load_plugin()


def make_meme(key="sample", keyword="示例", *, min_images=0, max_images=0, min_texts=1, max_texts=1, defaults=None):
    return SimpleNamespace(key=key, info=SimpleNamespace(keywords=[keyword], tags=[], shortcuts=[], params=SimpleNamespace(min_images=min_images, max_images=max_images, min_texts=min_texts, max_texts=max_texts, default_texts=defaults or [], options=[])))


class FakeBackend:
    def __init__(self, memes):
        self.memes = memes
        self.calls = []
        self.panel_calls = []

    def find(self, name):
        return [m for m in self.memes if name in [m.key, *m.info.keywords]]

    def search(self, name):
        return self.find(name)

    def generate(self, meme, images, texts, options):
        self.calls.append((meme.key, list(images), list(texts), dict(options)))
        return b"GIF89a-generated"

    def preview(self, meme):
        return b"GIF89a-preview"

    def render_panel(self, kind, payload):
        self.panel_calls.append((kind, deepcopy(payload)))
        return b"\x89PNG\r\n\x1a\n-test-panel"


async def collect(handler):
    return [result async for result in handler]


def text_of(results):
    return "\n".join(part.text for result in results for part in result.chain if isinstance(part, Plain))


class MainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        StarTools.data_dir = self.directory.name
        self.config = {"command_prefixes": ["/", ""], "use_sender_for_two_images": False, "check_resources_on_startup": False}
        self.plugin = main.MemesPlugin(object(), self.config)
        self.meme = make_meme()
        self.backend = FakeBackend([self.meme])
        self.plugin.backend = self.backend

    async def asyncTearDown(self):
        if self.plugin is not None:
            await self.plugin.terminate()
        self.directory.cleanup()

    async def test_custom_filter_rejects_unrelated_disabled_and_prefix_mismatch(self):
        message_filter = self.plugin.on_message.test_filter
        self.assertFalse(message_filter.filter(FakeEvent(text="普通聊天"), {}))
        self.assertFalse(message_filter.filter(FakeEvent(text="/菜单"), {}))
        self.assertTrue(message_filter.filter(FakeEvent(text="/表情列表"), {}))
        self.assertTrue(message_filter.filter(FakeEvent(text="示例 内容"), {}))
        self.assertFalse(message_filter.filter(FakeEvent(text="示例类似词"), {}))
        self.config["command_prefixes"] = ["/"]
        self.assertFalse(message_filter.filter(FakeEvent(text="示例 内容"), {}))
        event = FakeEvent(text="/示例 内容")
        self.plugin.state.set_enabled(self.plugin._scope(event), self.meme.key, False)
        self.assertFalse(message_filter.filter(event, {}))

    async def test_empty_prefix_list_keeps_commands_working_without_waking_chat(self):
        self.config["command_prefixes"] = []
        message_filter = self.plugin.on_message.test_filter
        for command in ["表情面板", "表情列表", "示例 内容"]:
            with self.subTest(command=command):
                event = FakeEvent(text=command)
                self.assertTrue(message_filter.filter(event, {}))
                self.assertTrue(await collect(self.plugin.on_message(event)))
                self.assertTrue(event.stopped)
        ordinary = FakeEvent(text="今天很好")
        self.assertFalse(message_filter.filter(ordinary, {}))
        self.assertEqual(await collect(self.plugin.on_message(ordinary)), [])
        self.assertFalse(ordinary.stopped)

    async def test_filter_removes_runtime_on_termination(self):
        message_filter = self.plugin.on_message.test_filter
        event = FakeEvent(text="/表情列表")
        self.assertTrue(message_filter.filter(event, {}))
        await self.plugin.terminate()
        self.plugin = None
        self.assertFalse(message_filter.filter(event, {}))

    async def test_ordinary_chat_yields_nothing_and_does_not_stop(self):
        event = FakeEvent(text="今天很好")
        self.assertEqual(await collect(self.plugin.on_message(event)), [])
        self.assertFalse(event.stopped)

    def assert_image_results(self, results, count=1):
        self.assertEqual(len(results), count)
        self.assertEqual(text_of(results), "")
        images = [part for result in results for part in result.chain if isinstance(part, Image)]
        self.assertEqual(len(images), count)
        self.assertTrue(base64.b64decode(images[0].file.removeprefix("base64://")).startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_help_and_panel_aliases_emit_images_instead_of_text(self):
        for command in ["/表情指令", "/表情面板", "/表情菜单", "/表情帮助"]:
            with self.subTest(command=command):
                event = FakeEvent(text=command)
                self.assertTrue(self.plugin._accepts(event))
                results = await collect(self.plugin.on_message(event))
                self.assert_image_results(results)
                self.assertEqual(self.backend.panel_calls[-1][0], "help")
                self.assertTrue(event.stopped)

    async def test_catalog_panel_pagination_uses_only_currently_available_templates(self):
        self.backend.memes = [make_meme(f"m{i:02}", f"模板{i}", min_images=1, max_images=2) for i in range(64)]
        event = FakeEvent(text="/表情列表 2")
        self.plugin.state.set_enabled(self.plugin._scope(event), "m00", False)
        self.config["disabled_memes"] = ["m01"]
        results = await collect(self.plugin.on_message(event))
        self.assert_image_results(results)
        kind, payload = self.backend.panel_calls[-1]
        self.assertEqual(kind, "catalog")
        self.assertEqual((payload["page"], payload["pages"], payload["total"]), (2, 3, 62))
        self.assertEqual(len(payload["items"]), 30)
        self.assertEqual([item["key"] for item in payload["items"]], [f"m{i:02}" for i in range(32, 62)])
        self.assertEqual(payload["items"][0]["keywords"], ["模板32"])
        self.assertEqual((payload["items"][0]["min_images"], payload["items"][0]["max_images"]), (1, 2))

        other_group = FakeEvent(text="/表情列表 1", group="101")
        self.assert_image_results(await collect(self.plugin.on_message(other_group)))
        self.assertEqual(self.backend.panel_calls[-1][1]["total"], 63)

    async def test_invalid_catalog_page_stays_a_concise_text_error(self):
        results = await collect(self.plugin.on_message(FakeEvent(text="/表情列表 999")))
        self.assertIn("页码", text_of(results))
        self.assertEqual(self.backend.panel_calls, [])

    async def test_search_panel_receives_query_and_filtered_catalog_items(self):
        blocked = make_meme("blocked", "其他示例")
        self.backend.memes.append(blocked)
        self.backend.search = lambda query: list(self.backend.memes)
        event = FakeEvent(text="/表情搜索 示例")
        self.plugin.state.set_enabled(self.plugin._scope(event), "blocked", False)
        self.assert_image_results(await collect(self.plugin.on_message(event)))
        kind, payload = self.backend.panel_calls[-1]
        self.assertEqual(kind, "search")
        self.assertEqual(payload["query"], "示例")
        self.assertEqual([item["key"] for item in payload["items"]], ["sample"])
        self.assertEqual(payload["total"], 1)

        self.backend.memes = [make_meme(f"q{i:02}", f"示例{i}") for i in range(35)]
        self.assert_image_results(await collect(self.plugin.on_message(FakeEvent(text="/表情搜索 示例 2"))))
        kind, payload = self.backend.panel_calls[-1]
        self.assertEqual(kind, "search")
        self.assertEqual(payload["query"], "示例")
        self.assertEqual((payload["page"], payload["pages"], payload["total"]), (2, 2, 35))
        self.assertEqual([item["key"] for item in payload["items"]], [f"q{i:02}" for i in range(30, 35)])

    async def test_tools_and_statistics_are_native_image_panels(self):
        self.assert_image_results(await collect(self.plugin.on_message(FakeEvent(text="/图片操作"))))
        self.assertEqual(self.backend.panel_calls[-1][0], "tools")
        event = FakeEvent(text="/表情调用统计")
        self.plugin.state.record(self.plugin._scope(event), self.plugin._user(event), "sample")
        self.plugin.state.record(self.plugin._scope(event), self.plugin._user(event), "sample")
        self.assert_image_results(await collect(self.plugin.on_message(event)))
        kind, payload = self.backend.panel_calls[-1]
        self.assertEqual(kind, "statistics")
        self.assertEqual([(row["key"], row["count"]) for row in payload["rows"]], [("sample", 2)])

    async def test_details_send_metadata_panel_and_generator_preview_separately(self):
        self.meme.info.tags = ["测试标签"]
        self.meme.info.params.default_texts = ["默认文字"]
        self.meme.info.params.options = [SimpleNamespace(names=["--number"], args=[SimpleNamespace(name="number")], help_text="图片编号")]
        event = FakeEvent(text="/表情详情 sample")
        results = await collect(self.plugin.on_message(event))
        self.assert_image_results(results, count=2)
        kind, payload = self.backend.panel_calls[-1]
        self.assertEqual(kind, "detail")
        self.assertEqual(payload["key"], "sample")
        self.assertEqual(payload["keywords"], ["示例"])
        self.assertEqual(payload["tags"], ["测试标签"])
        self.assertEqual(payload["default_texts"], ["默认文字"])
        self.assertIn("--number", payload["options"][0])
        self.assertTrue(payload["enabled"])
        preview = results[-1].chain[0]
        self.assertEqual(base64.b64decode(preview.file.removeprefix("base64://")), b"GIF89a-preview")

    async def test_unrelated_reply_does_not_fetch_onebot_history(self):
        event = FakeEvent([Plain("普通聊天"), Reply(id="123")])
        event.bot = SimpleNamespace(get_msg=AsyncMock())
        self.assertEqual(await collect(self.plugin.on_message(event)), [])
        event.bot.get_msg.assert_not_awaited()
        self.assertFalse(event.stopped)

    async def test_plain_message_fallback_and_generation_statistics(self):
        event = FakeEvent([], text='/示例 "两个 字"')
        results = await collect(self.plugin.on_message(event))
        self.assertTrue(event.stopped)
        self.assertEqual(self.backend.calls, [("sample", [], ["两个 字"], {})])
        image = results[0].chain[0]
        self.assertEqual(base64.b64decode(image.file.removeprefix("base64://")), b"GIF89a-generated")
        self.assertEqual(self.plugin.state.statistics(scope=self.plugin._scope(event)), [("sample", 1)])

    async def test_ordered_images_mentions_bot_mention_and_replied_images(self):
        self.meme.info.params.min_images = self.meme.info.params.max_images = 3
        self.meme.info.params.min_texts = self.meme.info.params.max_texts = 2
        first, replied = Image("first"), Image("reply")
        event = FakeEvent([At("999"), Plain("/示例 "), first, At("22"), Plain(' "两个 字" #指定名字'), Reply(chain=[replied, Plain('"引用 内容"')])])
        self.plugin._image_bytes = AsyncMock(side_effect=lambda image: image.file.encode())
        self.plugin._avatar = AsyncMock(return_value=("被提及者", b"avatar22"))
        results = await collect(self.plugin.on_message(event))
        self.assertEqual(len(results), 1)
        self.plugin._avatar.assert_awaited_once_with(event, "22")
        self.assertEqual(self.backend.calls[0][1], [("指定名字", b"first"), ("被提及者", b"avatar22"), ("", b"reply")])
        self.assertEqual(self.backend.calls[0][2], ["两个 字", "引用 内容"])

    async def test_reply_id_fetches_onebot_message(self):
        event = FakeEvent([Plain("/示例"), Reply(id="123")])
        event.bot = SimpleNamespace(get_msg=AsyncMock(return_value={"message": [{"type": "image", "data": {"url": "https://example.test/a.gif"}}, {"type": "text", "data": {"text": "引用文字"}}]}))
        raw, reply, sources = await self.plugin._message(event, include_reply=True)
        self.assertEqual(raw, "/示例")
        self.assertIn("引用文字", reply)
        self.assertEqual(next(iter(sources.values())).url, "https://example.test/a.gif")
        event.bot.get_msg.assert_awaited_once_with(message_id="123")

    async def test_admin_permission_is_platform_scoped_and_global_requires_bot_admin(self):
        group_admin = FakeEvent(text="/禁用表情 sample", raw={"sender": {"role": "admin"}})
        self.assertTrue(self.plugin._is_admin(group_admin))
        self.assertFalse(self.plugin._is_admin(group_admin, global_change=True))
        for event in [FakeEvent(raw={"sender": {"role": "member"}}), FakeEvent(platform_name="telegram", raw={"sender": {"role": "owner"}}), FakeEvent(group="", raw={"sender": {"role": "owner"}})]:
            self.assertFalse(self.plugin._is_admin(event))
        results = await collect(self.plugin.on_message(group_admin))
        self.assertIn("已禁用表情", text_of(results))
        self.assertFalse(self.plugin.state.enabled(self.plugin._scope(group_admin), "sample"))
        denied = FakeEvent(text="/全局禁用表情 sample", raw={"sender": {"role": "owner"}})
        self.assertIn("需要 AstrBot 管理员", text_of(await collect(self.plugin.on_message(denied))))
        administrator = FakeEvent(text="/全局禁用表情 sample", admin=True)
        self.assertIn("已全局禁用", text_of(await collect(self.plugin.on_message(administrator))))

    async def test_pending_inputs_are_scoped_by_sender_group_adapter_and_bot(self):
        self.config["command_prefixes"] = ["/"]
        self.config["params_mismatch_policy"] = {"too_few_image": "get"}
        self.meme.info.params.min_images = self.meme.info.params.max_images = 1
        start = FakeEvent(text="/示例 文字")
        results = await collect(self.plugin.on_message(start))
        self.assertIn("补充", text_of(results))
        own_key = (self.plugin._scope(start), self.plugin._user(start))
        self.assertIn(own_key, self.plugin.pending)
        for other in [FakeEvent([Image("other")], user="12"), FakeEvent([Image("other")], group="101"), FakeEvent([Image("other")], platform="adapter2"), FakeEvent([Image("other")], self_id="998")]:
            self.assertFalse(self.plugin._accepts(other))
            self.assertEqual(await collect(self.plugin.on_message(other)), [])
            self.assertIn(own_key, self.plugin.pending)
        follow_up = FakeEvent([Image("own")])
        self.plugin._image_bytes = AsyncMock(return_value=b"own-image")
        self.assertTrue(self.plugin._accepts(follow_up))
        await collect(self.plugin.on_message(follow_up))
        self.assertNotIn(own_key, self.plugin.pending)
        self.assertEqual(self.backend.calls[0][1:3], ([("", b"own-image")], ["文字"]))

    async def test_shortcut_seeded_special_tokens_are_literal_text(self):
        self.meme.info.shortcuts = [SimpleNamespace(pattern="快捷", names=[], texts=["自己", "@123", "#foo"], options={})]
        self.meme.info.params.min_texts = self.meme.info.params.max_texts = 3
        self.plugin._avatar = AsyncMock(side_effect=AssertionError("must not resolve seeded text as avatars"))
        await collect(self.plugin.on_message(FakeEvent(text="/快捷")))
        self.assertEqual(self.backend.calls[0][1:3], ([], ["自己", "@123", "#foo"]))
        self.plugin._avatar.assert_not_awaited()

    async def test_parameter_mismatch_prompt_drop_and_ignore(self):
        self.config["show_parameter_mismatch_tip"] = True
        prompt = FakeEvent(text="/示例 太 多")
        self.assertIn("文字数量不符", text_of(await collect(self.plugin.on_message(prompt))))
        self.assertTrue(prompt.stopped)
        self.assertEqual(self.backend.calls, [])

        self.config["params_mismatch_policy"] = {"too_much_text": "ignore"}
        ignored = FakeEvent(text="/示例 太 多")
        self.assertEqual(await collect(self.plugin.on_message(ignored)), [])
        self.assertFalse(ignored.stopped)
        self.assertEqual(self.backend.calls, [])

        self.config["params_mismatch_policy"] = {"too_much_text": "drop"}
        dropped = FakeEvent(text="/示例 太 多")
        self.assertEqual(len(await collect(self.plugin.on_message(dropped))), 1)
        self.assertEqual(self.backend.calls[0][2], ["太"])
        self.assertTrue(dropped.stopped)

    async def test_count_hint_switch_covers_default_off_and_enabled_in_all_chats(self):
        self.plugin._image_bytes = AsyncMock(return_value=b"input")
        for group in ["100", ""]:
            for setting in [None, False, True]:
                if setting is None:
                    self.config.pop("show_parameter_mismatch_tip", None)
                else:
                    self.config["show_parameter_mismatch_tip"] = setting
                for kind in ["image_few", "image_many", "text_few", "text_many"]:
                    with self.subTest(group=group, setting=setting, kind=kind):
                        params = self.meme.info.params
                        params.min_images, params.max_images = (1, 1) if kind == "image_few" else (0, 0)
                        params.min_texts = params.max_texts = 1
                        segments = [Plain("/示例 内容")]
                        if kind == "image_many":
                            segments.append(Image("input"))
                        elif kind == "text_few":
                            segments = [Plain("/示例")]
                        elif kind == "text_many":
                            segments = [Plain("/示例 太 多")]
                        event = FakeEvent(segments, group=group)
                        with self.assertLogs("memes.test", level="INFO") as logs:
                            results = await collect(self.plugin.on_message(event))
                        self.assertTrue(event.stopped)
                        if setting:
                            self.assertIn("数量不符", text_of(results))
                        else:
                            self.assertEqual(results, [])
                        self.assertIn("数量不符", "\n".join(logs.output))
                        self.assertEqual(self.backend.calls, [])
                        self.assertEqual(self.plugin.state.statistics(), [])

    async def test_failed_generation_is_not_recorded(self):
        self.backend.generate = lambda *args: (_ for _ in ()).throw(ValueError("bad image"))
        event = FakeEvent(text="/示例 内容")
        with self.assertLogs("memes.test", level="ERROR") as logs:
            results = await collect(self.plugin.on_message(event))
        self.assertEqual(results, [])
        self.assertTrue(event.stopped)
        self.assertIn("bad image", "\n".join(logs.output))
        self.assertEqual(self.plugin.state.statistics(), [])

    async def test_group_runtime_failures_are_logged_without_chat_reports(self):
        for error in [OSError("private path"), TimeoutError("request timeout"), RuntimeError("internal traceback")]:
            with self.subTest(error=type(error).__name__):
                def fail(*args):
                    raise error
                self.backend.generate = fail
                event = FakeEvent(text="/示例 内容")
                with self.assertLogs("memes.test", level="ERROR") as logs:
                    self.assertEqual(await collect(self.plugin.on_message(event)), [])
                self.assertTrue(event.stopped)
                self.assertIn(str(error), "\n".join(logs.output))
                self.assertEqual(self.plugin.state.statistics(), [])

    async def test_private_chat_retains_failure_feedback(self):
        self.backend.generate = lambda *args: (_ for _ in ()).throw(ValueError("bad image"))
        event = FakeEvent(text="/示例 内容", group="")
        with self.assertLogs("memes.test", level="ERROR"):
            results = await collect(self.plugin.on_message(event))
        self.assertIn("表情制作失败", text_of(results))
        self.assertTrue(event.stopped)

    async def test_group_network_failure_has_no_report(self):
        self.plugin._image_bytes = AsyncMock(side_effect=main.aiohttp.ClientError("download failed"))
        event = FakeEvent([Plain("/示例 内容"), Image("input")])
        with self.assertLogs("memes.test", level="ERROR") as logs:
            self.assertEqual(await collect(self.plugin.on_message(event)), [])
        self.assertTrue(event.stopped)
        self.assertIn("download failed", "\n".join(logs.output))

    async def test_pending_expiry_and_cancel_do_not_consume_other_users(self):
        event = FakeEvent(text="任意补充")
        key = (self.plugin._scope(event), self.plugin._user(event))
        self.plugin.pending[key] = main.Pending(self.meme, main.Inputs(), time.monotonic() - 1)
        self.assertFalse(self.plugin._accepts(event))
        self.assertEqual(await collect(self.plugin.on_message(event)), [])
        self.assertNotIn(key, self.plugin.pending)
        self.plugin.pending[key] = main.Pending(self.meme, main.Inputs(), time.monotonic() + 60)
        await collect(self.plugin.on_message(FakeEvent(text="/取消表情", user="12")))
        self.assertIn(key, self.plugin.pending)
        await collect(self.plugin.on_message(FakeEvent(text="/取消表情")))
        self.assertNotIn(key, self.plugin.pending)

    async def test_backend_startup_failure_remains_manageable_and_closes_session(self):
        self.plugin.backend = None
        with patch.object(main, "MemeBackend", side_effect=RuntimeError("engine unavailable")):
            with self.assertLogs("memes.test", level="ERROR"):
                await self.plugin.initialize()
        self.assertIn("engine unavailable", self.plugin.startup_error)
        event = FakeEvent(text="/表情列表")
        self.assertTrue(self.plugin._accepts(event))
        with self.assertLogs("memes.test", level="ERROR") as logs:
            self.assertEqual(await collect(self.plugin.on_message(event)), [])
        self.assertIn("engine unavailable", "\n".join(logs.output))
        self.assertTrue(event.stopped)
        with self.assertLogs("memes.test", level="ERROR"):
            results = await collect(self.plugin.on_message(FakeEvent(text="/表情列表", group="")))
        self.assertIn("引擎未就绪", text_of(results))
        session = self.plugin.http
        await self.plugin.terminate()
        self.plugin = None
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
