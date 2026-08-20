"""Deterministic and explainable filename normalization rules."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
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
    "hd", "sd", "fhd", "uhd", "vd", "video", "movie", "film",
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
    r"(?:com|net|org|tv|cc|cn|me|io|xyz|site|info|club|top|co|uk|jp|la)"
    r"(?::\d+)?(?:/[^\s\[\]【】()]*)?"
)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_FULL_DATE_RE = re.compile(
    r"(?i)(?<!\d)(?P<year>(?:19|20)\d{2}|\d{2})[\s._-]+"
    r"(?P<month>0?[1-9]|1[0-2])[\s._-]+"
    r"(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"
)
_COMPACT_DATE_RE = re.compile(
    r"(?i)(?:^|[\s._-])(?P<date>(?:19|20)\d{6})(?=$|[\s._-])"
)
_SEASON_EPISODE_RE = re.compile(
    r"(?i)(?:^|[\s._-])s(?:eason)?[\s._-]*0*(\d{1,2})"
    r"[\s._-]*e(?:pisode)?[\s._-]*0*(\d{1,3})(?=$|[\s._-])"
)
_X_EPISODE_RE = re.compile(
    r"(?i)(?:^|[\s._-])0*(\d{1,2})x0*(\d{1,3})(?=$|[\s._-])"
)
_EPISODE_ONLY_RE = re.compile(
    r"(?i)(?:^|[\s._-])(?:e|ep|episode)[\s._-]*0*(\d{1,4})(?=$|[\s._-])"
)
_CHINESE_EPISODE_RE = re.compile(
    r"(?:^|[\s._-])第\s*0*(\d{1,4})\s*集(?=$|[\s._-])"
)
_PAREN_EPISODE_RE = re.compile(
    r"(?i)(?:^|[\s._-])\(\s*0*(\d{1,5})\s*\)(?=$|[\s._-])"
)
_LONG_SERIES_NUMBER_RE = re.compile(
    r"(?i)(?<![a-z0-9])([a-z]{8,})[\s._-]*0*(\d{2,6})(?![a-z0-9])"
)
_SITE_BRAND_RE = re.compile(
    r"(?i)(?:^|[\s._-])(?:big|fun)?\d{3,5}(?:社区|论坛|論壇)(?=$|[\s._-])"
)
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
    "4k", "8k", "uhd", "fhd", "hd", "sd", "vd", "hdr", "hdr10", "dolbyvision",
    "2160p", "1440p", "1080p", "1080i", "720p", "576p", "480p", "360p",
    "bluray", "bdrip", "brrip", "dvdrip", "hdtv", "webrip", "webdl", "remux",
    "cam", "ts", "tc", "xvid", "divx", "x264", "x265", "h264", "h265",
    "hevc", "av1", "aac", "ac3", "dts", "flac", "atmos", "10bit", "8bit",
    "proper", "repack", "internal", "limited", "extended", "uncut", "directorscut",
    "mp4", "mkv", "mov", "avi", "wmv", "flv", "rm", "rmvb", "m4v",
    "webm", "mpg", "mpeg", "m2ts", "vob", "3gp",
    "中字", "中英", "字幕", "双语", "国语", "国配", "粤语", "英语", "日语", "韩语",
    "简体", "繁体", "简中", "繁中", "chs", "cht", "eng", "sub", "subs",
    "subbed", "dub", "dubbed", "c", "uc", "uncensored", "censored", "sample",
})
_RELEASE_GROUP_TOKENS = frozenset({
    "rarbg", "yts", "yify", "ettv", "eztv", "ion10", "ctrlhd", "mteam", "cmct",
    "frds", "beasts", "hdchina", "ourbits", "ptp", "ntb", "flux", "tigole",
    "qxr", "joy", "playweb", "cakes", "sparks", "amiable", "geckos", "fgt",
})
_NOISE_PATTERNS = (
    re.compile(r"(?i)^[0-9]{3,4}[pi]$"),
    re.compile(r"(?i)^(?:2160|1440|1080|720|576|480|360)$"),
    re.compile(r"(?i)^(?:vd|hd|fhd|uhd)(?:2160|1440|1080|720|576|480|360)[pi]?$"),
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
    identity_kind: str = "title"
    work_key: str = ""
    series_key: Optional[str] = None
    episode_date: Optional[str] = None
    episode_id: Optional[str] = None
    identity_source: str = "文件名"
    source_text: str = ""


_GENERIC_MEDIA_FOLDER_NAMES = frozenset({
    "video_ts", "audio_ts", "bdmv", "stream", "clipinf", "playlist",
    "certificate", "avchd", "private",
})
_GENERIC_LIBRARY_FOLDER_NAMES = frozenset({
    "movie", "movies", "video", "videos", "media", "download", "downloads",
    "complete", "completed", "new folder", "新建文件夹", "电影", "影片",
    "影视", "视频", "下载", "成人", "av", "jav",
})
_GENERIC_SEGMENT_FOLDER_RE = re.compile(
    r"(?i)^(?:cd|disc|disk|dvd|part|pt)[\s._-]*0*([1-9][0-9]?)$"
)
_GENERIC_DVD_STEM_RE = re.compile(
    r"(?i)^vts[\s._-]*0*(\d{1,2})[\s._-]+0*(\d{1,2})$"
)
_GENERIC_DISC_STEM_RE = re.compile(
    r"(?i)^(?:video[\s._-]*ts|audio[\s._-]*ts|avseq|mpegav|title|track)"
    r"[\s._-]*0*(\d{0,4})$"
)
_GENERIC_NUMERIC_STEM_RE = re.compile(r"^0*(\d{1,6})$")


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
    # Short dot/space-separated numbers are very often a date fragment or a
    # series episode number. Keep short catalog numbers only when an explicit
    # hyphen is present, for example ABC-1.
    if len(digits) < 3 and not compact and "-" not in separator:
        return False
    if digits in {"2160", "1440", "1080", "720", "576", "480", "360"}:
        if following in {"p", "i"} or folded in {
            "video", "movie", "film", "uhd", "fhd", "hd", "sd", "vd",
        }:
            return False
    # A movie-year-range value is usually a release year when separated by
    # spaces/dots/underscores, or compacted after a title such as IT2017.
    # Explicit hyphen forms remain available for catalog numbers.
    if len(digits) == 4 and 1900 <= int(digits) <= 2099:
        if compact or "-" not in separator or len(folded) > 3:
            return False
    return True


def _catalog_identity(text: str) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    fc2 = _FC2_RE.search(text)
    if fc2:
        return "fc2-ppv-{}".format(_normalize_serial(fc2.group(1))), fc2.span()
    streaming = _STREAMING_RE.search(text)
    if streaming:
        return (
            "{}-{}-{}".format(
                streaming.group(1).casefold(),
                streaming.group(2),
                _normalize_serial(streaming.group(3)),
            ),
            streaming.span(),
        )
    tokyo_hot = _TOKYO_HOT_RE.search(text)
    if tokyo_hot:
        return "tokyo-hot-n{}".format(_normalize_serial(tokyo_hot.group(1))), tokyo_hot.span()

    for pattern in (_CATALOG_SEPARATED_RE, _CATALOG_COMPACT_RE):
        compact = pattern is _CATALOG_COMPACT_RE
        for match in pattern.finditer(text):
            prefix = match.group(1).casefold()
            digits = match.group(2)
            following = text[match.end():match.end() + 1].casefold()
            separator = "" if compact else text[match.end(1):match.start(2)]
            if _valid_catalog(prefix, digits, following, separator, compact):
                return "{}-{}".format(prefix, _normalize_serial(digits)), match.span()
    return None, None


def _catalog_key(text: str) -> Optional[str]:
    return _catalog_identity(text)[0]


def _part_marker(
    text: str,
    catalog_span: Optional[Tuple[int, int]] = None,
) -> Optional[str]:
    match = _PART_RE.search(text)
    if match:
        return "part{}".format(int(match.group(1)))
    chinese = _CHINESE_PART_RE.search(text)
    if chinese:
        return "segment:{}".format(re.sub(r"\s+", "", chinese.group(1)))
    if catalog_span:
        tail = text[catalog_span[1]:]
        meaningful = [
            token for token in re.split(r"[^0-9a-z\u3400-\u9fff]+", tail)
            if token and not _is_noise_token(token)
        ]
        if len(meaningful) == 1 and re.fullmatch(r"0*[1-9][0-9]?", meaningful[0]):
            return "part{}".format(int(meaningful[0]))
        if len(meaningful) == 1 and meaningful[0].casefold() in {"a", "b"}:
            return "segment:{}".format(meaningful[0].casefold().upper())
        # Catalog releases sometimes put a long descriptive title before the
        # final _1/_2 segment. Preserve that explicit trailing segment even
        # though the middle title contains many meaningful words.
        trailing = re.search(r"(?:_|-)0*([1-9]|1[0-9]|20)\s*$", tail)
        if trailing:
            return "part{}".format(int(trailing.group(1)))
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


@dataclass(frozen=True)
class _EpisodeIdentity:
    kind: str
    series_key: str
    episode_date: Optional[str]
    episode_id: Optional[str]
    title_tokens: Tuple[str, ...]
    primary: str
    display: str


def _meaningful_tokens(fragment: str) -> Tuple[str, ...]:
    value = _SITE_BRAND_RE.sub(" ", fragment)
    value = _strip_promotional_affixes(value.translate(_BRACKETS))
    return tuple(
        token.casefold()
        for token in re.split(r"[^0-9a-z\u3400-\u9fff]+", value)
        if token and not _is_noise_token(token)
    )


def _normalize_two_or_four_digit_year(value: str) -> int:
    year = int(value)
    if len(value) == 2:
        return 2000 + year if year <= 79 else 1900 + year
    return year


def _episode_identity(text: str) -> Optional[_EpisodeIdentity]:
    """Recognize a concrete series episode before generic title matching."""

    for match in _FULL_DATE_RE.finditer(text):
        year = _normalize_two_or_four_digit_year(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        try:
            episode_date = date(year, month, day).isoformat()
        except ValueError:
            continue
        series_tokens = _meaningful_tokens(text[:match.start()])
        if not series_tokens:
            continue
        title_tokens = _meaningful_tokens(text[match.end():])
        series_key = "".join(series_tokens)
        title_key = "".join(title_tokens)
        primary = "date{}{}{}".format(
            series_key,
            episode_date.replace("-", ""),
            title_key,
        )
        display = "{} · {}".format(" ".join(series_tokens), episode_date)
        if title_tokens:
            display += " · " + " ".join(title_tokens)
        return _EpisodeIdentity(
            kind="dated_episode",
            series_key=series_key,
            episode_date=episode_date,
            episode_id=None,
            title_tokens=title_tokens,
            primary=primary,
            display=display,
        )

    for match in _COMPACT_DATE_RE.finditer(text):
        raw_date = match.group("date")
        year = int(raw_date[:4])
        month = int(raw_date[4:6])
        day = int(raw_date[6:8])
        try:
            episode_date = date(year, month, day).isoformat()
        except ValueError:
            continue
        series_tokens = _meaningful_tokens(text[:match.start()])
        if not series_tokens:
            continue
        title_tokens = _meaningful_tokens(text[match.end():])
        series_key = "".join(series_tokens)
        title_key = "".join(title_tokens)
        display = "{} · {}".format(" ".join(series_tokens), episode_date)
        if title_tokens:
            display += " · " + " ".join(title_tokens)
        return _EpisodeIdentity(
            kind="dated_episode",
            series_key=series_key,
            episode_date=episode_date,
            episode_id=None,
            title_tokens=title_tokens,
            primary="date{}{}{}".format(
                series_key, episode_date.replace("-", ""), title_key,
            ),
            display=display,
        )

    for pattern in (_SEASON_EPISODE_RE, _X_EPISODE_RE):
        match = pattern.search(text)
        if not match:
            continue
        series_tokens = _meaningful_tokens(text[:match.start()])
        if not series_tokens:
            continue
        season = int(match.group(1))
        episode = int(match.group(2))
        episode_id = "s{:02d}e{:02d}".format(season, episode)
        series_key = "".join(series_tokens)
        title_tokens = _meaningful_tokens(text[match.end():])
        return _EpisodeIdentity(
            kind="series_episode",
            series_key=series_key,
            episode_date=None,
            episode_id=episode_id,
            title_tokens=title_tokens,
            primary="episode{}{}".format(series_key, episode_id),
            display="{} · {}".format(" ".join(series_tokens), episode_id.upper()),
        )

    for pattern in (_EPISODE_ONLY_RE, _CHINESE_EPISODE_RE, _PAREN_EPISODE_RE):
        match = pattern.search(text)
        if not match:
            continue
        series_tokens = _meaningful_tokens(text[:match.start()])
        if not series_tokens:
            continue
        episode_id = "e{:03d}".format(int(match.group(1)))
        series_key = "".join(series_tokens)
        title_tokens = _meaningful_tokens(text[match.end():])
        return _EpisodeIdentity(
            kind="series_episode",
            series_key=series_key,
            episode_date=None,
            episode_id=episode_id,
            title_tokens=title_tokens,
            primary="episode{}{}".format(series_key, episode_id),
            display="{} · {}".format(" ".join(series_tokens), episode_id.upper()),
        )

    # Long compact series names followed by a running number are common in
    # downloaded libraries (for example fellatiojapan567). The number is the
    # concrete episode/work identity, not a disposable filename suffix.
    compact = _LONG_SERIES_NUMBER_RE.search(text)
    if compact and not (
        len(compact.group(2)) == 4
        and 1900 <= int(compact.group(2)) <= 2099
    ):
        series_key = compact.group(1).casefold()
        episode_id = "e{:06d}".format(int(compact.group(2)))
        title_tokens = _meaningful_tokens(text[compact.end():])
        return _EpisodeIdentity(
            kind="series_episode",
            series_key=series_key,
            episode_date=None,
            episode_id=episode_id,
            title_tokens=title_tokens,
            primary="episode{}{}".format(series_key, episode_id),
            display="{} · {}".format(series_key, episode_id.upper()),
        )
    return None


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
    # '@' is only a separator. Removing the whole following token discarded
    # real identifiers such as @fc1298546 and collapsed unrelated works into
    # one website-brand group.
    value = value.replace("@", " ")
    value = _SITE_BRAND_RE.sub(" ", value)
    value = _COMPOUND_NOISE_RE.sub(" ", value)
    value = _NOISE_PHRASE_RE.sub(" ", value)
    value = _convert_roman_suffix(value)
    episode = _episode_identity(value)
    catalog, catalog_span = (None, None) if episode else _catalog_identity(value)
    part = _part_marker(value, catalog_span)
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

    identity_kind = "catalog" if catalog else "title"
    series_key = None
    episode_date = None
    episode_id = None
    if episode:
        identity_kind = episode.kind
        series_key = episode.series_key
        episode_date = episode.episode_date
        episode_id = episode.episode_id
        primary = episode.primary
        display = episode.display
        years = ()
        normalized_tokens = (
            episode.series_key,
            (episode.episode_date or episode.episode_id or "").replace("-", ""),
        ) + episode.title_tokens

    aliases = [primary]
    if not catalog and not episode:
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
        identity_kind=identity_kind,
        work_key=primary,
        series_key=series_key,
        episode_date=episode_date,
        episode_id=episode_id,
        identity_source="文件名",
        source_text=raw,
    )


def normalize_filename(filename: str) -> NormalizedName:
    return normalize_stem(Path(filename).stem)


def _generic_media_segment(path: Path) -> Optional[str]:
    """Return a stable segment marker for a generic disc-container name."""

    stem = unicodedata.normalize("NFKC", path.stem).strip().casefold()
    parent_segment = ""
    for parent_name in reversed(path.parent.parts[-4:]):
        parent_match = _GENERIC_SEGMENT_FOLDER_RE.fullmatch(parent_name.strip())
        if parent_match:
            parent_segment = "disc{}-".format(int(parent_match.group(1)))
            break
    dvd = _GENERIC_DVD_STEM_RE.fullmatch(stem)
    if dvd:
        return "disc:{}vts{:02d}-{:02d}".format(
            parent_segment, int(dvd.group(1)), int(dvd.group(2)),
        )
    generic = _GENERIC_DISC_STEM_RE.fullmatch(stem)
    if generic:
        number = generic.group(1)
        prefix = stem[:-len(number)] if number else stem
        prefix = re.sub(r"[\s._-]+", "", prefix)
        return "disc:{}{}".format(
            parent_segment,
            prefix if not number else "{}-{}".format(prefix, int(number)),
        )
    parent_names = {
        unicodedata.normalize("NFKC", item).strip().casefold()
        for item in path.parent.parts[-3:]
    }
    numeric = _GENERIC_NUMERIC_STEM_RE.fullmatch(stem)
    if numeric and parent_names & _GENERIC_MEDIA_FOLDER_NAMES:
        return "disc:{}stream-{:06d}".format(parent_segment, int(numeric.group(1)))
    return None


def _meaningful_parent(path: Path, root: Optional[Path]) -> Optional[Path]:
    """Find the nearest parent likely to contain a disc image's work title."""

    root_identity = None
    if root is not None:
        try:
            root_identity = root.resolve()
        except OSError:
            root_identity = root.absolute()
    current = path.parent
    for _ in range(6):
        if not current.name:
            return None
        folded = unicodedata.normalize("NFKC", current.name).strip().casefold()
        if (
            folded not in _GENERIC_MEDIA_FOLDER_NAMES
            and folded not in _GENERIC_LIBRARY_FOLDER_NAMES
            and not _GENERIC_SEGMENT_FOLDER_RE.fullmatch(folded)
        ):
            info = normalize_stem(current.name)
            if info.primary and not (
                info.identity_kind == "title"
                and re.fullmatch(r"[a-z]{1,3}|\d{1,3}", info.primary)
            ):
                return current
        if root_identity is not None:
            try:
                reached_root = current.resolve() == root_identity
            except OSError:
                reached_root = current.absolute() == root_identity
            if reached_root:
                return None
        if current.parent == current:
            return None
        current = current.parent
    return None


def normalize_path(path: Path, root: Optional[Path] = None) -> NormalizedName:
    """Normalize one path and use folder context only for generic disc names.

    DVD/BDMV files such as VTS_01_1.VOB or 00001.M2TS carry a segment label,
    not a work title. In that narrow case the nearest meaningful parent folder
    supplies the work identity and the filename supplies the segment marker.
    """

    source_path = Path(path)
    segment = _generic_media_segment(source_path)
    if not segment:
        return normalize_stem(source_path.stem)
    parent = _meaningful_parent(source_path, root)
    if parent is None:
        raw = source_path.stem
        return NormalizedName(
            raw=raw,
            cleaned_display="未识别光盘作品（{}）".format(raw),
            catalog_key=None,
            primary="",
            aliases=(),
            part_marker=segment,
            tokens=(),
            identity_kind="generic_media",
            work_key="",
            identity_source="上级目录不足",
            source_text=str(source_path.parent),
        )
    parent_info = normalize_stem(parent.name)
    return replace(
        parent_info,
        raw=source_path.stem,
        part_marker=segment,
        identity_source="上级影片目录",
        source_text=parent.name,
    )
