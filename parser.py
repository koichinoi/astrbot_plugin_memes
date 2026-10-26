"""Framework independent command parsing for the Python meme-generator engine."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
import shlex
from types import SimpleNamespace
from typing import Any, Iterable


@dataclass
class CommandMatch:
    meme: Any
    remainder: str
    texts: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    arguments: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    """Split quoted text without interpreting backslashes or ``#`` comments.

    Both kinds of quotes follow POSIX shlex rules. Backslashes remain literal,
    so Windows paths and text such as ``\\n`` reach the generator unchanged.
    """
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    try:
        return list(lexer)
    except ValueError as exc:
        raise ValueError("文字引号未闭合，请检查单引号或双引号") from exc


def strip_prefix(text: str, prefixes: Iterable[str]) -> str | None:
    """Remove the longest prefix; an empty list or prefix allows bare names.

    The WebUI may discard blank list entries when saving configuration.
    """
    text = text.lstrip()
    configured = list(prefixes) or [""]
    for prefix in sorted(set(configured), key=len, reverse=True):
        if text.startswith(prefix):
            return text[len(prefix):].lstrip()
    return None


def _kind(option: Any) -> str:
    if getattr(option, "kind", None):
        return option.kind
    # Preserve compatibility with typed option descriptors without importing
    # any rendering dependencies into the bot process.
    for cls in type(option).__mro__:
        if cls.__name__ in {"BooleanOption", "IntegerOption", "FloatOption", "StringOption"}:
            return cls.__name__
    raise ValueError(f"不支持的表情选项类型：{option.name}")


def _flags(option: Any) -> list[str]:
    if hasattr(option, "names"):
        return list(option.names)
    flags = option.parser_flags
    short = list(getattr(flags, "short_aliases", None) or [])
    long = list(getattr(flags, "long_aliases", None) or [])
    if getattr(flags, "short", False) and option.name:
        short.insert(0, option.name[0])
    if getattr(flags, "long", False):
        long.insert(0, option.name)
    return list(dict.fromkeys([f"-{name}" for name in short] + [f"--{name}" for name in long]))


def _convert(option: Any, raw: str) -> Any:
    kind = _kind(option)
    try:
        if kind == "BooleanOption":
            value = raw.casefold()
            if value in {"true", "yes", "on", "1"}:
                return True
            if value in {"false", "no", "off", "0"}:
                return False
            raise ValueError
        if kind == "IntegerOption":
            value = int(raw)
        elif kind == "FloatOption":
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError
        else:
            value = raw
    except (TypeError, ValueError) as exc:
        expected = {"BooleanOption": "true 或 false", "IntegerOption": "整数", "FloatOption": "有限小数"}.get(kind, "文字")
        raise ValueError(f"选项 {option.name} 需要{expected}，收到：{raw}") from exc

    if kind in {"IntegerOption", "FloatOption"}:
        for attr, fallback, compare, label in (("minimum", "min", lambda a, b: a < b, "不小于"), ("maximum", "max", lambda a, b: a > b, "不大于")):
            bound = getattr(option, attr, None)
            if bound is None:
                bound = getattr(option, fallback, None)
            if bound is not None and compare(value, bound):
                raise ValueError(f"选项 {option.name} 需要{label} {bound}")
    choices = getattr(option, "choices", None)
    if choices and value not in choices:
        raise ValueError(f"选项 {option.name} 可选值：{'、'.join(map(str, choices))}")
    return value


_NUMBER = re.compile(r"^-[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?$|^-\.[0-9]+(?:[eE][+-]?[0-9]+)?$")


def option_help(option: Any) -> str:
    """Describe a Python ParserOption, including lightweight worker metadata."""
    names = " / ".join(_flags(option))
    if hasattr(option, "names"):
        arguments = []
        for argument in option.args or []:
            name = argument.get("name", "参数") if isinstance(argument, dict) else argument.name
            arguments.append(f"<{name}>")
        usage = " ".join([names, *arguments])
        return f"{usage}：{option.help_text}" if option.help_text else usage
    return f"{names}：{option.description}（默认 {option.default}）"


def _python_schema(meme: Any) -> dict[str, Any]:
    args_type = getattr(meme.info.params, "args_type", None)
    if args_type is None:
        return {}
    model = args_type.args_model
    schema = model.model_json_schema() if hasattr(model, "model_json_schema") else model.schema()
    return schema.get("properties", {})


def _python_convert(argument: Any, raw: str, schema: dict[str, Any]) -> Any:
    kinds = {"str": "StringOption", "int": "IntegerOption", "float": "FloatOption", "bool": "BooleanOption"}
    if argument.value not in kinds:
        raise ValueError(f"不支持的表情选项类型：{argument.name}")
    field_schema = schema.get(argument.name, {})
    descriptor = SimpleNamespace(
        name=argument.name,
        kind=kinds[argument.value],
        minimum=field_schema.get("minimum") if argument.value in {"int", "float"} else None,
        maximum=field_schema.get("maximum") if argument.value in {"int", "float"} else None,
        choices=field_schema.get("enum"),
    )
    return _convert(descriptor, raw)


def _parse_python_options(meme: Any, tokens: list[str], options: list[Any]) -> tuple[list[str], dict[str, Any]]:
    aliases: dict[str, Any] = {}
    for option in options:
        for name in option.names:
            if name in aliases and aliases[name] is not option:
                raise ValueError(f"表情选项别名冲突：{name}")
            aliases[name] = option
    schema = _python_schema(meme)
    texts: list[str] = []
    values: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == "--":
            texts.extend(tokens[index:])
            break
        flag, equal, inline = token.partition("=")
        option = aliases.get(flag)
        if option is None:
            if token.startswith("-") and token != "-" and not _NUMBER.fullmatch(token):
                raise ValueError(f"未知选项：{flag}；以 - 开头的文字请放在 -- 后面")
            texts.append(token)
            continue
        arguments = option.args or []
        if not arguments:
            # The upstream builder resolves an omitted destination consistently
            # with Alconna. Its imports stay inside the isolated Python engine.
            destination = option.dest or option.option().dest
            constant = option.action.value if option.action is not None else option.default
            if equal:
                if not isinstance(constant, bool):
                    raise ValueError(f"选项 {flag} 无需参数")
                constant = _convert(SimpleNamespace(name=destination, kind="BooleanOption"), inline)
            values[destination] = constant
            continue
        for offset, argument in enumerate(arguments):
            if equal and offset == 0:
                raw = inline
            else:
                if index >= len(tokens) or (tokens[index].startswith("-") and not _NUMBER.fullmatch(tokens[index])):
                    raise ValueError(f"选项 {flag} 缺少参数；可使用 {flag}=值")
                raw = tokens[index]
                index += 1
            values[argument.name] = _python_convert(argument, raw, schema)
    return texts, values


def parse_options(meme: Any, tokens: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Separate explicit Python engine options; ``--`` ends option parsing.

    Defaults remain the engine's responsibility, allowing shortcut values
    to be merged first and explicitly supplied flags to override them afterward.
    """
    remote_parser = getattr(meme, "parse_options", None)
    if callable(remote_parser):
        return remote_parser(tokens)
    options = meme.info.params.options
    if options and hasattr(options[0], "names"):
        return _parse_python_options(meme, tokens, options)
    aliases: dict[str, Any] = {}
    for option in options:
        for flag in _flags(option):
            previous = aliases.get(flag)
            if previous is not None and previous.name != option.name:
                raise ValueError(f"表情选项别名冲突：{flag}")
            aliases[flag] = option

    texts: list[str] = []
    values: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == "--":
            texts.extend(tokens[index:])
            break
        flag, equal, inline = token.partition("=")
        option = aliases.get(flag)
        if option is None:
            if token.startswith("-") and token != "-" and not _NUMBER.fullmatch(token):
                raise ValueError(f"未知选项：{flag}；以 - 开头的文字请放在 -- 后面")
            texts.append(token)
            continue
        kind = _kind(option)
        if kind == "BooleanOption" and not equal:
            values[option.name] = not bool(getattr(option, "default", False))
            continue
        if equal:
            raw = inline
        else:
            if index >= len(tokens) or (tokens[index].startswith("-") and not _NUMBER.fullmatch(tokens[index])):
                raise ValueError(f"选项 {flag} 缺少参数；可使用 {flag}=值")
            raw = tokens[index]
            index += 1
        values[option.name] = _convert(option, raw)
    return texts, values


def _python_pattern(pattern: str) -> str:
    # Normalize named capture spelling for custom template expressions.
    # The lookbehind forms (?<=...) and (?<!...) must remain untouched.
    pattern = re.sub(r"\(\?<([A-Za-z_][A-Za-z_0-9]*)>", r"(?P<\1>", pattern)
    return pattern.replace(r"\z", r"\Z")


def match_meme(memes: Iterable[Any], text: str) -> list[CommandMatch]:
    """Return candidates for the longest command, without parsing its remainder.

    Python shortcuts keep their expanded arguments as individual tokens. The
    caller prepends these tokens to incoming and replied message text before
    calling :func:`parse_options` once.
    """
    text = text.lstrip()
    matches: list[tuple[int, CommandMatch]] = []
    for meme in memes:
        seen: set[str] = set()
        for name in [meme.key, *meme.info.keywords]:
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            end = len(name)
            if text[:end].casefold() == name.casefold() and (end == len(text) or text[end].isspace()):
                matches.append((end, CommandMatch(meme=meme, remainder=text[end:].lstrip())))
        for shortcut in meme.info.shortcuts:
            pattern = getattr(shortcut, "key", None)
            python_shortcut = pattern is not None
            if not python_shortcut:
                pattern = shortcut.pattern
            try:
                found = re.match(_python_pattern(pattern), text)
            except re.error:
                # One unsupported expression must not prevent other memes loading.
                continue
            if found is None or found.end() == 0:
                continue
            end = found.end()
            if end < len(text) and not text[end].isspace():
                continue
            groups = {key: value or "" for key, value in found.groupdict().items()}
            try:
                if python_shortcut:
                    arguments = [value.format(**groups) for value in (shortcut.args or [])]
                    texts, names, options = [], [], {}
                else:
                    arguments = []
                    texts = [value.format(**groups) for value in (shortcut.texts or [])]
                    names = [value.format(**groups) for value in (shortcut.names or [])]
                    options = {name: value.format(**groups) if isinstance(value, str) else value for name, value in (shortcut.options or {}).items()}
            except (KeyError, IndexError, ValueError) as exc:
                raise ValueError(f"表情 {meme.key} 的快捷指令模板无效") from exc
            matches.append((end, CommandMatch(meme=meme, remainder=text[end:].lstrip(), texts=texts, names=names, options=options, arguments=arguments)))
    if not matches:
        return []
    longest = max(end for end, _ in matches)
    unique: dict[str, CommandMatch] = {}
    for end, match in matches:
        if end != longest:
            continue
        previous = unique.get(match.meme.key)
        # A populated shortcut is more specific than an overlapping plain
        # keyword. Preserve its native template values without claiming that
        # one meme is ambiguous with itself.
        if previous is None or (not (previous.texts or previous.names or previous.options or previous.arguments) and (match.texts or match.names or match.options or match.arguments)):
            unique[match.meme.key] = match
    return list(unique.values())
