"""Visual-output and overflow regressions for the chat image menus."""

import importlib.util
from io import BytesIO
import os
from pathlib import Path
import unittest

try:
    from PIL import Image, ImageFont
except ImportError:
    Image = ImageFont = None


PLUGIN = Path(__file__).resolve().parents[1]


@unittest.skipIf(Image is None, "Run panel rendering tests in the drawing environment")
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("memes_panel_test", PLUGIN / "panel.py")
        cls.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.panel)
        try:
            cls.regular, cls.bold = cls.panel._font_paths()
        except ValueError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def inspect(self, kind, payload=None, min_height=400):
        data = self.panel.render_panel(kind, payload)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(data)) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.width, 1200)
            self.assertGreaterEqual(image.height, min_height)
            self.assertLess(image.height, 6000)
            self.assertGreater(len(image.getcolors(maxcolors=1000000)), 50)
            return image.size

    def item(self, index, long=False):
        return {
            "key": ("unusually_long_template_identifier_" * 6 if long else "template_") + str(index),
            "keywords": ["一个很长的中文表情名字" * (18 if long else 1), "备用别名", "另一个名字"],
            "min_images": index % 2,
            "max_images": index % 3,
            "min_texts": 0,
            "max_texts": index % 5,
            "enabled": index % 3 != 0,
        }

    def test_help_is_an_actual_colored_png(self):
        size = self.inspect("help", {"total": 295, "prefix": "/"}, min_height=1100)
        self.assertLess(size[1], 2000)

    def test_catalog_thirty_long_items_stays_bounded(self):
        payload = {"items": [self.item(index, long=True) for index in range(30)], "page": 4, "pages": 10, "total": 295}
        size = self.inspect("catalog", payload, min_height=2000)
        self.assertLess(size[1], 3500)
        self.assertEqual(self.inspect("catalog", {**payload, "items": payload["items"] * 10}), size)

    def test_empty_and_short_search_results_have_complete_panels(self):
        self.inspect("catalog", {"items": [], "total": 0})
        self.inspect("search", {"items": [], "query": "找不到的模板", "total": 0})
        self.inspect("search", {"items": [self.item(1)], "query": "一个很长的关键词" * 40, "total": 1})

    def test_stats_tools_and_detail_cover_empty_and_long_content(self):
        self.inspect("statistics", {"rows": []})
        self.inspect("statistics", {"rows": [{"label": "很长的模板名字" * 30, "count": 100 - index} for index in range(20)]}, min_height=1600)
        self.inspect("tools", {"prefix": "/"}, min_height=1000)
        self.inspect("detail", {
            "key": "pixelate", "keywords": ["超长表情名" * 40, "别名" * 80], "tags": ["常用", "图片"],
            "min_images": 1, "max_images": 2, "min_texts": 0, "max_texts": 1,
            "default_texts": ["默认文字" * 200], "options": ["--选项 需要输入的值：" + "说明" * 100] * 20,
            "enabled": False,
        }, min_height=1000)

    def test_wrapping_and_ellipsis_fit_the_available_width(self):
        font = self.panel._font(self.regular, 24)
        text = "中文夹杂 /very-long-command-name 以及数字12345" * 8
        for width in (130, 300, 600):
            with self.subTest(width=width):
                lines = self.panel._wrap(text, font, width, max_lines=4)
                self.assertLessEqual(len(lines), 4)
                self.assertTrue(all(self.panel._width(line, font) <= width for line in lines))
                ellipsis = self.panel._ellipsis(text, font, width)
                self.assertTrue(ellipsis.endswith("…"))
                self.assertLessEqual(self.panel._width(ellipsis, font), width)

    def test_missing_or_non_chinese_custom_font_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "中文字体"):
            self.panel.render_panel("help", {"font_path": str(PLUGIN / "absent-font.ttf")})
        latin = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf"
        if latin.is_file():
            self.assertFalse(self.panel._supports_chinese(ImageFont.truetype(str(latin), 26)))
            with self.assertRaisesRegex(ValueError, "中文字体"):
                self.panel.render_panel("help", {"font_path": str(latin)})
        with self.assertRaisesRegex(ValueError, "未知"):
            self.panel.render_panel("unsupported", {})


if __name__ == "__main__":
    unittest.main()
