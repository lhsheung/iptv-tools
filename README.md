# IPTV 直播源测试工具 v3（FFmpeg 解码 + Apple 风格界面）

在原版（goplay-source/IPTV-tools，Python + tkinter）基础上做了两件事：
**加入 FFmpeg 真实解码检测**、**按 Apple 的设计取向重构界面**。
上一阶段加入的 PotPlayer DPL 导入导出能力完整保留。

---

## 零、打包版（免安装，推荐）

`dist/` 下提供两种产物，都不需要装 Python 或任何依赖，双击即用：

| 产物 | 体积 | 形态 | 解码检测 |
|---|---|---|---|
| `IPTVTools.exe` | **28.6 MB** | 单文件 | 不带 FFmpeg，HTTP 探活可用 |
| `IPTVTools/`（`--full`） | 约 496 MB | 目录版 | 内含 FFmpeg，功能齐全 |

**体积差全在 FFmpeg**：ffmpeg.exe 218 MB + ffprobe.exe 217 MB 就占 435 MB，
Python 运行时 + tkinter/Pillow + 本项目代码 + 11 MB 离线 IP 库合计只有 58 MB。
原版 14.8 MB 之所以小，是因为它只有 HTTP 探活、没有真实解码能力。

### 精简版怎么补上解码检测

在 `IPTVTools.exe` **同级**建目录 `tools/ffmpeg/bin/`，把 `ffmpeg.exe` 和
`ffprobe.exe` 放进去，重启程序即自动启用（设置页会显示 FFmpeg 状态）。
不放也完全可以正常使用，只是「FFmpeg 解码」「叠加检测」会降级，界面给出提示。

```
dist/
├── IPTVTools.exe
├── 使用说明.md
└── tools/ffmpeg/bin/
    ├── ffmpeg.exe
    └── ffprobe.exe
```

### 其他说明

- 单文件版首次启动会自解压到临时目录，约 2~3 秒出窗口。
- `config.json`、导出的 DPL / M3U 都生成在 **exe 同级**，不会藏进临时目录。
- 高分屏（125% / 150%）自动按真实 DPI 换算窗口尺寸与字号，无需手动设置。
- 构建命令：`python build.py`（精简版）/ `python build.py --full`（完整版）。

### 拖拽打开

三种方式都汇到同一个入口，行为完全一致（解析 → 去重 → 更新状态 → 切到结果页）：

- **拖到已运行的窗口**：把 `.m3u` / `.m3u8` / `.txt` / `.dpl` 拖到窗口任意位置
  （含标题栏）。一次可拖多个文件，也可以拖一个文件夹（只扫一层，不递归）。
- **拖到 exe 图标上启动**：Windows 把文件路径作为启动参数传入，启动后自动载入。
- **「打开文件」按钮**：支持多选。

扩展名不支持的文件会被忽略，并在日志里给出忽略数量，不打断当前操作。

实现用 Win32 原生 `WM_DROPFILES`（`DragAcceptFiles` + 窗口过程子类化），
零第三方依赖、不增加包体积。**窗口过程里不能调用 Tk API** —— Tk 在消息循环中
会释放 GIL，从窗口过程调 `root.after()` 会让 Python 3.14 直接 fatal；因此
窗口过程只把路径放进队列，由主线程 80ms 轮询取走。

两点平台限制：只有"放下"这一刻有消息，做不到悬停高亮（需要 IDropTarget COM）；
若程序以管理员权限运行、资源管理器是普通权限，UIPI 会拦截拖放消息，
以普通权限运行即可。

---

## 一、怎么运行（源码）

```bash
cd out
python main.py
```

要求 Python 3.9+，需要 `requests`；界面美化依赖 `Pillow`（没装也能跑，只是圆角边缘会略糙）。
本机验证环境：Python 3.14.7 + Tk 9.0 + Pillow 13.0。

首次运行若要联网检测，需要 `requests`：

```bash
pip install requests pillow
```

目录结构（`out/` 就是可直接运行的目录）：

```
out/
├── main.py              程序入口（DPI 感知 + 窗口尺寸换算）
├── gui.py               主界面（三页布局 + 底部操作栏）
├── theme.py             设计令牌：颜色 / 字号 / 间距 / 圆角 / 动效时长
├── widgets.py           自绘控件：卡片 / 胶囊按钮 / 分段控件 / 开关 / 滑块 / 虚拟列表
├── test_runner.py       批量检测调度：HTTP 探活 + FFmpeg 解码 组合
├── ffmpeg_check.py      FFprobe 解析 + FFmpeg 真解码
├── test_logic.py        HTTP 检测核心（上游逻辑，少量兼容性修改）
├── dpl_format.py        DPL 读写
├── ip2region_master/    离线归属地绑定的适配层
├── ip2region.xdb        离线 IP 库（11 MB）
├── tools/ffmpeg/bin/    ffmpeg.exe + ffprobe.exe
├── config.json          运行时生成
└── test_*.py            五个测试套件（共 189 项断言）
```

---

## 二、新增：FFmpeg 真实解码

### 为什么需要

HTTP 探活只能说明"连得上"。直播源里大量存在：

- 返回 200 但没有码流
- 能连上但编码不支持、花屏
- 需要特定 UA/Referer 才能拉流

这些都只有**真的解出画面**才能识别。

### 三种检测方式

在「设置 → 检测方式」里切换，界面上会说明各自代价：

| 方式 | 做什么 | 适用 |
|---|---|---|
| HTTP 探活 | 检查响应码、流特征、流畅度 | 几千条源先粗筛，最快 |
| FFmpeg 解码 | 真实解码 N 秒，统计实际帧数 | 最终确认，最准 |
| 叠加检测 | 先探活，通过后再解码复核 | 默认推荐，准确与速度折中 |

### 实现要点

- **FFprobe** 先解析媒体信息（容器、视频/音频编码、分辨率、码率）
- **FFmpeg** 用 `-f null -` 只解码不落盘，读 `-progress pipe:1` 的 key=value 拿到实时帧数
- 每个检测是独立子进程，超时即 kill，不会把线程卡死
- 音轨判断读 ffmpeg 的流信息（`Stream #0:1: Audio: aac ...`），
  不用字节数猜 —— 旧写法会把所有音视频流误报成"无音频"

### 结果表里的「解码」列

- `✓ 48帧` 真的解出了画面
- `✕ 失败` 连得上但解不出（这类源在实际播放时会黑屏）
- `—` 本次没做解码检测

右键任意一行可以「用 FFmpeg 复核这一条」，单条即时验证。

### FFmpeg 放哪里

程序按这个顺序找：

1. 环境变量指定的路径
2. 打包后：`_MEIPASS/tools/ffmpeg/bin/`（完整版内置）
3. **exe 同级的** `tools/ffmpeg/bin/`（精简版外挂，推荐）
4. 源码目录下的 `tools/ffmpeg/bin/`、`ffmpeg/bin/`
5. 系统 PATH

没有 FFmpeg 时界面会在设置页给出明确提示，并把解码相关功能降级，
HTTP 探活仍然可用。

---

## 三、界面重构

### 设计取向

参考 Apple 的界面设计原则（WWDC *Designing Fluid Interfaces* / *Principles of Great Design*），
落到 tkinter 上具体是：

- **单一色相**：只有系统蓝一个强调色，其余全是中性灰阶。没有渐变、没有彩色描边。
- **白卡片 + 发丝线**：层级靠留白和白卡片建立，不靠边框盒子堆叠。
- **字号 5 档**：22 / 15 / 12 / 10 / 9。不出现第六种。
- **圆角 3 档**：控件 8、卡片 12、浮层 18。
- **按下即反馈**：按钮在 `ButtonPress` 就变暗，不等抬起。
- **动效只做反馈**：悬停 120ms、选中迁移 180ms、进度 220ms，全是缓出，没有装饰动画。
- **中断从当前值出发**：颜色动画被打断时从当前颜色继续插值，不跳变。

### 三页布局

- **导入源**：源地址输入 + 打开文件 / 解析并载入 / 历史源；DPL 导入选项三个开关。
- **测试结果**：统计胶囊（可用/不可用/总计）+ 筛选分段控件 + 搜索框 + 结果表。
- **设置**：检测方式（含解码时长与 FFmpeg 状态）、性能（并发/超时/流畅度下限）、
  检测项开关、DPL 导出选项。

底部常驻操作栏：开始 / 暂停 / 停止 + 进度与剩余时间估算 + 导出按钮。
切页不打断正在进行的检测。

### 关于"毛玻璃"

tkinter 没有 `backdrop-filter`，做不出真正的材质模糊。这里用极浅的中性底色
模拟"半透明层压在白底上"的观感，而不是假装有模糊 —— 这是平台限制，不做虚假承诺。

### 自绘控件

ttk 在 Windows 上只能出方角控件，做不出圆角与连续反馈，所以关键控件自己画：

| 控件 | 说明 |
|---|---|
| `Card` | 圆角白卡，可自动高度或铺满 |
| `PillButton` | primary / secondary / ghost 三种样式 |
| `SegmentedControl` | 分段切换，选中片缓动滑动 |
| `Switch` | 开关，轨道颜色与圆钮同步过渡 |
| `Slider` | 细轨道 + 圆钮，按下放大，1:1 跟随 |
| `ResultList` | Canvas 虚拟列表，只画可见行，几千条不卡 |

圆角用 Pillow 以 4 倍尺寸渲染再 LANCZOS 下采样，得到抗锯齿边缘；
没装 Pillow 时自动退回多边形绘制（边缘略硬但能跑）。

---

## 四、顺带修掉的问题

| 问题 | 现象 | 处理 |
|---|---|---|
| `time` 未导入 | 点「暂停」直接崩 `NameError` | 补 `import time`，改用调度器的暂停标志 |
| `self.cfg_whitelist` 不存在 | 点「保存配置」必崩 | 移除对未创建控件的引用，改读实际存在的字段 |
| 界面「超时」设置不生效 | 上游把每处 timeout 写死成 5/8/10/15 秒 | 在唯一请求入口 `request_with_retry` 加全局覆盖 |
| `has_audio` 判断错误 | 有视频的流一律被判为"无音频" | 改读 ffmpeg 流信息；`decode_probe` 再用 probe 结果校准 |
| 打包后 DPI 失效 | PerMonitorV2 下 Tk 自报 96dpi，125% 缩放下窗口偏小、字发虚 | Win32 三级查真实 DPI（GetDpiForWindow → GetDpiForSystem → GetDeviceCaps），同步 `tk scaling` 让字体与 Canvas 像素同源 |

> 关于超时：上游对每条源会做多轮顺序校验（探活 → 重定向 → 分片），
> 所以**单条最坏耗时约为设定值的数倍**。界面上已把默认值从 15 秒调到 8 秒。

---

## 五、测试

五个套件，189 项断言，全部通过：

```bash
cd out
python test_dpl.py          # 46 项：DPL 格式读写
python test_e2e.py          # 30 项：真实 DPL → 解析 → 检测 → 导出 → 回读
python test_ffmpeg.py       # 41 项：FFmpeg 探测 / 三种模式 / 批量调度 / 暂停停止
python test_gui_smoke.py    # 46 项：真实 Tk 建窗，三页切换、控件、导入导出、配置保存
python test_decode_live.py  # 26 项：现场用 ffmpeg 生成 HLS，验证真的解出帧
```

`test_decode_live.py` 完全离线：用 ffmpeg 合成一段 6 秒视频切成 HLS，
起本地 HTTP 服务喂给检测链路，断言解出 74 帧、识别 h264/aac/320x240。
这样验证的是"解码链路真的通"，而不是"坏地址能优雅失败"。

### 开发辅助脚本

```bash
python _capture.py            # 启动真实界面并按页截图
python _selftest_widgets.py   # 把所有自绘控件摆一屏截图
```

---

## 六、已知限制

- **单条最坏耗时**：受上游多轮校验影响，约为「超时设置 × 3~6」。对大量死链做
  全量解码检测会比较慢，建议先用 HTTP 探活粗筛。
- **深色主题**：`theme.DARK` 已定义配色，但界面尚未接入切换入口，目前固定浅色。
- **结果表不支持列排序**：原版支持按响应时间/流畅度排序，重构后暂时只保留了
  筛选与搜索。需要的话可以补。
- **无视频预览**：播放需要内嵌播放器（mpv/VLC），tkinter 原生做不到，
  当前通过右键复制地址交给外部播放器。
