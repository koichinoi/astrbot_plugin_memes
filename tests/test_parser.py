import importlib.util
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"memes_test_{name}", Path(__file__).resolve().parents[1] / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser = load_module("parser")


class BooleanOption:
    def __init__(self, name="mirror", default=False, short=True, long=True, short_aliases=None, long_aliases=None):
        self.name = name
        self.default = default
        self.parser_flags = SimpleNamespace(short=short, long=long, short_aliases=short_aliases or [], long_aliases=long_aliases or [])


class IntegerOption(BooleanOption):
    pass


class FloatOption(BooleanOption):
    pass


class StringOption(BooleanOption):
    pass


def make_meme(key="sample", keywords=None, options=None, shortcuts=None):
    return SimpleNamespace(key=key, info=SimpleNamespace(keywords=keywords or ["示例"], params=SimpleNamespace(options=options or []), shortcuts=shortcuts or []))


class TokenTests(unittest.TestCase):
    def test_quotes_empty_tokens_unicode_and_literal_backslashes(self):
        self.assertEqual(parser.tokenize('甲 "乙 丙" \'丁 戊\' "" C:\\meme\\new #名字'), ["甲", "乙 丙", "丁 戊", "", r"C:\meme\new", "#名字"])

    def test_unclosed_quote_is_friendly(self):
        with self.assertRaisesRegex(ValueError, "引号"):
            parser.tokenize('"bad')

    def test_longest_prefix_and_empty_prefix(self):
        self.assertEqual(parser.strip_prefix("  //示例 文本", ["", "/", "//"]), "示例 文本")
        self.assertEqual(parser.strip_prefix("示例", ["", "/"]), "示例")
        self.assertIsNone(parser.strip_prefix("示例", ["/"]))


class OptionTests(unittest.TestCase):
    def setUp(self):
        self.boolean = BooleanOption(short_aliases=["翻转"], long_aliases=["镜像"])
        self.integer = IntegerOption("count")
        self.integer.min = 1
        self.integer.max = 5
        self.floating = FloatOption("ratio")
        self.string = StringOption("color", short=False)
        self.string.choices = ["red", "blue sky"]
        self.meme = make_meme(options=[self.boolean, self.integer, self.floating, self.string])

    def test_native_types_aliases_inline_and_separate_values(self):
        texts, options = parser.parse_options(self.meme, parser.tokenize('甲 -翻转 -c 3 --ratio=-0.25 --color "blue sky" 乙'))
        self.assertEqual(texts, ["甲", "乙"])
        self.assertEqual(options, {"mirror": True, "count": 3, "ratio": -0.25, "color": "blue sky"})
        self.assertEqual(self.boolean.parser_flags.short_aliases, ["翻转"])
        self.assertEqual(self.boolean.parser_flags.long_aliases, ["镜像"])

    def test_boolean_default_true_toggles_and_explicit_false(self):
        self.boolean.default = True
        self.assertEqual(parser.parse_options(self.meme, ["-m"])[1], {"mirror": False})
        self.assertEqual(parser.parse_options(self.meme, ["--镜像=false"])[1], {"mirror": False})
        self.assertEqual(parser.parse_options(self.meme, ["--mirror=yes"])[1], {"mirror": True})

    def test_defaults_not_returned_and_last_explicit_value_wins(self):
        self.assertEqual(parser.parse_options(self.meme, ["你好"]), (["你好"], {}))
        self.assertEqual(parser.parse_options(self.meme, ["-c", "1", "--count=4"])[1], {"count": 4})

    def test_escape_negative_text_and_lone_hyphen(self):
        self.assertEqual(parser.parse_options(self.meme, ["-2", "-.5", "-", "--", "--bad", "-m"]), (["-2", "-.5", "-", "--bad", "-m"], {}))

    def test_missing_unknown_and_invalid_values(self):
        for tokens, message in [(["--count"], "缺少"), (["--count", "--mirror"], "缺少"), (["--missing"], "未知"), (["--count=3.5"], "整数"), (["--count=6"], "不大于"), (["--count=0"], "不小于"), (["--ratio=nan"], "有限"), (["--color=green"], "可选"), (["--mirror=maybe"], "true 或 false")]:
            with self.subTest(tokens=tokens):
                with self.assertRaisesRegex(ValueError, message):
                    parser.parse_options(self.meme, tokens)

    def test_short_and_long_flags_can_be_disabled(self):
        item = BooleanOption(short=False, long=False, short_aliases=["翻"], long_aliases=["开关"])
        meme = make_meme(options=[item])
        self.assertEqual(parser.parse_options(meme, ["-翻", "--开关=false"])[1], {"mirror": False})
        with self.assertRaisesRegex(ValueError, "未知"):
            parser.parse_options(meme, ["--mirror"])

    def test_native_minimum_maximum(self):
        self.integer.min = None
        self.integer.max = None
        self.integer.minimum = 2
        self.integer.maximum = 4
        self.floating.minimum = -0.5
        self.floating.maximum = 0.5
        self.assertEqual(parser.parse_options(self.meme, ["--count=3", "--ratio=0.5"])[1], {"count": 3, "ratio": 0.5})
        for tokens in (["--count=1"], ["--count=5"], ["--ratio=0.6"]):
            with self.subTest(tokens=tokens):
                with self.assertRaisesRegex(ValueError, "不小于|不大于"):
                    parser.parse_options(self.meme, tokens)


class MatchTests(unittest.TestCase):
    def test_longest_name_boundary_key_and_case(self):
        short = make_meme("short", ["你好"])
        long = make_meme("long", ["你好 世界"])
        self.assertEqual([match.meme.key for match in parser.match_meme([short, long], "你好 世界 参数")], ["long"])
        self.assertEqual(parser.match_meme([short], "你好啊"), [])
        self.assertEqual(parser.match_meme([short], "SHORT 参数")[0].remainder, "参数")

    def test_shared_keyword_and_duplicate_alias(self):
        first = make_meme("sample", ["sample", "SAMPLE"])
        second = make_meme("second", ["sample"])
        self.assertEqual([m.meme.key for m in parser.match_meme([first, second], "sample")], ["sample", "second"])

    def test_overlapping_keyword_shortcut_deduplicates_and_keeps_templates(self):
        shortcut = SimpleNamespace(pattern="示例", names=["小明"], texts=["默认内容"], options={"mirror": True})
        meme = make_meme(shortcuts=[shortcut])
        matches = parser.match_meme([meme], "示例 后续文字")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].names, ["小明"])
        self.assertEqual(matches[0].texts, ["默认内容"])
        self.assertEqual(matches[0].options, {"mirror": True})
        self.assertEqual(matches[0].remainder, "后续文字")

    def test_rust_named_shortcut_fields_and_unparsed_remainder(self):
        shortcut = SimpleNamespace(pattern=r"给(?<name>[^\s]+)说(?<word>[^\s]+)", names=["{name}"], texts=["{word}"], options={"title": "{name}的图", "mirror": True})
        meme = make_meme(shortcuts=[shortcut])
        match = parser.match_meme([meme], '给小明说你好 "另一 段" --mirror=false')[0]
        self.assertEqual(match.names, ["小明"])
        self.assertEqual(match.texts, ["你好"])
        self.assertEqual(match.options, {"title": "小明的图", "mirror": True})
        self.assertEqual(match.remainder, '"另一 段" --mirror=false')
        self.assertEqual(shortcut.names, ["{name}"])

    def test_shortcut_boundary_and_optional_group(self):
        shortcut = SimpleNamespace(pattern=r"问(?<word>好)?", names=[], texts=["{word}"], options={})
        meme = make_meme(shortcuts=[shortcut])
        self.assertEqual(parser.match_meme([meme], "问hello"), [])
        self.assertEqual(parser.match_meme([meme], "问")[0].texts, [""])

    def test_invalid_regex_and_zero_length_shortcut_do_not_break_keyword(self):
        invalid = SimpleNamespace(pattern="[", names=[], texts=[], options={})
        empty = SimpleNamespace(pattern="", names=[], texts=[], options={})
        meme = make_meme(shortcuts=[invalid, empty])
        self.assertEqual(len(parser.match_meme([meme], "示例 文字")), 1)


class PythonOptionTests(unittest.TestCase):
    def setUp(self):
        def option(names, *, args=None, dest=None, constant=None, help_text=None):
            return SimpleNamespace(names=names, args=args, dest=dest, action=SimpleNamespace(value=constant), help_text=help_text)

        self.options = [
            option(["-n", "--number"], args=[SimpleNamespace(name="number", value="int")]),
            option(["--mode"], args=[SimpleNamespace(name="mode", value="str")]),
            option(["--loop", "循环"], dest="mode", constant="loop"),
            option(["--circle", "圆"], dest="circle", constant=True),
        ]
        schema = {"properties": {"number": {"minimum": 1, "maximum": 5}, "mode": {"enum": ["normal", "loop"]}}}
        self.meme = make_meme(options=self.options)
        self.meme.info.params.args_type = SimpleNamespace(args_model=SimpleNamespace(model_json_schema=lambda: schema))

    def test_python_aliases_arguments_constants_and_explicit_boolean(self):
        self.assertEqual(
            parser.parse_options(self.meme, ["文字", "-n", "3", "循环", "圆", "--circle=false"]),
            (["文字"], {"number": 3, "mode": "loop", "circle": False}),
        )
        self.assertEqual(parser.parse_options(self.meme, ["--loop", "--mode=normal"])[1], {"mode": "normal"})
        self.assertEqual(parser.parse_options(self.meme, ["文字"]), (["文字"], {}))

    def test_python_schema_validation_and_bad_flags(self):
        for tokens, expected in [
            (["--number=0"], "不小于"), (["--number=6"], "不大于"),
            (["--number=3.5"], "整数"), (["--mode=invalid"], "可选"),
            (["--number"], "缺少"), (["--number", "--circle"], "缺少"),
            (["--loop=normal"], "无需"), (["--circle=invalid"], "true 或 false"),
            (["--missing"], "未知"),
        ]:
            with self.subTest(tokens=tokens), self.assertRaisesRegex(ValueError, expected):
                parser.parse_options(self.meme, tokens)

    def test_python_quotes_end_marker_and_negative_text(self):
        self.assertEqual(
            parser.parse_options(self.meme, parser.tokenize('"两 段" -1 - -- --circle')),
            (["两 段", "-1", "-", "--circle"], {}),
        )

    def test_option_help_accepts_worker_catalog(self):
        descriptor = SimpleNamespace(names=["-n", "--number"], args=[{"name": "number", "value": "int"}], help_text="图片编号")
        self.assertEqual(parser.option_help(descriptor), "-n / --number <number>：图片编号")
        self.assertEqual(parser.option_help(self.options[2]), "--loop / 循环")

    def test_remote_parse_callback_needs_no_local_engine_metadata(self):
        calls = []
        remote = SimpleNamespace(parse_options=lambda tokens: (calls.append(tokens) or (["正文"], {"circle": True})))
        self.assertEqual(parser.parse_options(remote, ["圆", "正文"]), (["正文"], {"circle": True}))
        self.assertEqual(calls, [["圆", "正文"]])

    def test_python_shortcuts_keep_seed_arguments_and_allow_overrides(self):
        self.meme.info.shortcuts = [SimpleNamespace(key=r"给(?P<name>[^\s]+)循环", args=["--loop", "{name}"], humanized="给某人循环")]
        match = parser.match_meme([self.meme], '给小明循环 --mode normal "新 文本"')[0]
        self.assertEqual(match.arguments, ["--loop", "小明"])
        self.assertEqual(match.remainder, '--mode normal "新 文本"')
        self.assertEqual(
            parser.parse_options(self.meme, match.arguments + parser.tokenize(match.remainder)),
            (["小明", "新 文本"], {"mode": "normal"}),
        )

    def test_python_shortcut_same_keyword_keeps_seed_arguments(self):
        self.meme.info.shortcuts = [SimpleNamespace(key="示例", args=["--loop"], humanized=None)]
        matches = parser.match_meme([self.meme], "示例 内容")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].arguments, ["--loop"])


try:
    from meme_generator import ParserOption as PythonParserOption, get_memes
except ImportError:
    PythonParserOption = None


@unittest.skipIf(PythonParserOption is None, "Run in the isolated Python engine environment")
class RealPythonEngineParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.memes = {}
        for native in get_memes():
            params = native.params_type
            cls.memes[native.key] = SimpleNamespace(
                key=native.key,
                info=SimpleNamespace(
                    keywords=native.keywords,
                    shortcuts=native.shortcuts,
                    params=SimpleNamespace(
                        options=params.args_type.parser_options if params.args_type else [],
                        args_type=params.args_type,
                    ),
                ),
            )

    def test_installed_python_engine_options(self):
        self.assertEqual(parser.parse_options(self.memes["petpet"], ["圆"]), ([], {"circle": True}))
        self.assertEqual(parser.parse_options(self.memes["petpet"], ["--circle=false"]), ([], {"circle": False}))
        self.assertEqual(parser.parse_options(self.memes["always"], ["套娃", "--mode", "loop"]), ([], {"mode": "loop"}))
        self.assertEqual(parser.parse_options(self.memes["crawl"], ["-n", "2"]), ([], {"number": 2}))
        with self.assertRaisesRegex(ValueError, "可选"):
            parser.parse_options(self.memes["always"], ["--mode", "missing"])

    def test_installed_python_regex_shortcuts(self):
        matches = parser.match_meme([self.memes["high_EQ"]], "低情商 不行 高情商 很好")
        self.assertEqual(matches[0].arguments, ["不行", "很好"])
        match = parser.match_meme([self.memes["always"]], "一直一直 --mode normal")[0]
        self.assertEqual(parser.parse_options(match.meme, match.arguments + parser.tokenize(match.remainder)), ([], {"mode": "normal"}))

    def test_every_installed_option_and_shortcut_metadata_can_be_read(self):
        for meme in self.memes.values():
            with self.subTest(meme=meme.key):
                self.assertEqual(parser.parse_options(meme, []), ([], {}))
                for option in meme.info.params.options:
                    self.assertTrue(parser.option_help(option))
                for shortcut in meme.info.shortcuts:
                    self.assertIsNotNone(re.compile(shortcut.key))


if __name__ == "__main__":
    unittest.main()
