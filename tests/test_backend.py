"""Integration checks against the real Python engine, without asset downloads."""

import importlib.util
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


PLUGIN = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("memes_engine_backend", PLUGIN / "engine_backend.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
EngineBackend = _module.EngineBackend


def isolate_engine_directories():
    """Point Python engine configuration into test work files before import."""
    if "meme_generator" in sys.modules:
        return
    package = importlib.util.find_spec("meme_generator")
    directory = Path(package.origin).parent
    spec = importlib.util.spec_from_file_location("meme_generator.dirs", directory / "dirs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = Path(os.environ.get("MEMES_TEST_RUNTIME", PLUGIN / "work" / "test-runtime"))
    module.BASE_CACHE_DIR = runtime / "cache"
    module.BASE_CONFIG_DIR = runtime / "config"
    module.BASE_DATA_DIR = runtime / "data"
    sys.modules[spec.name] = module


try:
    PYTHON_ENGINE_INSTALLED = version("meme-generator").startswith("0.1.")
except PackageNotFoundError:
    PYTHON_ENGINE_INSTALLED = False


@unittest.skipUnless(PYTHON_ENGINE_INSTALLED, "Run with the isolated Python meme-generator 0.1 environment")
class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        isolate_engine_directories()
        cls.backend = EngineBackend()
        from PIL import Image

        cls.Image = Image
        cls.image = cls.png()

    @classmethod
    def png(cls, width=64, height=48, red=20):
        image = cls.Image.new("RGBA", (width, height), (red, 80, 160, 255))
        image.putpixel((0, 0), (255, 0, 0, 255))
        image.putpixel((width - 1, height - 1), (0, 255, 0, 255))
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @classmethod
    def inspect(cls, data):
        with cls.Image.open(BytesIO(data)) as image:
            frames, durations = [], []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                frames.append(image.convert("RGBA"))
                durations.append(image.info.get("duration", 0))
            return image.size, frames, durations

    def animated(self, durations=(80, 160)):
        frames = [self.Image.open(BytesIO(self.png(red=red))).convert("RGBA") for red in (20, 180)]
        output = BytesIO()
        frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                       duration=list(durations), loop=0, disposal=2, optimize=False)
        return output.getvalue()

    def test_real_python_engine_catalog_lookup_search_and_aliases(self):
        self.assertEqual(self.backend._engine.__version__, "0.1.14")
        self.assertGreater(len(self.backend.memes), 0)
        meme = self.backend.find("PIXELATE")[0]
        self.assertTrue(callable(meme.native))
        self.assertTrue(hasattr(meme.native, "params_type"))
        self.assertIn(meme, self.backend.find(meme.info.keywords[0]))
        self.assertIn(meme, self.backend.search(meme.info.keywords[0]))
        for entry in self.backend.memes:
            with self.subTest(key=entry.key):
                self.assertEqual(self.backend.find(entry.key), [entry])
                self.assertEqual(entry.info.params.min_images, entry.native.params_type.min_images)
        for entries in self.backend._names.values():
            self.assertEqual(len(entries), len({entry.key for entry in entries}))

    def test_real_python_static_meme_render(self):
        result = self.backend.generate(self.backend.find("pixelate")[0], [("测试", self.image)], [], {"number": 3})
        self.assertIsInstance(result, bytes)
        self.assertEqual(self.inspect(result)[0], (64, 48))

    def test_real_python_animated_meme_render(self):
        result = self.backend.generate(self.backend.find("turn")[0], [("测试", self.image)], [], {})
        size, frames, durations = self.inspect(result)
        self.assertEqual(size, (250, 250))
        self.assertGreater(len(frames), 1)
        self.assertEqual(set(durations), {50})

    def test_python_preview_returns_complete_buffer(self):
        with patch("meme_generator.meme.random_image", return_value=self.image):
            result = self.backend.preview(self.backend.find("pixelate")[0])
        self.assertEqual(self.inspect(result)[0], (64, 48))

    def test_image_names_are_passed_to_python_user_infos(self):
        engine = self.backend._engine
        captured = []

        def render(images, texts, args):
            captured.extend(user.name for user in args.user_infos)
            return BytesIO(self.image)

        native = engine.Meme("names_test", render, engine.MemeParamsType(min_images=1, max_images=1))
        options = {"unrelated": 1}
        result = self.backend.generate(_module.PythonMeme(native), [("昵称", self.image)], [], options)
        self.assertEqual(result, self.image)
        self.assertEqual(captured, ["昵称"])
        self.assertEqual(options, {"unrelated": 1})

    def test_python_exceptions_become_friendly_values(self):
        meme = self.backend.find("pixelate")[0]
        with self.assertRaisesRegex(ValueError, "图片数量不符"):
            self.backend.generate(meme, [], [], {})
        with self.assertRaisesRegex(ValueError, "图片加载失败"):
            self.backend.generate(meme, [("broken", b"invalid")], [], {})
        with self.assertRaisesRegex(ValueError, "像素化大小"):
            self.backend.generate(meme, [("test", self.image)], [], {"number": 0})

    def test_rust_engine_version_is_rejected(self):
        with patch.object(self.backend._engine, "__version__", "0.2.3"):
            with self.assertRaisesRegex(RuntimeError, "Python 引擎"):
                EngineBackend()

    def test_resource_checker_runs_async_python_download_function(self):
        with patch("meme_generator.download.check_resources", new_callable=AsyncMock) as check:
            self.backend.check_resources()
        check.assert_awaited_once_with()

    def test_rotate_resize_crop_and_merge_geometry(self):
        for command, images, argument, dimensions in [
            ("旋转", [self.image], "90", (48, 64)),
            ("旋转", [self.image], "", (48, 64)),
            ("缩放", [self.image], "50%", (32, 24)),
            ("缩放", [self.image], "32x", (32, 24)),
            ("裁剪", [self.image], "1:1", (48, 48)),
            ("横向拼接", [self.image, self.image], "", (128, 48)),
            ("纵向拼接", [self.image, self.image], "", (64, 96)),
        ]:
            with self.subTest(command=command, argument=argument):
                output = self.backend.operate(command, images, argument)
                self.assertIsInstance(output, bytes)
                self.assertEqual(self.inspect(output)[0], dimensions)

    def test_flip_and_color_pixel_results(self):
        flipped = self.backend.operate("左翻", [self.image])
        self.assertEqual(self.inspect(flipped)[1][0].getpixel((63, 0)), (255, 0, 0, 255))
        vertical = self.backend.operate("上翻", [self.image])
        self.assertEqual(self.inspect(vertical)[1][0].getpixel((0, 47)), (255, 0, 0, 255))
        grayscale = self.backend.operate("黑白", [self.image])
        pixel = self.inspect(grayscale)[1][0].getpixel((20, 20))
        self.assertEqual(pixel[0], pixel[1])
        self.assertEqual(pixel[1], pixel[2])
        inverted = self.backend.operate("反色", [self.image])
        self.assertEqual(self.inspect(inverted)[1][0].getpixel((20, 20)), (235, 175, 95, 255))

    def test_gif_roundtrip(self):
        gif = self.backend.operate("gif合成", [self.image, self.png(red=180)], "0.1")
        self.assertIsInstance(gif, bytes)
        frames = self.backend.operate("gif分解", [gif])
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(self.inspect(frame)[0] == (64, 48) for frame in frames))
        self.assertEqual(self.inspect(gif)[2], [100, 100])

    def test_gif_transform_and_reverse_preserve_individual_frame_timing(self):
        gif = self.animated()
        for command, argument in [("水平翻转", ""), ("旋转", "90"), ("缩放", "50%"),
                                  ("裁剪", "1:1"), ("灰度图", ""), ("反相", "")]:
            with self.subTest(command=command):
                output = self.backend.operate(command, [gif], argument)
                self.assertEqual(self.inspect(output)[2], [80, 160])
        reversed_gif = self.backend.operate("gif倒放", [gif])
        self.assertEqual(self.inspect(reversed_gif)[2], [160, 80])
        self.assertGreater(self.inspect(reversed_gif)[1][0].getpixel((20, 20))[0], 100)

    def test_gif_speed_preserves_relative_frame_durations(self):
        gif = self.animated()
        self.assertEqual(self.inspect(self.backend.operate("gif变速", [gif], "0.5x"))[2], [160, 320])
        self.assertEqual(self.inspect(self.backend.operate("gif变速", [gif], "50%"))[2], [160, 320])
        self.assertEqual(self.inspect(self.backend.operate("gif变速", [gif], "20FPS"))[2], [50, 50])
        self.assertEqual(self.inspect(self.backend.operate("gif变速", [gif], "0.05s"))[2], [50, 50])

    def test_merging_static_and_animated_preserves_animation(self):
        result = self.backend.operate("横向拼接", [self.image, self.animated()])
        size, frames, durations = self.inspect(result)
        self.assertEqual(size, (128, 48))
        self.assertEqual(len(frames), 2)
        self.assertEqual(durations, [80, 160])

    def test_invalid_inputs_return_explanations(self):
        for command, argument in [
            ("缩放", "0x2"), ("缩放", "0%"), ("裁剪", "0:1"),
            ("裁剪", "0,0,100,100"), ("gif变速", "0fps"),
            ("gif变速", "0x"), ("gif变速", "100fps"),
            ("旋转", "nan"), ("gif合成", "-1"),
        ]:
            with self.subTest(command=command, argument=argument):
                self.assertIsInstance(self.backend.operate(command, [self.image], argument), str)
        self.assertIn("图片解码出错", self.backend.operate("水平翻转", [b"bad"]))
        self.assertIsInstance(self.backend.operate("水平翻转", []), str)
        self.assertIsInstance(self.backend.operate("水平翻转", [self.image, self.image]), str)


if __name__ == "__main__":
    unittest.main()
