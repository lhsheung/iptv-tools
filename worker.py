"""
IPTV Tools Python Worker —— NDJSON 前后端通信进程
开发维护: https://iptv-search.com

被 WinUI 3 前端以子进程方式拉起，通过 stdin/stdout 收发 **JSON Lines**
（每行一个 UTF-8 JSON 对象）。严格协议约束：

- **stdout 只输出 JSON**，一事件一行，不允许任何普通日志混入。
- **stderr 只放诊断日志**，供前端单独收集，不参与解析。
- 启动时立刻输出一条 ``ready`` 事件，前端据此判断后端就绪。

调用方（C#）：:

    var p = Process.Start(new ProcessStartInfo(python, "worker.py")
    {
        RedirectStandardInput = true,
        RedirectStandardOutput = true,
        RedirectStandardError = true,
        UseShellExecute = false,
        StandardOutputEncoding = Encoding.UTF8,
    });

请求（前端 → Worker）::

    {"id": 7, "cmd": "parse", "params": {...}}

响应（Worker → 前端）::

    {"id": 7, "type": "result", "data": {...}}     // 请求对应的结果
    {"id": 7, "type": "error",  "message": "..."}  // 请求失败
    {"type": "event", "event": "progress", ...}    // 与某个长任务相关的推送

命令一览
--------
``ping``            心跳
``info``            运行环境与能力（python 版本、依赖、ffmpeg、ip2region）
``parse``           解析 M3U / TXT / DPL 内容或文件
``test``            批量测试频道（并发），逐个推送 ``progress`` 事件
``cancel``          取消当前测试任务
``ffmpeg_check``    对单个或多个地址做 FFprobe + FFmpeg 真解码
``export``          导出 M3U / DPL 文件
``open_browser``    用系统浏览器打开链接（便于排查源页面）
"""

import io
import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Dict, List

# ── 强制 UTF-8：Windows 控制台默认 GBK 会把 JSON 里的中文写坏 ──────────────────
for _stream_name in ("stdin", "stdout", "stderr"):
    _s = getattr(sys, _stream_name, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ── 输出通道 ─────────────────────────────────────────────────────────────────
# 关键隔离：把真正的 stdout 句柄留给自己写 JSON，然后把 sys.stdout 指向 stderr，
# 这样任何库（requests、urllib3、ip2region……）的 print 都不会污染 NDJSON 通道。
_json_stream = sys.stdout
sys.stdout = sys.stderr

_out_lock = threading.Lock()


def emit(obj: Dict[str, Any]) -> None:
    """向 stdout 写一条 JSON 事件（线程安全）。"""
    try:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        line = json.dumps({"type": "error", "message": f"结果序列化失败: {e}"},
                          ensure_ascii=False)
    with _out_lock:
        try:
            _json_stream.write(line + "\n")
            _json_stream.flush()
        except Exception:
            # stdout 已断（前端退出），直接结束进程
            os._exit(0)


def log(msg: str) -> None:
    """诊断日志走 stderr，绝不影响 JSON 通道。"""
    try:
        sys.stderr.write(f"[worker] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def result(req_id, data) -> None:
    emit({"id": req_id, "type": "result", "data": data})


def error(req_id, message: str, detail: str = "") -> None:
    payload = {"id": req_id, "type": "error", "message": message}
    if detail:
        payload["detail"] = detail
    emit(payload)


def event(req_id, name: str, **fields) -> None:
    payload = {"id": req_id, "type": "event", "event": name}
    payload.update(fields)
    emit(payload)


# ── 依赖按需加载，缺依赖时给出可读错误而不是崩溃 ──────────────────────────────

def _load_test_logic():
    import test_logic
    return test_logic


def _load_dpl():
    import dpl_format
    return dpl_format


def _load_ffmpeg():
    import ffmpeg_check
    return ffmpeg_check


# ── 取消标志 ─────────────────────────────────────────────────────────────────
_cancel_flag = threading.Event()
_current_job = {"id": None}


def cmd_ping(req_id, params):
    result(req_id, {"pong": True, "time": time.time()})


def cmd_info(req_id, params):
    """返回运行环境与能力信息，前端据此点亮/置灰功能。"""
    info: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "frozen": bool(getattr(sys, "frozen", False)),
        "cwd": os.getcwd(),
    }

    try:
        import requests
        info["requests"] = requests.__version__
    except Exception:
        info["requests"] = None

    try:
        tl = _load_test_logic()
        info["test_logic"] = True
        # ip2region 是否可用（离线归属地）
        try:
            searcher = tl.init_xdb()
            info["ip2region"] = bool(searcher) or bool(getattr(tl, "_XDB_LOADED", False))
        except Exception as e:
            info["ip2region"] = False
            info["ip2region_error"] = str(e)
    except Exception as e:
        info["test_logic"] = False
        info["test_logic_error"] = str(e)

    try:
        dpl = _load_dpl()
        info["dpl"] = True
        info["dpl_version"] = getattr(dpl, "__version__", "")
    except Exception:
        info["dpl"] = False

    try:
        fc = _load_ffmpeg()
        info["ffmpeg"] = fc.ffmpeg_available()
    except Exception as e:
        info["ffmpeg"] = {"ffmpeg_ok": False, "ffprobe_ok": False,
                          "error": str(e)}

    result(req_id, info)


def _read_source(params) -> str:
    """从参数里取播放列表文本：支持 content / path / url 三种来源。"""
    content = params.get("content")
    if content:
        return content

    path = params.get("path")
    if path:
        dpl = _load_dpl()
        return dpl.read_playlist_file(path)

    url = params.get("url")
    if url:
        import requests
        headers = {"User-Agent": params.get("user_agent") or
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, timeout=params.get("timeout", 20), headers=headers)
        r.raise_for_status()
        dpl = _load_dpl()
        return dpl.decode_bytes(r.content)

    raise ValueError("缺少 content / path / url 之一")


def cmd_parse(req_id, params):
    """解析播放列表，返回统一结构的频道数组。"""
    tl = _load_test_logic()
    dpl = _load_dpl()

    content = _read_source(params)
    page_url = params.get("page_url") or params.get("path") or params.get("url") or ""

    valid, fmt = tl.is_valid_playlist_content(content)
    if not valid:
        error(req_id, f"无法识别的播放列表格式: {fmt}")
        return

    if fmt == "dpl":
        channels = tl.parse_dpl_source(
            content, page_url,
            carry_title=params.get("carry_title", True),
            skip_invalid=params.get("skip_invalid", False),
            dedupe=params.get("dedupe", True),
        )
    else:
        channels = tl.parse_source(content, page_url)

    result(req_id, {
        "format": fmt,
        "count": len(channels),
        "channels": [_channel_dict(c) for c in channels],
    })


def _channel_dict(c) -> Dict[str, Any]:
    """Channel 对象 → 前端友好的扁平结构。"""
    return {
        "name": c.name or "",
        "url": c.link_str or "",
        "group": c.group_name or "",
        "logo": c.dpl_thumbnail or (c.extinf_headers or {}).get("tvg-logo", "") or "",
        "played": bool(getattr(c, "dpl_played", False)),
        "invalid": bool(getattr(c, "dpl_invalid", False)),
        "duration": getattr(c, "dpl_duration", None),
    }


def cmd_test(req_id, params):
    """批量测试频道。

    参数::

        {"channels": [{"name","url","group"}...],   // 必须
         "config": {"max_workers":8,"timeout":15,"min_fluency_score":10,
                    "check_fluency":true,"check_live":true,
                    "quick_live_check":false,"strict_validation":true,
                    "domain_blacklist":[]},
         "mode": "http" | "ffmpeg" | "both",       // 默认 http
         "decode_seconds": 3,
         "limit": 0}                                // 0=不限
    """
    tl = _load_test_logic()
    raw_channels = params.get("channels") or []
    if not raw_channels:
        error(req_id, "没有要测试的频道")
        return

    limit = int(params.get("limit") or 0)
    if limit > 0:
        raw_channels = raw_channels[:limit]

    cfg_in = params.get("config") or {}
    config = tl.TestConfig(
        max_workers=int(cfg_in.get("max_workers", 8)),
        min_fluency_score=int(cfg_in.get("min_fluency_score", 10)),
        timeout=int(cfg_in.get("timeout", 15)),
        check_fluency=bool(cfg_in.get("check_fluency", True)),
        check_live=bool(cfg_in.get("check_live", True)),
        quick_live_check=bool(cfg_in.get("quick_live_check", False)),
        strict_validation=bool(cfg_in.get("strict_validation", True)),
        domain_blacklist=list(cfg_in.get("domain_blacklist") or []),
    )

    mode = params.get("mode", "http")
    decode_seconds = float(params.get("decode_seconds", 3))
    fc = None
    ffmpeg_path = ffprobe_path = ""
    if mode in ("ffmpeg", "both"):
        fc = _load_ffmpeg()
        avail = fc.ffmpeg_available()
        ffmpeg_path, ffprobe_path = avail["ffmpeg"], avail["ffprobe"]
        if not ffmpeg_path:
            error(req_id, "需要 FFmpeg 才能用解码检测，但未找到 ffmpeg.exe")
            return

    _cancel_flag.clear()
    _current_job["id"] = req_id

    total = len(raw_channels)
    done = 0
    valid_count = 0
    started = time.time()
    lock = threading.Lock()
    results: List[Dict[str, Any]] = []

    def make_channel(raw):
        return tl.Channel(
            name=raw.get("name") or "",
            link_str=raw.get("url") or raw.get("link_str") or "",
            group_name=raw.get("group") or raw.get("group_name") or "",
            dpl_thumbnail=raw.get("logo"),
        )

    def work(raw):
        """单频道测试：先走 http 逻辑，需要时再叠 FFmpeg 解码。"""
        ch = make_channel(raw)
        out: Dict[str, Any] = {"url": ch.link_str, "name": ch.name,
                               "group": ch.group_name}
        try:
            ch = tl.test_channel_via_config(ch, config)
            out.update({
                "ok": bool(ch.is_valid),
                "response_ms": ch.response_time or "",
                "location": ch.location or "",
                "codec": ch.codec or "",
                "fluency_score": ch.fluency_score,
                "fluency_level": ch.fluency_level or "",
                "bitrate_kbps": ch.bitrate_kbps,
                "final_url": ch.final_url or ch.link_str,
                "via": "http",
            })
        except Exception as e:
            out.update({"ok": False, "error": f"http 检测异常: {e}",
                        "via": "http"})

        if mode in ("ffmpeg", "both") and (mode == "ffmpeg" or out.get("ok")):
            target = out.get("final_url") or ch.link_str
            try:
                dp = fc.decode_probe(target, ffmpeg_path, ffprobe_path,
                                     seconds=decode_seconds)
                out["ffmpeg"] = dp
                out["decode_ok"] = dp["ok"]
                if mode == "ffmpeg":
                    out["ok"] = dp["ok"]
                elif dp["ok"]:
                    # http 通过 + 能真解码 → 更高可信度
                    out["ok"] = True
                out["summary"] = dp["summary"]
                pinfo = dp.get("probe") or {}
                if pinfo.get("video_codec") and not out.get("codec"):
                    out["codec"] = pinfo["video_codec"]
            except Exception as e:
                out["decode_ok"] = False
                out.setdefault("ffmpeg", {})["error"] = str(e)

        return out

    def worker_loop():
        nonlocal done, valid_count
        while not _cancel_flag.is_set():
            with lock:
                if not raw_channels:
                    return
                raw = raw_channels.pop(0)
            if _cancel_flag.is_set():
                return

            item = work(raw)

            with lock:
                done += 1
                if item.get("ok"):
                    valid_count += 1
                results.append(item)
                cur_done, cur_valid = done, valid_count

            event(req_id, "progress", done=cur_done, total=total,
                  valid=cur_valid, item=item)

    threads = [threading.Thread(target=worker_loop, daemon=True)
               for _ in range(max(1, min(config.max_workers, 32)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = round(time.time() - started, 2)
    cancelled = _cancel_flag.is_set()
    _current_job["id"] = None

    result(req_id, {
        "total": total,
        "done": done,
        "valid": valid_count,
        "elapsed": elapsed,
        "cancelled": cancelled,
        "mode": mode,
        "results": results,
    })


def cmd_cancel(req_id, params):
    _cancel_flag.set()
    result(req_id, {"cancelled": True, "job": _current_job["id"]})


def cmd_ffmpeg_check(req_id, params):
    """对单个/多个地址做 FFprobe + FFmpeg 解码检测。"""
    fc = _load_ffmpeg()
    avail = fc.ffmpeg_available()
    if not avail["ffmpeg"]:
        error(req_id, "未找到 ffmpeg.exe，无法解码检测")
        return

    urls = params.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        error(req_id, "缺少 urls 参数")
        return

    seconds = float(params.get("decode_seconds", 3))
    out = []
    for i, u in enumerate(urls, 1):
        dp = fc.decode_probe(u, avail["ffmpeg"], avail["ffprobe"], seconds=seconds)
        out.append(dp)
        event(req_id, "progress", done=i, total=len(urls), item=dp)

    result(req_id, {"count": len(out), "items": out})


def cmd_export(req_id, params):
    """导出播放列表（M3U 或 DPL）。"""
    fmt = (params.get("format") or "m3u").lower()
    path = params.get("path")
    channels = params.get("channels") or []
    if not path:
        error(req_id, "缺少 path 参数")
        return
    if not channels:
        error(req_id, "没有可导出的频道")
        return

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    if fmt == "dpl":
        dpl = _load_dpl()
        count = dpl.write_dpl_file(
            path, channels,
            playname=params.get("playname") or None,
            topindex=int(params.get("topindex", 0)),
            saveplaypos=int(params.get("saveplaypos", 0)),
            include_played=bool(params.get("include_played", False)),
            include_invalid=bool(params.get("include_invalid", False)),
        )
        result(req_id, {"path": path, "format": "dpl", "entries": count})
        return

    # M3U
    lines = ["#EXTM3U"]
    written = 0
    for ch in channels:
        url = ch.get("final_url") or ch.get("url") or ""
        if not url:
            continue
        group = ch.get("group") or "未分组"
        name = ch.get("name") or url
        extra = ""
        if ch.get("logo"):
            extra += f' tvg-logo="{ch["logo"]}"'
        if ch.get("fluency_score") is not None:
            extra += f' fluency_score="{ch["fluency_score"]}"'
        if ch.get("response_ms"):
            extra += f' response_time="{ch["response_ms"]}"'
        lines.append(f'#EXTINF:-1 group-title="{group}"{extra}, {name}')
        lines.append(url)
        written += 1

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    result(req_id, {"path": path, "format": "m3u", "entries": written})


def cmd_open_browser(req_id, params):
    import webbrowser
    url = params.get("url")
    if not url:
        error(req_id, "缺少 url")
        return
    webbrowser.open(url)
    result(req_id, {"opened": url})


# ── 命令路由 ─────────────────────────────────────────────────────────────────

HANDLERS = {
    "ping": cmd_ping,
    "info": cmd_info,
    "parse": cmd_parse,
    "test": cmd_test,
    "cancel": cmd_cancel,
    "ffmpeg_check": cmd_ffmpeg_check,
    "export": cmd_export,
    "open_browser": cmd_open_browser,
}


def handle_line(line: str) -> None:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as e:
        emit({"type": "error", "message": f"请求 JSON 解析失败: {e}"})
        return

    if not isinstance(req, dict):
        emit({"type": "error", "message": "请求必须是 JSON 对象"})
        return

    req_id = req.get("id")
    cmd = req.get("cmd")
    params = req.get("params") or {}

    handler = HANDLERS.get(cmd)
    if handler is None:
        error(req_id, f"未知命令: {cmd}")
        return

    try:
        handler(req_id, params)
    except Exception as e:
        tb = traceback.format_exc()
        log(f"命令 {cmd} 失败: {e}\n{tb}")
        error(req_id, str(e), tb.splitlines()[-1] if tb else "")


def main() -> None:
    log("worker 启动")
    emit({
        "type": "ready",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "pid": os.getpid(),
        "commands": sorted(HANDLERS),
    })

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        handle_line(line)

    log("worker 退出")


if __name__ == "__main__":
    main()
