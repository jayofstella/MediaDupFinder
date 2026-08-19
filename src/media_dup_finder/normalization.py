"""Deterministic and explainable filename normalization rules."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .cjk import simplify_for_matching


_FC2_RE = re.compile(r"(?i)(?<![a-z0-9])fc2[\s._-]*ppv[\s._-]*(\d{3,9})(?!\d)")
_STREAMING_RE = re.compile(
    r"(?i)(?<![a-z0-9])(1pondo|10musume|caribbeancom|pacopacomama)"
    r"[\s._-]*(\d{6})[\s._-]+(\d{1,4})(?!\d)"
)
_TOKYO_HOT_RE = re.compile(r"(?i)(?<![a-z0-9])tokyo[\s._-]*hot[\s._-]*n[\s._-]*(\d{3,6})(?!\d)")
_CATALOG_SEPARATED_RE = re.compile(
    r"(?i)(?<![a-z0-9])([a-z][a-z0-9]{1,11})[\s._-]+([0-9]{1,7})(?![0-9])"
)
_CATALOG_COMPACT_RE = re.compile(
    r"(?i)(?<![a-z0-9])([a-z]{2,12})([0-9]{2,7})(?![0-9])"
)
_NON_CATALOG_PREFIXES = frozenset({
    "cd", "disc", "disk", "part", "pt", "season", "episode", "vol", "volume",
    "web", "hdr", "avc", "hevc", "xvid", "divx", "mp",
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
})

_PART_RE = re.compile(
    r"(?i)(?:^|[\s._\-\[\]()（）【】])(?:cd|disc|disk|part|pt)[\s._-]*0*([1-9][0-9]?)"
    r"(?:$|[\s._\-\[\]()（）【】])"
)
_CHINESE_PART_RE = re.compile(
    r"(?:^|[\s._\-\[\]()（）【】])"
    r"(上集|下集|上部|下部|上篇|下篇|上|下|第\s*[一二三四五六七八九十0-9]+\s*[部集段篇])"
    r"(?:$|[\s._\-\[\]()（）【】])"
)

_DOMAIN_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+"
    r"(?:com|net|org|tv|cc|cn|me|io|xyz|site|info|club|top|co|uk|jp)"
    r"(?::\d+)?(?:/[^\s\[\]【】()]*)?"
)
_AT_GROUP_RE = re.compile(r"(?i)(?:^|[\s._\-\[\]【】])@[a-z0-9\u3400-\u9fff-]{2,30}")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_ROMAN_TOKEN_RE = re.compile(
    r"(?i)(?<![a-z0-9])(x|ix|viii|vii|vi|v|iv|iii|ii|i)(?![a-z0-9])"
)
_ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
                 "vii": 7, "viii": 8, "ix": 9, "x": 10}

_BRACKETS = str.maketrans({
    "《": " ", "》": " ", "【": " ", "】": " ", "[": " ", "]": " ",
    "（": " ", "）": " ", "(": " ", ")": " ", "「": " ", "」": " ",
    "『": " ", "』": " ", "〈": " ", "〉": " ", "“": " ", "”": " ",
    "‘": " ", "’": " ", "\"": " ", "'": " ",
})

_PROMOTIONAL_PREFIXES = (
    "电影天堂", "影视天堂", "电影下载", "高清下载", "迅雷下载", "资源发布",
    "经典剧情", "经典影片", "经典电影", "最新电影", "热门电影", "高清电影",
    "高清影视", "精品影视", "影视分享", "资源分享", "珍藏版", "收藏版",
    "完整版", "未删减版", "未删减", "正片", "电影版",
)
_COMPOUND_NOISE_RE = re.compile(
    r"国语中字|粤语中字|英语中字|日语中字|韩语中字|国粤双语|国英双语|"
    r"中英双语|双语字幕|中英双字|简繁中字|简繁字幕|内嵌中字|中文字幕|"
    r"中文硬字幕|中英文字幕|官方中字|外挂中字|中字国语|中字粤语"
)
_NOISE_PHRASE_RE = re.compile(
    r"(?i)web[\s._-]*dl|blu[\s._-]*ray|dolby[\s._-]*vision|director[\s._-]*s?[\s._-]*cut"
)

_NOISE_TOKENS = frozenset({
    "4k", "8k", "uhd", "fhd", "hd", "sd", "hdr", "hdr10", "dolbyvision",
    "2160p", "1440p", "1080p", "1080i", "720p", "576p", "480p", "360p",
    "bluray", "bdrip", "brrip", "dvdrip", "hdtv", "webrip", "webdl", "remux",
    "cam", "ts", "tc", "xvid", "divx", "x264", "x265", "h264", "h265",
    "hevc", "av1", "aac", "ac3", "dts", "flac", "atmos", "10bit", "8bit",
    "proper", "repack", "internal", "limited", "extended", "uncut", "directorscut",
    "中字", "中英", "字幕", "双语", "国语", "国配", "粤语", "英语", "日语", "韩语",
    "简体", "繁体", "简中", "繁中", "chs", "cht", "eng", "sub", "subs",
    "subbed", "dub", "dubbed", "c", "uc", "uncensored", "censored", "leak", "sample",
})
_RELEASE_GROUP_TOKENS = frozenset({
    "rarbg", "yts", "yify", "ettv", "eztv", "ion10", "ctrlhd", "mteam", "cmct",
    "frds", "beasts", "hdchina", "ourbits", "ptp", "ntb", "flux", "tigole",
    "qxr", "joy", "playweb", "cakes", "sparks", "amiable", "geckos", "fgt",
})
_NOISE_PATTERNS = (
    re.compile(r"(?i)^[0-9]{3,4}[pi]$"),
    re.compile(r"(?i)^(?:x|h)[._-]?(?:264|265)$"),
    re.compile(r"(?i)^[0-9]+(?:bit|fps)$"),
)


@dataclass(frozen=True)
class NormalizedName:
    raw: str
    cleaned_display: str
    catalog_key: Optional[str]
    primary: str
    aliases: Tuple[str, ...]
    part_marker: Optional[str] = None
    years: Tuple[int, ...] = ()
    tokens: Tuple[str, ...] = ()


def _normalize_serial(digits: str) -> str:
    return str(int(digits)) if digits and int(digits) else "0"


def _valid_catalog(
    prefix: str,
    digits: str,
    following: str,
    separator: str = "",
    compact: bool = False,
) -> bool:
    folded = prefix.casefold()
    if folded in _NON_CATALOG_PREFIXES:
        return False
    if digits in {"2160", "1440", "1080", "720", "576", "480", "360"}:
        if following in {"p", "i"} or folded in {"video", "movie", "film", "uhd"}:
            return False
    # A movie-year-range value is usually a release year when separated by
    # spaces/dots/underscores, or compacted after a title such as IT2017.
    # Explicit hyphen forms remain available for catalog numbers.
    if len(digits) == 4 and 1900 <= int(digits) <= 2099:
        if compact or "-" not in separator or len(folded) > 3:
            return False
    return True


def _catalog_key(text: str) -> Optional[str]:
    fc2 = _FC2_RE.search(text)
    if fc2:
        return "fc2-ppv-{}".format(_normalize_serial(fc2.group(1)))
    streaming = _STREAMING_RE.search(text)
    if streaming:
        return "{}-{}-{}".format(
            streaming.group(1).casefold(), streaming.group(2), _normalize_serial(streaming.group(3))
        )
    tokyo_hot = _TOKYO_HOT_RE.search(text)
    if tokyo_hot:
        return "tokyo-hot-n{}".format(_normalize_serial(tokyo_hot.group(1)))

    for pattern in (_CATALOG_SEPARATED_RE, _CATALOG_COMPACT_RE):
        compact = pattern is _CATALOG_COMPACT_RE
        for match in pattern.finditer(text):
            prefix = match.group(1).casefold()
            digits = match.group(2)
            following = text[match.end():match.end() + 1].casefold()
            separator = "" if compact else text[match.end(1):match.start(2)]
            if _valid_catalog(prefix, digits, following, separator, compact):
                return "{}-{}".format(prefix, _normalize_serial(digits))
    return None


def _part_marker(text: str) -> Optional[str]:
    match = _PART_RE.search(text)
    if match:
        return "part{}".format(int(match.group(1)))
    chinese = _CHINESE_PART_RE.search(text)
    if chinese:
        return "segment:{}".format(re.sub(r"\s+", "", chinese.group(1)))
    return None


def _convert_roman_suffix(text: str) -> str:
    """Convert a sequel numeral, but preserve ordinary titles such as I Robot."""

    candidates = list(_ROMAN_TOKEN_RE.finditer(text))
    for match in reversed(candidates):
        tail_tokens = [
            token for token in re.split(r"[^0-9a-z\u3400-\u9fff]+", text[match.end():])
            if token
        ]
        head_match = re.search(r"([a-z]+)[^a-z]*$", text[:match.start()])
        after_ordinal_word = bool(
            head_match
            and head_match.group(1).casefold() in {"episode", "part", "chapter", "vol", "volume"}
        )
        suffix_only = all(
            bool(_YEAR_RE.fullmatch(token)) or _is_noise_token(token)
            for token in tail_tokens
        )
        if not after_ordinal_word and not suffix_only:
            continue
        value = _ROMAN_VALUES.get(match.group(1).casefold())
        if value is not None:
            return text[:match.start(1)] + str(value) + text[match.end(1):]
    return text


def _strip_promotional_affixes(text: str) -> str:
    value = text.strip()
    changed = True
    while changed and value:
        changed = False
        for word in _PROMOTIONAL_PREFIXES:
            if value.startswith(word):
                value = value[len(word):].lstrip("-_. ·")
                changed = True
            if value.endswith(word):
                value = value[:-len(word)].rstrip("-_. ·")
                changed = True
    return value


def _is_noise_token(token: str) -> bool:
    folded = token.casefold().strip()
    if not folded or folded in _NOISE_TOKENS or folded in _RELEASE_GROUP_TOKENS:
        return True
    return any(pattern.match(folded) for pattern in _NOISE_PATTERNS)


def _extract_release_years(text: str, catalog: Optional[str]) -> Tuple[int, ...]:
    """Return the most likely release year, not every year-like title number."""

    if catalog:
        return ()
    matches = list(_YEAR_RE.finditer(text))
    if not matches:
        return ()
    # The last year-like token is normally the release year in names such as
    # "2001 A Space Odyssey 1968" and "Blade Runner 2049 2017".
    match = matches[-1]
    if len(matches) == 1 and not text[:match.start()].strip():
        # An unbracketed leading number such as "2001 A Space Odyssey" or
        # "1917" is more likely part of the title than release metadata.
        return ()
    remainder = text[:match.start()] + " " + text[match.end():]
    remainder = _strip_promotional_affixes(remainder.translate(_BRACKETS))
    meaningful = [
        token for token in re.split(r"[^0-9a-z\u3400-\u9fff]+", remainder)
        if token and not _is_noise_token(token)
    ]
    # A filename that is only "1917" is a title, not proof of a release year.
    if not meaningful:
        return ()
    return (int(match.group(1)),)


def _dedupe(values: Iterable[str]) -> Tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def normalize_stem(stem: str) -> NormalizedName:
    """Normalize one filename stem without reading or changing the file."""

    raw = stem
    value = unicodedata.normalize("NFKC", stem).strip().casefold()
    value = simplify_for_matching(value)
    value = _DOMAIN_RE.sub(" ", value)
    value = _AT_GROUP_RE.sub(" ", value)
    value = _COMPOUND_NOISE_RE.sub(" ", value)
    value = _NOISE_PHRASE_RE.sub(" ", value)
    value = _convert_roman_suffix(value)
    catalog = _catalog_key(value)
    part = _part_marker(value)
    years = _extract_release_years(value, catalog)
    value = value.translate(_BRACKETS)
    value = _strip_promotional_affixes(value)
    raw_tokens = [token for token in re.split(r"[^\w\u3400-\u9fff]+", value) if token]

    filtered = []
    for token in raw_tokens:
        if _is_noise_token(token):
            continue
        if _PART_RE.search(" {} ".format(token)):
            continue
        filtered.append(token)

    display = " ".join(filtered).strip()
    primary = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", display.casefold())
    normalized_tokens = tuple(
        token for token in (
            re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", item.casefold()) for item in filtered
        ) if token
    )

    if catalog:
        primary = catalog.replace("-", "")
        display = catalog.upper()
        years = ()

    aliases = [primary]
    if not catalog:
        without_year = primary
        for year in years:
            marker = str(year)
            position = without_year.rfind(marker)
            if position >= 0:
                without_year = without_year[:position] + without_year[position + len(marker):]
        if len(without_year) >= 2 and without_year != primary:
            aliases.append(without_year)
        trailing_candidates = aliases[1:] if years else list(aliases)
        for candidate in trailing_candidates:
            trailing_one = re.match(r"^(.{2,})0?1$", candidate)
            if trailing_one:
                aliases.append(trailing_one.group(1))

    return NormalizedName(
        raw=raw,
        cleaned_display=display or raw,
        catalog_key=catalog,
        primary=primary,
        aliases=_dedupe(aliases),
        part_marker=part,
        years=years,
        tokens=normalized_tokens,
    )


def normalize_filename(filename: str) -> NormalizedName:
    return normalize_stem(Path(filename).stem)
