"""
ip2region 查询适配层（供 test_logic.py 直接调用）

原项目期望的接口
----------------
``load_xdb_file()``        初始化并返回一个可复用的查询对象
``searchWithContent(url)`` 传入「地址」（URL 或 IP），返回中文归属地字符串

上游 ip2region 的 Python 绑定只是一个 demo（``searchWithContent`` 不接收参数、
且直接 print），语义与调用方不匹配。这里在上游 ``XdbSearcher`` 之上做薄封装：

- **xdb 文件定位**：依次尝试环境变量 ``IP2REGION_XDB``、本文件同级
  ``../../data/ip2region.xdb``、以及向上若干层目录，兼容源码运行与打包运行。
- **全量缓存模式**：xdb 一次性读入内存，查询不再触碰磁盘，多线程安全。
  10 万次查询量级下这是唯一实用的做法。
- **IP 提取**：``searchWithContent`` 收到的是完整 URL，先解析出主机名，
  是域名则就地解析出 IPv4 再查询。
- 任何异常都向上抛出，由调用方（test_logic）自行兜底成「未知」，
  避免这里的错误被静默吞掉而难以排查。
"""

import os
import socket
import threading
from typing import Optional
from urllib.parse import urlsplit

try:
    from .xdbSearcher import XdbSearcher
except ImportError:  # 以顶层模块方式被 import 时
    from xdbSearcher import XdbSearcher

__all__ = ["load_xdb_file", "searchWithContent", "get_searcher", "xdb_path"]

# ── xdb 文件定位 ─────────────────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))


def _candidate_paths():
    """按优先级给出可能存在的 xdb 路径。"""
    env = os.environ.get("IP2REGION_XDB")
    if env:
        yield env

    # binding/python/ → 向上逐层找 data/ip2region.xdb 或 ip2region.xdb
    base = _HERE
    for _ in range(6):
        yield os.path.join(base, "data", "ip2region.xdb")
        yield os.path.join(base, "ip2region.xdb")
        parent = os.path.dirname(base)
        if parent == base:
            break
        base = parent


def xdb_path() -> str:
    """返回可用的 xdb 路径；找不到时抛 FileNotFoundError。"""
    tried = []
    for p in _candidate_paths():
        p = os.path.normpath(p)
        if p in tried:
            continue
        tried.append(p)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "未找到 ip2region.xdb，已尝试: " + "; ".join(tried[:6])
    )


# ── 查询对象（全局单例，全量缓存） ───────────────────────────────────────────

_lock = threading.Lock()
_searcher: Optional[XdbSearcher] = None


def load_xdb_file() -> XdbSearcher:
    """加载 xdb 全量缓存并返回可复用的查询对象（线程安全单例）。"""
    global _searcher
    if _searcher is not None:
        return _searcher

    with _lock:
        if _searcher is not None:
            return _searcher
        path = xdb_path()
        content = XdbSearcher.loadContentFromFile(dbfile=path)
        _searcher = XdbSearcher(contentBuff=content)
        return _searcher


def get_searcher() -> Optional[XdbSearcher]:
    """已加载则返回，未加载返回 None（不触发加载）。"""
    return _searcher


# ── 地址 → IP ────────────────────────────────────────────────────────────────

def _is_ipv4(s: str) -> bool:
    """判断字符串是否为合法 IPv4。

    不用 ``XdbSearcher.isip``：上游把它实现成实例方法，而这里需要独立校验。
    """
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or len(p) > 3:
            return False
        if int(p) > 255:
            return False
    return True


def _extract_ip(target: str) -> str:
    """从 URL / host:port / 纯 IP 中取出 IPv4 字符串。

    域名会做一次 DNS 解析；失败则抛异常交给调用方兜底。
    """
    if not target:
        raise ValueError("空地址")

    s = target.strip()

    # 带 scheme 的完整 URL
    if "://" in s:
        host = urlsplit(s).hostname or ""
    else:
        # 可能是 host:port 或裸 IP
        host = s.split("/")[0].split("?")[0]
        if host.count(":") == 1:
            host = host.split(":")[0]

    host = host.strip().strip("[]")
    if not host:
        raise ValueError(f"无法从地址解析主机: {target[:80]}")

    # 已是 IPv4
    if _is_ipv4(host):
        return host

    # 域名 → IPv4（取第一个结果）
    infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    for info in infos:
        ip = info[4][0]
        if _is_ipv4(ip):
            return ip
    raise ValueError(f"域名无 IPv4 解析结果: {host}")


def _clean_region(raw: str) -> str:
    """把 ``中国|0|广东省|深圳市|电信`` 收敛成``广东省 深圳市 电信``。"""
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 5:
        return raw.strip()

    country, _, province, city, isp = parts[:5]
    bits = []
    for v in (country, province, city, isp):
        # 0 / 空 表示该级无数据
        if not v or v == "0":
            continue
        # 国家与省份重复时只留一个
        if v in bits:
            continue
        bits.append(v)
    return " ".join(bits)


def searchWithContent(target: str) -> str:
    """查询地址的归属地。

    参数可以是完整 URL、host:port、域名或裸 IP——调用方传的是播放地址。
    返回形如 ``广东省 深圳市 电信``；任何环节失败都会抛异常。
    """
    searcher = load_xdb_file()
    ip = _extract_ip(target)
    raw = searcher.search(ip) or ""
    cleaned = _clean_region(raw)
    return cleaned or "未知"


if __name__ == "__main__":
    # 自检：打印 xdb 路径与一次示例查询
    print("xdb:", xdb_path())
    for t in ("1.2.3.4", "http://www.baidu.com/live.m3u8", "114.114.114.114"):
        try:
            print(t, "->", searchWithContent(t))
        except Exception as e:
            print(t, "-> ERR", e)
