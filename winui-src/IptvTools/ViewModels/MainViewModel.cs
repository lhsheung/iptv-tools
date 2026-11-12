using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using IptvTools.Models;
using IptvTools.Services;

namespace IptvTools.ViewModels;

public sealed partial class MainViewModel : ObservableObject
{
    private PythonWorkerClient? _worker;

    /// <summary>Worker 启动所在目录，用于定位 ffmpeg / ip2region。</summary>
    private string _workerDir = AppContext.BaseDirectory;

    public ObservableCollection<ChannelRow> Channels { get; } = new();

    /// <summary>当前正在跑的测试请求 id，用于把 progress 事件对回本批。</summary>
    private long? _activeTestId;

    public TestSettings Settings { get; } = new();

    // ── 界面状态 ─────────────────────────────────────────────────────────────

    [ObservableProperty] private bool _isWorkerReady;
    [ObservableProperty] private string _workerStatus = "正在启动后端…";
    [ObservableProperty] private EnvInfo? _env;

    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private bool _isTesting;
    [ObservableProperty] private string _busyText = "";

    [ObservableProperty] private double _progressValue;
    [ObservableProperty] private int _progressDone;
    [ObservableProperty] private int _progressTotal;
    [ObservableProperty] private int _validCount;

    [ObservableProperty] private string _sourceName = "";
    [ObservableProperty] private string _sourceFormat = "";

    [ObservableProperty] private string _filterText = "";
    [ObservableProperty] private string _searchText = "";

    [ObservableProperty] private string _lastMessage = "";

    // ── 日志 ─────────────────────────────────────────────────────────────────
    public ObservableCollection<string> Logs { get; } = new();

    private void Log(string line)
    {
        // 保留最近 400 行，避免长跑内存膨胀
        Logs.Add($"{DateTime.Now:HH:mm:ss}  {line}");
        while (Logs.Count > 400) Logs.RemoveAt(0);
    }

    // ── 生命周期 ─────────────────────────────────────────────────────────────

    /// <summary>
    /// 启动 Worker。pythonExe 为 null 时按约定寻找内置/系统 Python。
    /// </summary>
    public async Task InitializeAsync(string? pythonExe = null, string? workerScript = null)
    {
        try
        {
            pythonExe ??= PythonLocator.Find();
            workerScript ??= Path.Combine(_workerDir, "worker", "worker.py");

            if (pythonExe is null)
            {
                WorkerStatus = "未找到 Python 运行时";
                LastMessage = "未找到 Python。请安装 Python 3.9+，或把便携版放到 worker 目录。";
                return;
            }
            if (!File.Exists(workerScript))
            {
                WorkerStatus = "Worker 脚本缺失";
                LastMessage = $"找不到 worker.py: {workerScript}";
                return;
            }

            _workerDir = Path.GetDirectoryName(workerScript)!;
            Log($"启动 Worker: {pythonExe}");

            _worker = PythonWorkerClient.Start(pythonExe, workerScript, _workerDir);
            _worker.LogReceived += line => App.Dispatcher.TryEnqueue(() => Log(line));
            _worker.EventReceived += node => App.Dispatcher.TryEnqueue(() => OnWorkerEvent(node));
            _worker.ProcessExited += code => App.Dispatcher.TryEnqueue(() =>
            {
                IsWorkerReady = false;
                WorkerStatus = code is null ? "后端已停止" : $"后端已退出（{code}）";
                Log($"Worker 退出，code={code}");
            });

            // 等 ready（最多 8 秒）
            for (int i = 0; i < 80 && _worker.ReadyInfo is null && _worker.IsRunning; i++)
                await Task.Delay(100);

            if (_worker.ReadyInfo is null)
            {
                WorkerStatus = "后端启动失败";
                LastMessage = "Worker 未在预期时间内就绪，请查看日志。";
                return;
            }

            IsWorkerReady = true;
            WorkerStatus = $"后端就绪 · Python {_worker.ReadyInfo["python"]?.GetValue<string>()}";
            Log($"Worker ready, pid={_worker.ReadyInfo["pid"]}");

            Env = await _worker.RequestAsync<EnvInfo>("info");
            Log($"能力: ffmpeg={Env?.Ffmpeg?.FfmpegOk} ip2region={Env?.Ip2Region} dpl={Env?.Dpl}");
        }
        catch (Exception ex)
        {
            WorkerStatus = "后端启动异常";
            LastMessage = ex.Message;
            Log($"启动失败: {ex}");
        }
    }

    /// <summary>选择本地文件并解析。</summary>
    [RelayCommand]
    private async Task ImportFileAsync()
    {
        if (!IsWorkerReady || _worker is null) { LastMessage = "后端未就绪"; return; }
        var picker = new Windows.Storage.Pickers.FileOpenPicker();
        picker.FileTypeFilter.Add(".m3u");
        picker.FileTypeFilter.Add(".m3u8");
        picker.FileTypeFilter.Add(".txt");
        picker.FileTypeFilter.Add(".dpl");
        WinRT.Interop.InitializeWithWindow.Initialize(picker,
            WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindowInstance));

        var file = await picker.PickSingleFileAsync();
        if (file is null) return;

        await LoadFromPathAsync(file.Path);
    }

    /// <summary>按路径解析（供拖放、命令行参数复用）。</summary>
    public async Task LoadFromPathAsync(string path)
    {
        if (!IsWorkerReady || _worker is null) { LastMessage = "后端未就绪"; return; }
        try
        {
            IsBusy = true; BusyText = "正在解析播放列表…";
            var res = await _worker.RequestAsync<ParseResult>("parse", new { path });
            if (res is null) { LastMessage = "解析返回为空"; return; }

            Channels.Clear();
            foreach (var c in res.Channels) Channels.Add(ChannelRow.FromModel(c));
            SourceName = Path.GetFileName(path);
            SourceFormat = res.Format.ToUpperInvariant();
            FilterText = "";
            ProgressDone = ProgressTotal = ValidCount = 0;
            ProgressValue = 0;
            LastMessage = $"已载入 {res.Count} 个地址（{SourceFormat}）";
            Log($"parse ok: {res.Format} / {res.Count}");
        }
        catch (WorkerException ex)
        {
            LastMessage = $"解析失败: {ex.Message}";
            Log($"parse 失败: {ex.Message} {ex.Detail}");
        }
        catch (Exception ex)
        {
            LastMessage = $"解析异常: {ex.Message}";
        }
        finally { IsBusy = false; BusyText = ""; }
    }

    /// <summary>从 URL 加载播放列表。</summary>
    [RelayCommand]
    private async Task ImportUrlAsync(string url)
    {
        if (!IsWorkerReady || _worker is null || string.IsNullOrWhiteSpace(url)) return;
        try
        {
            IsBusy = true; BusyText = "正在下载播放列表…";
            var res = await _worker.RequestAsync<ParseResult>("parse", new { url });
            if (res is null) return;
            Channels.Clear();
            foreach (var c in res.Channels) Channels.Add(ChannelRow.FromModel(c));
            SourceName = url;
            SourceFormat = res.Format.ToUpperInvariant();
            LastMessage = $"已载入 {res.Count} 个地址（{SourceFormat}）";
        }
        catch (WorkerException ex) { LastMessage = $"下载失败: {ex.Message}"; }
        finally { IsBusy = false; BusyText = ""; }
    }

    // ── 测试 ─────────────────────────────────────────────────────────────────

    /// <summary>测试全部勾选频道。</summary>
    [RelayCommand]
    private Task TestAllAsync() => RunTestAsync(Channels.Where(c => c.IsSelected).ToList());

    /// <summary>只测当前筛选可见且勾选的频道。</summary>
    [RelayCommand]
    private Task TestVisibleAsync() => RunTestAsync(VisibleRows.Where(c => c.IsSelected).ToList());

    /// <summary>重测所有失败项。</summary>
    [RelayCommand]
    private Task TestFailedAsync() =>
        RunTestAsync(Channels.Where(c => c.State == RowState.Invalid).ToList());

    /// <summary>单行重测。</summary>
    [RelayCommand]
    private Task TestOneAsync(ChannelRow? row) =>
        row is null ? Task.CompletedTask : RunTestAsync(new List<ChannelRow> { row });

    private async Task RunTestAsync(List<ChannelRow> rows)
    {
        if (_worker is null || !IsWorkerReady) { LastMessage = "后端未就绪"; return; }
        if (rows.Count == 0) { LastMessage = "没有可测试的频道"; return; }
        if (IsTesting) { LastMessage = "已有测试在进行中"; return; }

        Settings.Clamp();

        if (Settings.Mode is "ffmpeg" or "both" && Env?.Ffmpeg?.FfmpegOk != true)
        {
            LastMessage = "当前模式需要 FFmpeg，但未检测到可用的 ffmpeg.exe";
            return;
        }

        IsTesting = true;
        IsBusy = true;
        BusyText = $"正在测试 {rows.Count} 个地址…";
        ProgressDone = 0;
        ProgressTotal = rows.Count;
        ProgressValue = 0;
        ValidCount = 0;

        foreach (var r in rows) { r.State = RowState.Testing; r.Error = ""; }

        var sw = Stopwatch.StartNew();
        try
        {
            var payload = new
            {
                channels = rows.Select(r => new { name = r.Name, url = r.Url, group = r.Group, logo = r.Logo }),
                config = Settings.ToWire(),
                mode = Settings.Mode,
                decode_seconds = Settings.DecodeSeconds,
            };

            // 先登记请求 id，progress 事件才能匹配
            _activeTestId = null;
            var task = _worker.RequestAsync<TestSummary>("test", payload);
            _activeTestId = _worker.LastIssuedId;

            var summary = await task;
            sw.Stop();

            if (summary is not null)
            {
                // 兜底：把最终结果再刷一遍（progress 可能丢帧）
                ApplyFinalResults(rows, summary);
                ValidCount = summary.Valid;
                LastMessage = summary.Cancelled
                    ? $"已取消：完成 {summary.Done}/{summary.Total}，可用 {summary.Valid}"
                    : $"测试完成：{summary.Total} 个地址，可用 {summary.Valid}，耗时 {summary.Elapsed:0.0}s";
                Log($"test done: total={summary.Total} valid={summary.Valid} elapsed={summary.Elapsed}");
            }
        }
        catch (WorkerException ex)
        {
            LastMessage = $"测试失败: {ex.Message}";
            Log($"test 失败: {ex.Message}");
        }
        catch (Exception ex)
        {
            LastMessage = $"测试异常: {ex.Message}";
            Log($"test 异常: {ex}");
        }
        finally
        {
            _activeTestId = null;
            IsTesting = false;
            IsBusy = false;
            BusyText = "";
        }
    }

    private void ApplyFinalResults(List<ChannelRow> rows, TestSummary summary)
    {
        // 以 url 为键回填（同一 url 多行时全部命中）
        var byUrl = new Dictionary<string, TestItem>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in summary.Results)
            byUrl[item.Url] = item;

        foreach (var r in rows)
        {
            if (byUrl.TryGetValue(r.Url, out var item)) r.ApplyResult(item);
            else if (r.State == RowState.Testing) r.State = RowState.Pending;
        }
        ProgressDone = summary.Done;
        ProgressValue = summary.Total == 0 ? 0 : (double)summary.Done / summary.Total * 100;
    }

    [RelayCommand]
    private async Task CancelTestAsync()
    {
        if (_worker is null || !IsTesting) return;
        try { await _worker.RequestAsync("cancel"); LastMessage = "已请求取消"; }
        catch (Exception ex) { Log($"cancel 失败: {ex.Message}"); }
    }

    private void OnWorkerEvent(JsonNode node)
    {
        var name = node["event"]?.GetValue<string>();
        if (name != "progress") return;

        var idNode = node["id"];
        if (idNode is null) return;
        long id;
        try { id = idNode.GetValue<long>(); } catch { return; }
        if (_activeTestId is null || id != _activeTestId) return;

        var done = node["done"]?.GetValue<int>() ?? 0;
        var total = node["total"]?.GetValue<int>() ?? 0;
        var valid = node["valid"]?.GetValue<int>();

        ProgressDone = done;
        ProgressTotal = total;
        ProgressValue = total == 0 ? 0 : (double)done / total * 100;
        if (valid is not null) ValidCount = valid.Value;

        var itemNode = node["item"];
        if (itemNode is null) return;
        TestItem? item = null;
        try { item = itemNode.Deserialize<TestItem>(PythonWorkerClient.JsonOpts); }
        catch { }
        if (item is null) return;

        // ffmpeg_check 的 item 结构不同（无 name/url 之外的字段），此处按 url 匹配
        foreach (var r in Channels)
        {
            if (!string.Equals(r.Url, item.Url, StringComparison.OrdinalIgnoreCase)) continue;
            r.ApplyResult(item);
        }
    }

    // ── FFmpeg 单点检测 ──────────────────────────────────────────────────────

    [RelayCommand]
    private async Task DecodeCheckAsync(ChannelRow? row)
    {
        if (_worker is null || !IsWorkerReady || row is null) return;
        if (Env?.Ffmpeg?.FfmpegOk != true) { LastMessage = "未检测到 FFmpeg"; return; }
        try
        {
            row.DecodeSummary = "解码中…";
            var res = await _worker.RequestAsync<JsonObject>("ffmpeg_check",
                new { urls = new[] { row.Url }, decode_seconds = Settings.DecodeSeconds });
            var items = res?["items"]?.AsArray();
            if (items is null || items.Count == 0) { row.DecodeSummary = "无结果"; return; }
            var dp = items[0]!.Deserialize<DecodeProbeResult>(PythonWorkerClient.JsonOpts);
            if (dp is null) return;
            row.DecodeOk = dp.Ok;
            row.DecodeSummary = dp.Summary ?? (dp.Ok ? "可解码" : "无法解码");
            var p = dp.Probe;
            if (p is not null && p.Ok)
            {
                if (!string.IsNullOrEmpty(p.VideoCodec)) row.Codec = p.VideoCodec;
                if (p.BitrateKbps is > 0) row.BitrateKbps = p.BitrateKbps;
            }
        }
        catch (WorkerException ex) { row.DecodeSummary = $"失败: {ex.Message}"; }
    }

    // ── 导出 ─────────────────────────────────────────────────────────────────

    [RelayCommand]
    private async Task ExportAsync(string format)
    {
        if (_worker is null || !IsWorkerReady) { LastMessage = "后端未就绪"; return; }
        var rows = Channels.Where(c => c.IsSelected).ToList();
        if (rows.Count == 0) { LastMessage = "没有勾选的频道"; return; }

        bool isDpl = string.Equals(format, "dpl", StringComparison.OrdinalIgnoreCase);

        var picker = new Windows.Storage.Pickers.FileSavePicker();
        picker.FileTypeChoices.Add(isDpl ? "PotPlayer 播放列表" : "M3U 播放列表",
            new List<string> { isDpl ? ".dpl" : ".m3u" });
        picker.SuggestedFileName = isDpl ? "playlist" : "playlist";
        WinRT.Interop.InitializeWithWindow.Initialize(picker,
            WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindowInstance));

        var file = await picker.PickSaveFileAsync();
        if (file is null) return;

        try
        {
            IsBusy = true; BusyText = "正在导出…";
            var res = await _worker.RequestAsync<JsonObject>("export", new
            {
                format = isDpl ? "dpl" : "m3u",
                path = file.Path,
                channels = rows.Select(r => r.ToWire()).ToArray(),
                playname = Path.GetFileNameWithoutExtension(file.Path),
            });
            var entries = res?["entries"]?.GetValue<int>() ?? rows.Count;
            LastMessage = $"已导出 {entries} 个条目到 {file.Name}";
            Log($"export ok: {file.Path} entries={entries}");
        }
        catch (WorkerException ex)
        {
            LastMessage = $"导出失败: {ex.Message}";
            Log($"export 失败: {ex.Message}");
        }
        finally { IsBusy = false; BusyText = ""; }
    }

    [RelayCommand] private Task ExportM3uAsync() => ExportAsync("m3u");
    [RelayCommand] private Task ExportDplAsync() => ExportAsync("dpl");

    /// <summary>只导出可用频道。</summary>
    [RelayCommand]
    private async Task ExportValidOnlyAsync(string format)
    {
        foreach (var c in Channels) c.IsSelected = c.State == RowState.Valid;
        await ExportAsync(format);
    }

    // ── 选择与筛选 ───────────────────────────────────────────────────────────

    [RelayCommand]
    private void SelectAll() { foreach (var c in VisibleRows) c.IsSelected = true; }

    [RelayCommand]
    private void SelectNone() { foreach (var c in Channels) c.IsSelected = false; }

    [RelayCommand]
    private void SelectValid() { foreach (var c in Channels) c.IsSelected = c.State == RowState.Valid; }

    [RelayCommand]
    private void ClearAll()
    {
        Channels.Clear();
        SourceName = "";
        SourceFormat = "";
        ProgressDone = ProgressTotal = ValidCount = 0;
        ProgressValue = 0;
        LastMessage = "";
    }

    /// <summary>按搜索/筛选条件得到的行集合。</summary>
    public IEnumerable<ChannelRow> VisibleRows
    {
        get
        {
            var q = Channels.AsEnumerable();
            switch (FilterText)
            {
                case "valid": q = q.Where(c => c.State == RowState.Valid); break;
                case "invalid": q = q.Where(c => c.State == RowState.Invalid); break;
                case "untested": q = q.Where(c => c.State == RowState.Pending); break;
            }
            if (!string.IsNullOrWhiteSpace(SearchText))
            {
                var s = SearchText.Trim();
                q = q.Where(c =>
                    c.Name.Contains(s, StringComparison.OrdinalIgnoreCase) ||
                    c.Group.Contains(s, StringComparison.OrdinalIgnoreCase) ||
                    c.Url.Contains(s, StringComparison.OrdinalIgnoreCase));
            }
            return q;
        }
    }

    partial void OnFilterTextChanged(string value) => OnPropertyChanged(nameof(VisibleRows));
    partial void OnSearchTextChanged(string value) => OnPropertyChanged(nameof(VisibleRows));

    public async ValueTask ShutdownAsync()
    {
        if (_worker is not null) await _worker.DisposeAsync();
        _worker = null;
    }
}
