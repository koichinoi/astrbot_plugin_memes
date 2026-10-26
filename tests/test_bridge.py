"""Exercise the actual isolated engine from a host with different dependencies."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
import zlib


PLUGIN = Path(__file__).resolve().parents[1]
ENGINE_PYTHON = os.environ.get("MEMES_TEST_PYTHON", "")


def engine_modules():
    return {name for name in sys.modules if name == "PIL" or name.startswith("PIL.") or name == "meme_generator" or name.startswith("meme_generator.")}


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"memes_bridge_test_{name}", PLUGIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def png(red=20, width=64, height=48):
    """Build a valid small RGBA PNG using only the host standard library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes((red, 80, 160, 255)) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def dimensions(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                raise AssertionError("Invalid JPEG segment")
            marker = data[offset + 1]
            offset += 2
            if marker in {0xC0, 0xC1, 0xC2}:
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                return width, height
            offset += struct.unpack(">H", data[offset:offset + 2])[0]
    raise AssertionError("Unrecognized or incomplete image")


@unittest.skipUnless(ENGINE_PYTHON, "Set MEMES_TEST_PYTHON to the isolated Python engine interpreter")
class IsolatedBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules_before = engine_modules()
        cls.bridge = load_module("backend")
        cls.parser = load_module("parser")
        cls.temporary = TemporaryDirectory(prefix="memes-bridge-")
        try:
            cls.backend = cls.bridge.MemeBackend(runtime_dir=cls.temporary.name, python_executable=ENGINE_PYTHON)
        except BaseException:
            cls.temporary.cleanup()
            raise
        cls.image = png()

    @classmethod
    def tearDownClass(cls):
        process = cls.backend._process
        try:
            cls.backend.close()
            if process.poll() is None:
                raise AssertionError("Closing the bridge left its worker running")
            added = engine_modules() - cls.modules_before
            if added:
                raise AssertionError(f"Engine dependencies leaked into the host: {sorted(added)}")
        finally:
            cls.temporary.cleanup()

    def test_catalog_lookup_search_and_remote_python_options(self):
        self.assertEqual(self.backend._rpc("version"), "0.1.14")
        self.assertEqual(len(self.backend.memes), 295)
        pixelate = self.backend.find("PIXELATE")[0]
        self.assertFalse(hasattr(pixelate, "native"))
        self.assertIn(pixelate, self.backend.search(pixelate.info.keywords[0]))
        self.assertEqual(self.backend.find("摸")[0].key, "petpet")
        self.assertEqual(self.backend.find("missing-meme"), [])
        for meme in self.backend.memes:
            with self.subTest(key=meme.key):
                self.assertEqual(self.backend.find(meme.key), [meme])
        self.assertEqual(
            self.parser.parse_options(self.backend.find("petpet")[0], ["圆"]),
            ([], {"circle": True}),
        )
        match = self.parser.match_meme(self.backend.memes, "低情商 不行 高情商 很好")[0]
        self.assertEqual(match.meme.key, "high_EQ")
        self.assertEqual(match.arguments, ["不行", "很好"])
        self.assertEqual(
            self.parser.parse_options(match.meme, match.arguments + self.parser.tokenize(match.remainder)),
            (["不行", "很好"], {}),
        )
        self.assertEqual(engine_modules() - self.modules_before, set())

    def test_real_static_and_animated_generation_cross_process(self):
        jpeg = self.backend.generate(self.backend.find("pixelate")[0], [("测试昵称", self.image)], [], {"number": 3})
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertTrue(jpeg.endswith(b"\xff\xd9"))
        self.assertEqual(dimensions(jpeg), (64, 48))
        animation = self.backend.generate(self.backend.find("turn")[0], [("测试昵称", self.image)], [], {})
        self.assertIn(animation[:6], {b"GIF87a", b"GIF89a"})
        self.assertEqual(dimensions(animation), (250, 250))
        self.assertTrue(animation.endswith(b";"))

    def test_gif_operation_roundtrip_and_text_feedback(self):
        animation = self.backend.operate("gif合成", [self.image, png(red=180)], "0.1")
        self.assertIsInstance(animation, bytes)
        self.assertEqual(dimensions(animation), (64, 48))
        frames = self.backend.operate("gif分解", [animation])
        self.assertIsInstance(frames, list)
        self.assertEqual(len(frames), 2)
        for frame in frames:
            self.assertTrue(frame.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(dimensions(frame), (64, 48))
        self.assertIn("请附带图片", self.backend.operate("缩放", []))
        self.assertIn("帧间隔", self.backend.operate("gif合成", [self.image, self.image], "0.001"))
        self.assertIn("裁剪", self.backend.operate("裁剪", [self.image], "0:1"))

    def test_worker_recovers_after_invalid_options_and_image_bytes(self):
        process = self.backend._process
        meme = self.backend.find("pixelate")[0]
        for operation, expected in [
            (lambda: self.parser.parse_options(meme, ["--number=invalid"]), "整数"),
            (lambda: self.parser.parse_options(meme, ["--missing"]), "未知"),
            (lambda: self.backend.generate(meme, [("损坏图片", b"invalid")], [], {}), "图片加载失败"),
            (lambda: self.backend.generate(meme, [("昵称", self.image)], [], {"number": 0}), "像素化大小"),
        ]:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                operation()
            self.assertIsNone(process.poll())
        valid = self.backend.generate(meme, [("昵称", self.image)], [], {"number": 2})
        self.assertEqual(dimensions(valid), (64, 48))

    def test_concurrent_calls_keep_each_response_with_its_request(self):
        meme = self.backend.find("pixelate")[0]

        def render(number):
            texts, options = self.parser.parse_options(meme, [f"--number={number}", f"文字{number}"])
            output = self.backend.generate(meme, [(f"昵称{number}", self.image)], [], options)
            return texts, options, dimensions(output)

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(render, range(1, 9)))
        self.assertEqual(results, [([f"文字{number}"], {"number": number}, (64, 48)) for number in range(1, 9)])
        self.assertIsNone(self.backend._process.poll())

    def test_help_and_catalog_panels_cross_process_as_png_without_host_image_dependencies(self):
        help_image = self.backend.render_panel("help", {"prefix": "/"})
        self.assertTrue(help_image.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = dimensions(help_image)
        self.assertGreaterEqual(width, 1000)
        self.assertLessEqual(width, 1600)
        self.assertGreater(height, 600)
        self.assertLess(height, 6000)

        items = []
        for meme in self.backend.memes[:30]:
            params = meme.info.params
            items.append({
                "key": meme.key,
                "keywords": meme.info.keywords,
                "min_images": params.min_images,
                "max_images": params.max_images,
                "min_texts": params.min_texts,
                "max_texts": params.max_texts,
            })
        catalog = self.backend.render_panel("catalog", {"page": 1, "pages": 10, "total": len(self.backend.memes), "items": items, "prefix": "/"})
        self.assertTrue(catalog.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = dimensions(catalog)
        self.assertGreaterEqual(width, 1000)
        self.assertGreater(height, 800)
        self.assertLess(height, 6000)
        self.assertNotEqual(catalog, help_image)
        self.assertEqual(engine_modules() - self.modules_before, set())


if __name__ == "__main__":
    unittest.main()
