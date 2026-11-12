"""
PotPlayer (Daum) DPL 播放列表读写
开发维护: https://iptv-search.com

DPL 是 PotPlayer 的原生播放列表格式，本质是纯文本：

    DAUMPLAYLIST
    playname=http://example.com/a.m3u8
    topindex=0
    saveplaypos=0
    1*file*http://example.com/a.m3u8
    1*title*广东体育
    1*played*1
    1*invalid*1
    1*author*☘️广东频道
    1*thumbnail*https://example.com/logo/广东体育.png
    2*file*...

格式要点（来自 PotPlayer 实际导出的文件）：

- 首行为魔术字 ``DAUMPLAYLIST``；文件通常带 UTF-8 BOM，换行为 CRLF。
- 头部是 ``key=value``：``playname`` 为当前播放地址，``topindex`` 为当前
  播放位置，``saveplaypos`` 是否保存进度，另有 ``playtime`` /
  ``extplaylist`` 等可选字段。
- 条目是 ``序号*字段名*值``，序号从 1 开始并逐条递增。
- ``file`` 是条目的必需字段；``title`` 可缺省（缺省时 PotPlayer 显示文件名
  或地址，本模块提供沿用上一条标题的兜底）。
- ``played`` / ``invalid`` 是 PotPlayer 的播放状态标记，``invalid=1`` 表示
  播放器曾判定该条失效；``author`` / ``thumbnail`` / ``duration`` 为扩展元数据。
- 同一频道常常存在多条镜像地址，因此 ``file`` 允许重复出现。

本模块只负责「字节 ↔ 结构」的纯格式转换，不依赖 tkinter / requests，
便于单独测试与复用。
"""

import codecs
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

#: DPL 文件魔术字
DPL_MAGIC = "DAUMPLAYLIST"

#: 条目行，如 ``12*file*http://...``
_ENTRY_RE = re.compile(r"^(\d+)\*([^*]+)\*(.*)$")

#: 头部行，如 ``topindex=0``
_HEADER_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

#: 已知的头部字段（其余 ``key=value`` 视为未知头部，原样保留）
KNOWN_HEADERS = frozenset({
    "playname", "topindex", "saveplaypos", "playtime", "extplaylist",
})

#: 值中不允许出现的字符（DPL 无转义机制，只能丢弃）
_UNSAFE_VALUE_CHARS = "\r\n"


# ── 编解码 ────────────────────────────────────────────────────────────────────

def decode_bytes(data: bytes) -> str:
    """把播放列表字节解码为字符串。

    PotPlayer 导出一般为 UTF-8 with BOM；社区流传的文件常见 GB18030(GBK)，
    个别工具会写 UTF-16。按 BOM → UTF-8 → GB18030 顺序尝试，保证不抛异常。
    """
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig")
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16")
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_playlist_file(path: str) -> str:
    """读取任意编码的播放列表文件（M3U / TXT / DPL 通用）。"""
    with open(path, "rb") as f:
        return decode_bytes(f.read())


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class DplEntry:
    """DPL 中的一条记录。"""

    index: int = 0
    file: str = ""
    title: str = ""
    played: bool = False
    invalid: bool = False
    author: str = ""
    thumbnail: str = ""
    duration: str = ""
    #: 未识别的扩展字段，原样保留以便无损回写
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class DplPlaylist:
    """解析后的完整 DPL 播放列表。"""

    entries: List[DplEntry] = field(default_factory=list)
    playname: str = ""
    topindex: int = 0
    saveplaypos: int = 0
    #: 未识别的头部字段
    extra_headers: Dict[str, str] = field(default_factory=dict)


# ── 识别 ──────────────────────────────────────────────────────────────────────

def is_dpl(content: str) -> bool:
    """判断文本是否为 DPL 播放列表。"""
    if not content:
        return False
    head = content.lstrip("\ufeff \t\r\n")
    return head[: len(DPL_MAGIC)].upper() == DPL_MAGIC


def _to_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _clean(value: str) -> str:
    """去掉值中的换行与首尾空白，避免破坏单行结构。"""
    for ch in _UNSAFE_VALUE_CHARS:
        value = value.replace(ch, " ")
    return value.strip()


# ── 解析 ──────────────────────────────────────────────────────────────────────

def parse_dpl(text: str) -> DplPlaylist:
    """解析 DPL 文本，返回 :class:`DplPlaylist`。

    解析器对脏数据宽容：无法识别的行直接跳过，``file`` 为空的条目丢弃。
    """
    playlist = DplPlaylist()
    by_index: Dict[int, DplEntry] = {}
    order: List[int] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        entry_match = _ENTRY_RE.match(line)
        if entry_match:
            idx = int(entry_match.group(1))
            field_name = entry_match.group(2).strip().lower()
            value = entry_match.group(3)
            entry = by_index.get(idx)
            if entry is None:
                entry = DplEntry(index=idx)
                by_index[idx] = entry
                order.append(idx)
            if field_name == "file":
                entry.file = value.strip()
            elif field_name == "title":
                entry.title = _clean(value)
            elif field_name == "played":
                entry.played = _to_bool(value)
            elif field_name == "invalid":
                entry.invalid = _to_bool(value)
            elif field_name == "author":
                entry.author = _clean(value)
            elif field_name == "thumbnail":
                entry.thumbnail = value.strip()
            elif field_name == "duration":
                entry.duration = value.strip()
            else:
                entry.extra[field_name] = _clean(value)
            continue

        header_match = _HEADER_RE.match(line)
        if header_match:
            key = header_match.group(1).strip().lower()
            value = header_match.group(2).strip()
            if key == "playname":
                playlist.playname = value
            elif key == "topindex":
                try:
                    playlist.topindex = int(value)
                except ValueError:
                    playlist.topindex = 0
            elif key == "saveplaypos":
                try:
                    playlist.saveplaypos = int(value)
                except ValueError:
                    playlist.saveplaypos = 0
            elif key not in KNOWN_HEADERS:
                playlist.extra_headers[key] = value
            continue
        # 魔术字 DAUMPLAYLIST 及其它噪声行直接忽略

    playlist.entries = [
        by_index[i] for i in order if by_index[i].file
    ]
    # 重新编号，保证 1..N 连续
    for pos, entry in enumerate(playlist.entries, start=1):
        entry.index = pos
    return playlist


def entries_to_channels(
    playlist: DplPlaylist,
    page_url: str = "",
    carry_title: bool = True,
    skip_invalid: bool = False,
    dedupe: bool = True,
) -> List[dict]:
    """把 DPL 条目转换为 ``test_logic.parse_source`` 使用的频道字典。

    参数
    ----
    carry_title
        条目缺少 ``title`` 时沿用上一条的标题。PotPlayer 允许省略 title，
        实际流传的 DPL 常在首条写一次标题、后续镜像条目留空，沿用可避免
        频道名变成一长串 URL。
    skip_invalid
        跳过 PotPlayer 已标记 ``invalid=1`` 的条目。
    dedupe
        相同 URL 只保留一条，避免重复测试同一条流。
    """
    channels: List[dict] = []
    seen_urls = set()
    last_title = ""

    for entry in playlist.entries:
        if skip_invalid and entry.invalid:
            continue

        url = entry.file.strip()
        if not url:
            continue

        title = entry.title or (last_title if carry_title else "")
        if entry.title:
            last_title = entry.title

        if dedupe:
            if url in seen_urls:
                continue
            seen_urls.add(url)

        channels.append({
            "name": title,
            "link_str": url,
            "group_name": entry.author,
            "extvlcopt_lines": [],
            "page_url": page_url,
            "extinf_headers": {"tvg-logo": entry.thumbnail} if entry.thumbnail else {},
            "raw_extinf_line": None,
            # 供导出时回写 DPL 元数据
            "dpl_played": entry.played,
            "dpl_invalid": entry.invalid,
            "dpl_thumbnail": entry.thumbnail or None,
            "dpl_duration": entry.duration or None,
        })
    return channels


# ── 生成 ──────────────────────────────────────────────────────────────────────

def _sanitize_value(value: str) -> str:
    return _clean(value or "")


def _split_links(link_str: str) -> List[str]:
    """拆分 ``a#b#c`` 形式的多镜像地址。"""
    if not link_str:
        return []
    return [part.strip() for part in link_str.split("#") if part.strip()]


def build_dpl(
    channels: Iterable,
    playname: Optional[str] = None,
    topindex: int = 0,
    saveplaypos: int = 0,
    include_played: bool = False,
    include_invalid: bool = False,
    newline: str = "\r\n",
    with_bom: bool = True,
) -> str:
    """把频道列表生成为 DPL 文本。

    ``channels`` 可以是 :class:`test_logic.Channel` 对象，也可以是包含
    ``name`` / ``link_str`` / ``group_name`` 等键的字典。

    一个频道若带多条镜像地址（``a#b``），会展开成多条同标题条目，
    与 PotPlayer 原生行为一致。
    """
    lines: List[str] = [DPL_MAGIC]
    body: List[str] = []
    index = 0

    for ch in channels:
        get = ch.get if isinstance(ch, dict) else (lambda k, d=None, _c=ch: getattr(_c, k, d))

        name = _sanitize_value(get("name", "") or "")
        group = _sanitize_value(get("group_name", "") or "")
        link_str = get("link_str", "") or ""
        final_url = get("final_url", "") or ""
        thumbnail = _sanitize_value(get("dpl_thumbnail", "") or "")
        duration = _sanitize_value(get("dpl_duration", "") or "")
        played = bool(get("dpl_played", False))
        invalid = bool(get("dpl_invalid", False))

        if not thumbnail:
            headers = get("extinf_headers", {}) or {}
            if isinstance(headers, dict):
                thumbnail = _sanitize_value(headers.get("tvg-logo", "") or "")

        links = _split_links(link_str) or _split_links(final_url)
        if not links:
            continue

        for link in links:
            index += 1
            body.append(f"{index}*file*{_sanitize_value(link)}")
            body.append(f"{index}*title*{name or link}")
            if include_played and played:
                body.append(f"{index}*played*1")
            if include_invalid and invalid:
                body.append(f"{index}*invalid*1")
            if group and group != "未分组":
                body.append(f"{index}*author*{group}")
            if thumbnail:
                body.append(f"{index}*thumbnail*{thumbnail}")
            if duration:
                body.append(f"{index}*duration*{duration}")

    if not body:
        return ""

    if playname is None:
        playname = body[0].split("*file*", 1)[-1] if body else ""

    lines.append(f"playname={_sanitize_value(playname)}")
    lines.append(f"topindex={max(0, int(topindex))}")
    lines.append(f"saveplaypos={max(0, int(saveplaypos))}")
    lines.extend(body)

    text = newline.join(lines) + newline
    return ("\ufeff" + text) if with_bom else text


def write_dpl_file(path: str, channels: Iterable, **kwargs) -> int:
    """生成并写出 DPL 文件，返回写入的条目数。"""
    text = build_dpl(channels, **kwargs)
    if not text:
        return 0
    # 以字节写出，保证 BOM 与 CRLF 原样落地
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))
    return text.count("*file*")
