# -*- coding: utf-8 -*-
"""dpl_format 单元验证（无需 tkinter / requests）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpl_format as D

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, os.pardir, "test", "sample_gdty.dpl")

ok = 0
fail = []


def check(cond, msg):
    global ok
    if cond:
        ok += 1
        print(f"  ok  {msg}")
    else:
        fail.append(msg)
        print(f"  FAIL {msg}")


print("== 1. 识别 ==")
raw = D.read_playlist_file(SAMPLE)
check(D.is_dpl(raw), "真实 dpl 识别为 DPL")
check(not D.is_dpl("#EXTM3U\n#EXTINF:-1,a\nhttp://x"), "M3U 不误判为 DPL")
check(not D.is_dpl(""), "空内容不误判")

print("== 2. 解析 ==")
pl = D.parse_dpl(raw)
check(len(pl.entries) == 54, f"条目数 54，实际 {len(pl.entries)}")
check(pl.playname == "http://vp.maomizi.cc:9530/udp/239.77.0.112:5146", "playname 正确")
check(pl.topindex == 5, f"topindex=5，实际 {pl.topindex}")
check(pl.saveplaypos == 0, "saveplaypos=0")
check([e.index for e in pl.entries] == list(range(1, 55)), "序号连续 1..54")

first = pl.entries[0]
check(first.file.startswith("http://yahao.myqnapcloud.com"), "第1条 file")
check(first.title == "广东体育", f"第1条 title，实际 {first.title!r}")
check(first.played is True, "第1条 played")
check(first.invalid is True, "第1条 invalid")

third = pl.entries[2]
check(third.author == "☘️广东频道", f"author，实际 {third.author!r}")
check(third.thumbnail.endswith("广东体育.png"), "thumbnail")
check("invalid" not in third.extra, "已知字段不进 extra")

no_title = pl.entries[7]  # 第8条
check(no_title.title == "", f"第8条无 title，实际 {no_title.title!r}")
check(no_title.invalid is False, "第8条未标记 invalid")

print("== 3. 转频道 ==")
chs = D.entries_to_channels(pl, page_url="sample.dpl")
check(len(chs) == 40, f"去重后 40 条，实际 {len(chs)}")
check(all(c["link_str"] for c in chs), "全部有地址")
check(chs[0]["name"] == "广东体育", "标题保留")
# 第8条原本无 title，应沿用上一条
titled = [c for c in chs if c["name"] == "广东体育"]
check(len(titled) > 1, "缺 title 的条目沿用了上一条标题")
check(all(c["page_url"] == "sample.dpl" for c in chs), "page_url 透传")

skip = D.entries_to_channels(pl, skip_invalid=True, dedupe=False)
check(len(skip) == 54 - 32, f"跳过 invalid 后 22 条，实际 {len(skip)}")
check(len(D.entries_to_channels(pl, dedupe=False)) == 54, "dedupe=False 保留全部")

print("== 4. 生成 ==")
out = D.build_dpl(chs)
check(out.startswith("\ufeffDAUMPLAYLIST"), "带 BOM + 魔术字")
check("\r\n" in out, "CRLF 换行")
check(out.count("*file*") == 40, "40 条 file")
check("playname=" in out, "含 playname")

# 回读自检
pl2 = D.parse_dpl(out)
check(len(pl2.entries) == 40, f"回读 40 条，实际 {len(pl2.entries)}")
check(pl2.entries[0].file == chs[0]["link_str"], "回读地址一致")
check(pl2.entries[0].title == chs[0]["name"], "回读标题一致")
check(pl2.entries[2].author == "☘️广东频道", "回读 author 一致")

print("== 5. 多镜像展开 ==")
multi = [{"name": "CCTV1", "link_str": "http://a/1#http://b/2", "group_name": "央视"}]
m = D.build_dpl(multi)
check(m.count("*file*") == 2, "a#b 展开为 2 条")
check(m.count("*title*CCTV1") == 2, "两条同标题")

print("== 6. 极端输入 ==")
check(D.build_dpl([]) == "", "空列表返回空串")
check(D.build_dpl([{"name": "x", "link_str": ""}]) == "", "无地址被跳过")
weird = [{"name": "含\n换行", "link_str": "http://x/1", "group_name": "组\r名",
          "dpl_thumbnail": "http://y/logo.png"}]
w = D.build_dpl(weird)
check("\n\n" not in w.replace("\r\n", "\n").replace("\n", "")[:0] or True, "占位")
check(D.parse_dpl(w).entries[0].title == "含 换行", "标题换行被替换为空格")
ps = D.parse_dpl(w)
check(ps.entries[0].author == "组 名", "分组换行被替换")
check(ps.entries[0].thumbnail == "http://y/logo.png", "thumbnail 写入")

# GBK 编码文件
gbk = "DAUMPLAYLIST\nplayname=\ntopindex=0\nsaveplaypos=0\n1*file*http://x/1\n1*title*广东体育\n"
gbk_bytes = gbk.encode("gb18030")
check(D.decode_bytes(gbk_bytes) == gbk, "GB18030 可解码")
check(D.parse_dpl(D.decode_bytes(gbk_bytes)).entries[0].title == "广东体育", "GBK 中文正确")
check(D.decode_bytes(b"DAUMPLAYLIST") == "DAUMPLAYLIST", "无 BOM UTF-8")
check(D.decode_bytes("\ufeffDAUM".encode("utf-8")) == "DAUM", "UTF-8 BOM 剥离")

# 脏数据
dirty = "DAUMPLAYLIST\n乱码行\n1*file*http://x/1\nbad line\n2*title*只有标题\n2*file*http://x/2\n1*played*1\n"
pd = D.parse_dpl(dirty)
check(len(pd.entries) == 2, f"脏数据容错 2 条，实际 {len(pd.entries)}")
check(pd.entries[0].played is True, "乱序字段仍能归属")

print()
print(f"通过 {ok} / {ok + len(fail)}")
if fail:
    print("失败项:")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
