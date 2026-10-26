"""Chinese PNG menus rendered inside the isolated Python drawing worker."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1200
MARGIN = 48
CONTENT = WIDTH - MARGIN * 2
BACKGROUND = "#F3F6F2"
INK = "#202D4F"
MUTED = "#627080"
TEAL = "#087F81"
BLUE = "#4255A4"
BORDER = "#DCE4E3"
WHITE = "#FFFFFF"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value or [] if item is not None]


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _supports_chinese(font: ImageFont.FreeTypeFont) -> bool:
    first = font.getmask("中")
    second = font.getmask("文")
    missing = font.getmask("\U0010FFFF")
    return bool(first.getbbox() and second.getbbox()) and (
        first.size, bytes(first)
    ) not in {(second.size, bytes(second)), (missing.size, bytes(missing))}


@lru_cache(maxsize=8)
def _font_paths(custom: str = "") -> tuple[str, str]:
    if custom:
        candidates = [(str(Path(custom).expanduser()), str(Path(custom).expanduser()))]
    else:
        windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        candidates = [
            (str(windows / "msyh.ttc"), str(windows / "msyhbd.ttc")),
            (str(windows / "simhei.ttf"), str(windows / "simhei.ttf")),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
            ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
            ("/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"),
        ]
    for regular, bold in candidates:
        try:
            font = ImageFont.truetype(regular, 28)
            if _supports_chinese(font):
                return regular, bold if Path(bold).is_file() else regular
        except (OSError, ValueError):
            continue
    raise ValueError("图片面板需要中文字体：请安装微软雅黑、Noto Sans CJK 或文泉驿字体，或配置 panel_font_path")


@lru_cache(maxsize=96)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _width(text: str, font: ImageFont.FreeTypeFont) -> float:
    return font.getlength(text)


def _ellipsis(text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    text = str(text).replace("\n", " ")
    if _width(text, font) <= width:
        return text
    if _width("…", font) > width:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _width(text[:middle] + "…", font) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + "…"


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int | None = None) -> list[str]:
    """Wrap by measured glyph width, preserving explicit paragraph breaks."""
    lines = []
    for paragraph in str(text).split("\n"):
        current = ""
        for character in paragraph:
            if current and _width(current + character, font) > width:
                lines.append(current.rstrip())
                current = character.lstrip()
            else:
                current += character
        lines.append(current.rstrip())
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsis(lines[-1] + "…", font, width)
    return lines


def _range(minimum: Any, maximum: Any) -> str:
    low, high = _integer(minimum), _integer(maximum)
    return str(low) if low == high else f"{low}–{high}"


class _Page:
    def __init__(self, title: str, subtitle: str, payload: dict[str, Any], badge: str = ""):
        self.regular, self.bold = _font_paths(str(payload.get("font_path") or ""))
        self.image = Image.new("RGB", (WIDTH, 6000), BACKGROUND)
        self.draw = ImageDraw.Draw(self.image)
        self.prefix = str(payload.get("prefix", ""))[:16]
        self.draw.ellipse((950, -180, 1370, 240), fill="#E0EEEA")
        self.draw.rounded_rectangle((48, 37, 80, 43), radius=3, fill=TEAL)
        self.text(94, 28, str(payload.get("brand") or "ASTRBOT"), 20, TEAL, True, max_width=710)
        if badge:
            self.pill(930, 27, badge, fill="#E4EEEC", color=TEAL, size=19, width=222, height=35)
        self.text(MARGIN, 82, title, 54, INK, True)
        self.text(MARGIN + 2, 157, subtitle, 25, MUTED, max_width=CONTENT)
        self.y = 218

    def font(self, size: int, bold: bool = False):
        return _font(self.bold if bold else self.regular, size)

    def text(self, x, y, value, size=24, color=INK, bold=False, max_width=None):
        value = str(value)
        font = self.font(size, bold)
        if max_width is not None:
            value = _ellipsis(value, font, max_width)
        box = self.draw.textbbox((0, 0), value, font=font)
        if y + box[3] - box[1] > self.image.height:
            raise ValueError("面板内容过长，请减少每页条目")
        self.draw.text((x, y - box[1]), value, font=font, fill=color)
        return box[3] - box[1]

    def paragraph(self, x, y, value, width, size=24, color=MUTED, bold=False, max_lines=None, spacing=10):
        font = self.font(size, bold)
        lines = _wrap(str(value), font, width, max_lines)
        line_height = size + spacing
        for index, line in enumerate(lines):
            self.text(x, y + index * line_height, line, size, color, bold)
        return len(lines) * line_height

    def card(self, x, y, width, height, fill=WHITE, outline=BORDER, radius=22):
        self.draw.rounded_rectangle((x, y + 3, x + width, y + height + 3), radius=radius, fill="#E2E9E4")
        self.draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=fill, outline=outline, width=1)

    def pill(self, x, y, text, fill="#E7F3EF", color=TEAL, size=18, width=None, height=30):
        font = self.font(size)
        width = width or int(_width(text, font)) + 24
        self.draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill)
        self.text(x + 12, y + (height - size) // 2 - 1, text, size, color, max_width=width - 24)
        return width

    def section(self, title, subtitle=""):
        self.text(MARGIN, self.y, title, 30, INK, True)
        if subtitle:
            self.text(MARGIN + 235, self.y + 6, subtitle, 20, MUTED, max_width=CONTENT - 235)
        self.y += 54

    def finish(self, footer=None) -> bytes:
        if footer is None:
            footer = f"使用 {self.prefix}表情指令 查看使用面板"
        self.draw.line((MARGIN, self.y + 28, WIDTH - MARGIN, self.y + 28), fill=BORDER, width=2)
        self.text(MARGIN, self.y + 51, "表情包制作 · AstrBot", 20, MUTED)
        self.text(MARGIN + 310, self.y + 51, footer, 20, MUTED, max_width=CONTENT - 310)
        result = self.image.crop((0, 0, WIDTH, self.y + 102))
        output = BytesIO()
        result.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _help(payload):
    total = _integer(payload.get("total"))
    badge = f"{total} 个模板等你来玩" if total else "聊天里的表情工具箱"
    page = _Page("表情工坊", "挑个模板，发张图片，把聊天变得更有趣。", payload, badge)
    p = page.prefix
    y = page.y
    page.card(MARGIN, y, CONTENT, 250, fill=INK, outline=INK, radius=28)
    page.text(80, y + 30, "从这条指令开始", 27, "#CED9EF")
    page.text(80, y + 82, p + "摸 自己", 49, WHITE, True, max_width=800)
    page.text(82, y + 152, "用自己的头像，做一张摸头表情。", 25, "#DDE7F1")
    first_chip = min(460, int(_width(p + "关键词 + 图片/文字", page.font(20))) + 24)
    second_chip = min(460, int(_width(p + "随机表情 + 图片/文字", page.font(20))) + 24)
    page.pill(80, y + 203, p + "关键词 + 图片/文字", fill="#3A4668", color=WHITE, size=20, width=first_chip, height=32)
    page.pill(104 + first_chip, y + 203, p + "随机表情 + 图片/文字", fill="#3A4668", color=WHITE, size=20, width=second_chip, height=32)
    page.draw.rounded_rectangle((947, y + 53, 1094, y + 179), radius=23, fill="#D4F1E8")
    page.draw.ellipse((989, y + 91, 1000, y + 102), fill=TEAL)
    page.draw.ellipse((1041, y + 91, 1052, y + 102), fill=TEAL)
    page.draw.arc((993, y + 97, 1052, y + 145), 15, 165, fill=TEAL, width=5)
    page.y += 292

    page.section("发现模板", "先找到想玩的，再查看它需要什么")
    gap = 16
    width = (CONTENT - gap * 2) // 3
    entries = [
        ("全部模板", p + "表情列表", "加页码即可翻页，例如 " + p + "表情列表 2"),
        ("搜索灵感", p + "表情搜索 关键词", "按关键词和标签寻找模板"),
        ("查看用法", p + "表情详情 关键词", "查看图片、文字要求与预览"),
    ]
    for index, (title, command, note) in enumerate(entries):
        x = MARGIN + index * (width + gap)
        page.card(x, page.y, width, 158)
        page.text(x + 20, page.y + 20, title, 21, MUTED)
        page.text(x + 20, page.y + 59, command, 24, BLUE, True, max_width=width - 40)
        page.paragraph(x + 20, page.y + 103, note, width - 40, 19, max_lines=2, spacing=7)
    page.y += 198

    page.section("图片这样给", "直接附图、引用消息、用户头像都可以")
    inputs = [
        ("01  直接附图", "在指令后附上需要处理的图片。"),
        ("02  引用图片", "回复一张图片，再发送制作指令。"),
        ("03  头像入图", "使用 自己 或 @某人 获取头像。"),
    ]
    for index, (title, note) in enumerate(inputs):
        x = MARGIN + index * (width + gap)
        page.card(x, page.y, width, 130, fill="#EAF2ED")
        page.text(x + 20, page.y + 23, title, 24, TEAL, True)
        page.paragraph(x + 20, page.y + 67, note, width - 40, 21, max_lines=2, spacing=8)
    page.y += 152
    page.text(MARGIN + 6, page.y, '文字含空格时加英文引号 "..."；#名字 可以指定图片名。', 22, MUTED)
    page.y += 66

    width = (CONTENT - 20) // 2
    for index, title in enumerate(("开关管理", "更多工具")):
        x = MARGIN + index * (width + 20)
        page.card(x, page.y, width, 336)
        page.text(x + 24, page.y + 25, title, 29, INK, True)
        if index == 0:
            page.paragraph(x + 24, page.y + 74, "会话开关：管理员\n全局开关：AstrBot 管理员", width - 48, 20, max_lines=2, spacing=8)
            commands = ["启用表情 关键词", "禁用表情 关键词", "全局启用表情 关键词", "全局禁用表情 关键词"]
        else:
            page.paragraph(x + 24, page.y + 74, "处理图片、查看使用记录，或取消等待中的补充输入。", width - 48, 20, max_lines=2, spacing=8)
            commands = ["图片操作", "表情调用统计", "我的今日表情调用统计", "取消表情"]
        for line, command in enumerate(commands):
            page.text(x + 24, page.y + 153 + line * 40, p + command, 23, BLUE, max_width=width - 48)
    page.y += 356
    return page.finish("头像能力取决于平台；引用图片可直接制作。")


def _catalog(payload, search=False):
    all_items = payload.get("items") or []
    items = all_items[:30]
    total = max(0, _integer(payload.get("total"), len(all_items)))
    pages = max(1, _integer(payload.get("pages"), 1))
    current = min(pages, max(1, _integer(payload.get("page"), 1)))
    title = str(payload.get("title") or ("搜索结果" if search else "表情模板"))
    if search:
        subtitle = f"关键词「{str(payload.get('query') or '')}」 · 找到 {total} 个可用模板"
    else:
        subtitle = f"第 {current} / {pages} 页 · 共 {total} 个可用模板"
    page = _Page(title, subtitle, payload, "先看名字，再发图片")
    p = page.prefix
    page.card(MARGIN, page.y, CONTENT, 65, fill="#E6F0EB")
    page.text(MARGIN + 22, page.y + 21, "制作：" + p + "关键词 + 图片/文字    ·    用法：" + p + "表情详情 key", 23, TEAL, max_width=CONTENT - 44)
    page.y += 86
    width, gap, height = (CONTENT - 18) // 2, 18, 128
    if not items:
        page.card(MARGIN, page.y, CONTENT, 240)
        page.text(MARGIN + 35, page.y + 55, "还没有找到可用模板", 33, INK, True)
        page.text(MARGIN + 35, page.y + 123, "换个关键词试试，或检查当前会话的表情开关。", 25, MUTED)
        page.y += 266
    for index, item in enumerate(items):
        x = MARGIN + index % 2 * (width + gap)
        y = page.y + index // 2 * (height + 14)
        page.card(x, y, width, height)
        names = " / ".join(_strings(item.get("keywords"))) or str(item.get("key", "未命名"))
        page.text(x + 20, y + 20, names, 27, INK, True, max_width=width - 86)
        number = (current - 1) * 30 + index + 1
        page.text(x + width - 55, y + 24, f"{number:02d}", 20, "#8F9BA6")
        page.text(x + 21, y + 60, str(item.get("key", "")), 20, MUTED, max_width=width - 42)
        image_count = _range(item.get("min_images"), item.get("max_images"))
        text_count = _range(item.get("min_texts"), item.get("max_texts"))
        image_label = "无需图片" if _integer(item.get("max_images")) == 0 else "图片 " + image_count
        text_label = "无需文字" if _integer(item.get("max_texts")) == 0 else "文字 " + text_count
        first = page.pill(x + 20, y + 94, image_label, size=17, height=26)
        second = page.pill(x + 31 + first, y + 94, text_label, fill="#EDF0FA", color=BLUE, size=17, height=26)
        if item.get("enabled") is False:
            page.pill(x + 42 + first + second, y + 94, "已禁用", fill="#F5E8E4", color="#9E5B46", size=17, height=26)
    if items:
        page.y += ((len(items) + 1) // 2) * (height + 14) + 2
    page.y += 14
    if search:
        note = str(payload.get("next_command") or "缩小关键词可继续查找；使用 key 查看模板详情。")
        if total > len(items):
            note = f"本页展示 {len(items)} 项。" + note
    elif current < pages:
        note = "下一页：" + p + f"表情列表 {current + 1}"
    elif current > 1:
        note = "已到最后一页 · 上一页：" + p + f"表情列表 {current - 1}"
    else:
        note = "使用 " + p + "表情搜索 关键词 寻找想玩的模板。"
    page.card(MARGIN, page.y, CONTENT, 66, fill="#E6F0EB")
    page.text(MARGIN + 22, page.y + 21, note, 23, TEAL, max_width=CONTENT - 44)
    page.y += 80
    return page.finish("图片、文字数量以模板详情为准。")


def _tools(payload):
    page = _Page("图片工具箱", "发送指令并附图，或回复一张图片后发送指令。", payload, "静态图 / GIF 都能处理")
    p = page.prefix
    entries = [
        ("水平翻转", "水平翻转", "左右镜像，换个方向看图片。"),
        ("竖直翻转", "竖直翻转", "上下翻转图片。"),
        ("旋转", "旋转 90", "按角度旋转，例如 90 或 180。"),
        ("缩放", "缩放 50%", "也可指定尺寸，例如 100x100。"),
        ("裁剪", "裁剪 2:1", "支持比例、尺寸或四点坐标。"),
        ("灰度图", "灰度图", "转换为黑白灰度画面。"),
        ("反相", "反相", "反转图片的色彩。"),
        ("横向拼接", "横向拼接", "附上多张图片，左右拼成一张。"),
        ("纵向拼接", "纵向拼接", "附上多张图片，上下拼成一张。"),
        ("GIF 分解", "gif分解", "把 GIF 拆成单张图片。"),
        ("GIF 合成", "gif合成 0.1", "把多张图合成 GIF，参数为秒。"),
        ("GIF 倒放", "gif倒放", "倒转动画的播放顺序。"),
        ("GIF 变速", "gif变速 2x", "支持倍率、百分比、FPS 或秒。"),
    ]
    width, gap, height = (CONTENT - 18) // 2, 18, 146
    for index, (title, command, note) in enumerate(entries):
        x = MARGIN + index % 2 * (width + gap)
        y = page.y + index // 2 * (height + 14)
        page.card(x, y, width, height)
        page.text(x + 22, y + 21, title, 28, INK, True)
        page.text(x + 22, y + 66, p + command, 23, BLUE)
        page.text(x + 22, y + 108, note, 20, MUTED, max_width=width - 44)
    page.y += ((len(entries) + 1) // 2) * (height + 14)
    return page.finish("一次处理的图片数量和输出上限受配置限制。")


def _statistics(payload):
    rows = (payload.get("rows") or [])[:20]
    page = _Page(str(payload.get("title") or "表情使用榜"), str(payload.get("subtitle") or "看看大家最近都在玩什么。"), payload, "每一次制作都有记录")
    if not rows:
        page.card(MARGIN, page.y, CONTENT, 242)
        page.text(MARGIN + 35, page.y + 54, "还没有制作记录", 35, INK, True)
        page.text(MARGIN + 35, page.y + 121, "试试 " + page.prefix + "摸 自己，开启第一张表情。", 25, MUTED)
        page.y += 266
    maximum = max([max(0, _integer(row.get("count"))) for row in rows], default=1) or 1
    for index, row in enumerate(rows):
        y = page.y + index * 94
        page.card(MARGIN, y, CONTENT, 80)
        page.pill(MARGIN + 18, y + 23, f"{index + 1:02d}", width=54, height=32, fill="#E5F0EB" if index < 3 else "#EFF2F3")
        label = str(row.get("label") or row.get("key") or "未命名")
        page.text(MARGIN + 92, y + 14, label, 24, INK, True, max_width=CONTENT - 265)
        count = max(0, _integer(row.get("count")))
        page.text(WIDTH - MARGIN - 146, y + 17, f"{count:,} 次", 22, TEAL, max_width=130)
        left, top, length = MARGIN + 94, y + 55, CONTENT - 265
        page.draw.rounded_rectangle((left, top, left + length, top + 9), radius=4, fill="#EBEFED")
        if count:
            page.draw.rounded_rectangle((left, top, left + max(8, int(length * count / maximum)), top + 9), radius=4, fill=TEAL if index < 3 else "#8094B9")
    if rows:
        page.y += len(rows) * 94
    return page.finish("可加 我的 / 全局 / 今日 / 本周 等条件筛选。")


def _detail(payload):
    keywords = _strings(payload.get("keywords"))
    key = str(payload.get("key") or "模板详情")
    title = keywords[0] if keywords else key
    page = _Page(_ellipsis(title, _font(_font_paths(str(payload.get("font_path") or ""))[1], 54), 1080), key, payload, "先看要求，再开始制作")
    aliases = " / ".join(keywords[1:])
    if aliases:
        page.text(MARGIN + 3, page.y, "其他关键词：" + aliases, 23, MUTED, max_width=CONTENT)
        page.y += 52
    width = (CONTENT - 20) // 2
    for index, (label, minimum, maximum, unit) in enumerate([
        ("需要图片", payload.get("min_images"), payload.get("max_images"), "张"),
        ("需要文字", payload.get("min_texts"), payload.get("max_texts"), "段"),
    ]):
        x = MARGIN + index * (width + 20)
        page.card(x, page.y, width, 148, fill="#E6F0EB" if index == 0 else "#EAEFFA")
        page.text(x + 25, page.y + 24, label, 24, TEAL if index == 0 else BLUE)
        page.text(x + 25, page.y + 75, _range(minimum, maximum) + " " + unit, 42, INK, True)
    page.y += 184
    sections = [
        ("默认文字", "\n".join(_strings(payload.get("default_texts"))) or "此模板没有预设文字。"),
        ("可用选项", "\n".join(_strings(payload.get("options"))[:20]) or "此模板没有额外选项。"),
    ]
    tags = " / ".join(_strings(payload.get("tags")))
    if tags:
        sections.insert(0, ("模板标签", tags))
    for title, content in sections:
        lines = _wrap(content, page.font(23), CONTENT - 52, max_lines=24)
        height = 91 + len(lines) * 34
        page.card(MARGIN, page.y, CONTENT, height)
        page.text(MARGIN + 26, page.y + 23, title, 27, INK, True)
        for index, line in enumerate(lines):
            page.text(MARGIN + 26, page.y + 74 + index * 34, line, 23, MUTED)
        page.y += height + 20
    if payload.get("enabled") is False:
        page.card(MARGIN, page.y, CONTENT, 70, fill="#F4E7E2")
        page.text(MARGIN + 24, page.y + 23, "当前会话已禁用此模板，请联系管理员启用。", 23, "#9E5B46")
        page.y += 86
    page.card(MARGIN, page.y, CONTENT, 75, fill="#E6F0EB")
    page.text(MARGIN + 24, page.y + 25, "制作指令：" + page.prefix + key + " + 图片/文字", 25, TEAL, max_width=CONTENT - 48)
    page.y += 91
    return page.finish("预览图片会随模板详情一同发送。")


def render_panel(kind: str, payload: dict[str, Any] | None = None) -> bytes:
    """Render a bounded, self-contained PNG rather than relying on chat markdown."""
    payload = payload or {}
    handlers = {
        "help": _help,
        "catalog": _catalog,
        "search": lambda values: _catalog(values, search=True),
        "tools": _tools,
        "statistics": _statistics,
        "detail": _detail,
    }
    if kind not in handlers:
        raise ValueError(f"未知的图片面板：{kind}")
    return handlers[kind](payload)
