"""ip2region Python 绑定。

对外暴露本项目的适配层实现（见 ``iptest.py``）：
``load_xdb_file()`` 与 ``searchWithContent(url)``。
"""

from .iptest import load_xdb_file, searchWithContent, xdb_path

__all__ = ["load_xdb_file", "searchWithContent", "xdb_path"]
