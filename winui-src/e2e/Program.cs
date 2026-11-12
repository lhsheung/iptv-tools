// 端到端联调：在真实输出目录里拉起 Python Worker，走完整 NDJSON 协议。
// 用法：dotnet run --project e2e -- <输出目录>
// 这不是单元测试框架，是"能不能跑通"的冒烟验证，退出码非 0 即失败。

using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

string? workerDir = args.Length > 0 ? args[0] : null;
if (workerDir is null)
{
    Console.Error.WriteLine("用法: e2e <worker 所在目录>");
    return 2;
}

var workerPy = Path.Combine(workerDir, "worker.py");
if (!File.Exists(workerPy))
{
    Console.Error.WriteLine($"找不到 worker.py: {workerPy}");
    return 2;
}

// 与本机实际 Python 保持一致
string python = Environment.GetEnvironmentVariable("IPTVTOOLS_PYTHON")
                ?? @"D:\Software\Tool\python\python.exe";
if (!File.Exists(python))
{
    Console.Error.WriteLine($"找不到 python: {python}");
    return 2;
}

int failed = 0;
void Check(string name, bool ok, string? detail = null)
{
    Console.WriteLine($"{(ok ? "PASS" : "FAIL")}  {name}{(detail is null ? "" : "  — " + detail)}");
    if (!ok) failed++;
}

var psi = new ProcessStartInfo
{
    FileName = python,
    Arguments = $"\"{workerPy}\"",
    UseShellExecute = false,
    CreateNoWindow = true,
    RedirectStandardInput = true,
    RedirectStandardOutput = true,
    RedirectStandardError = true,
    StandardOutputEncoding = Encoding.UTF8,
    StandardErrorEncoding = Encoding.UTF8,
    StandardInputEncoding = Encoding.UTF8,
    WorkingDirectory = workerDir,
};
psi.Environment["PYTHONUNBUFFERED"] = "1";
psi.Environment["PYTHONIOENCODING"] = "utf-8";

void Trace(string msg) =>
    Console.WriteLine($"[trace {DateTime.Now:HH:mm:ss.fff}] {msg}");

Trace($"python={python}");
Trace($"worker={workerPy}");

using var proc = Process.Start(psi)!;
Trace($"pid={proc.Id}");

var pending = new Dictionary<long, TaskCompletionSource<JsonNode?>>();
var events = new List<JsonNode>();
var stderrLines = new List<string>();
var nonJson = new List<string>();
// 关键：RunContinuationsAsynchronously，否则 await 的后续代码会在 stdout 读取线程上
// 同步执行，一旦后续逻辑阻塞，就会把读取线程一起堵死（表现为 ping 永远不返回）。
var readyTcs = new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
long nextId = 0;
var gate = new object();

// stdout：只允许 JSON
var stdoutTask = Task.Run(async () =>
{
    string? line;
    while ((line = await proc.StandardOutput.ReadLineAsync()) is not null)
    {
        if (string.IsNullOrWhiteSpace(line)) continue;
        JsonNode? node;
        try { node = JsonNode.Parse(line); }
        catch (JsonException)
        {
            lock (gate) nonJson.Add(line);
            continue;
        }
        if (node is not JsonObject obj) continue;

        var type = obj["type"]?.GetValue<string>();
        if (type == "ready") { readyTcs.TrySetResult(obj); continue; }
        if (type == "event")
        {
            lock (gate) events.Add(obj);
            continue;
        }
        if (obj["id"] is null) continue;
        long id = obj["id"]!.GetValue<long>();

        TaskCompletionSource<JsonNode?>? tcs;
        lock (gate) { if (!pending.Remove(id, out tcs)) continue; }
        if (type == "error")
            tcs.TrySetException(new Exception(obj["message"]?.GetValue<string>() ?? "err"));
        else
            tcs.TrySetResult(obj["data"]);
    }
});

// stderr：诊断日志
var stderrTask = Task.Run(async () =>
{
    string? line;
    while ((line = await proc.StandardError.ReadLineAsync()) is not null)
        lock (gate) stderrLines.Add(line);
});

async Task<JsonNode?> RequestAsync(string cmd, object? p = null, int timeoutSec = 120)
{
    long id = Interlocked.Increment(ref nextId);
    var tcs = new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
    lock (gate) pending[id] = tcs;

    var payload = new JsonObject
    {
        ["id"] = id,
        ["cmd"] = cmd,
        ["params"] = p is null ? new JsonObject() : JsonSerializer.SerializeToNode(p),
    };
    await proc.StandardInput.WriteLineAsync(payload.ToJsonString());
    await proc.StandardInput.FlushAsync();

    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(timeoutSec));
    using var reg = cts.Token.Register(() =>
    {
        lock (gate) pending.Remove(id);
        tcs.TrySetCanceled();
    });
    return await tcs.Task;
}

try
{
    // ── 1. ready ──────────────────────────────────────────────────────────
    var readyTask = await Task.WhenAny(readyTcs.Task, Task.Delay(15000));
    Check("worker 输出 ready 事件", readyTcs.Task.IsCompleted,
          readyTask == readyTcs.Task ? null : "15s 未就绪");
    if (!readyTcs.Task.IsCompleted) return 1;

    // ── 2. stdout 纯净性 ─────────────────────────────────────────────────
    Check("stdout 无非 JSON 行", nonJson.Count == 0,
          nonJson.Count == 0 ? null : string.Join(" | ", nonJson.Take(3)));

    Trace("准备发送 ping");

    // ── 3. ping ──────────────────────────────────────────────────────────
    var pong = await RequestAsync("ping");
    Check("ping", pong?["pong"]?.GetValue<bool>() == true);

    // ── 4. info：能力探测 ────────────────────────────────────────────────
    var info = await RequestAsync("info", null, 60);
    Check("info 返回结果", info is not null);
    if (info is not null)
    {
        var py = info["python"]?.GetValue<string>() ?? "";
        var testLogic = info["test_logic"]?.GetValue<bool>() == true;
        var dpl = info["dpl"]?.GetValue<bool>() == true;
        var ip2 = info["ip2region"]?.GetValue<bool>() == true;
        var ffmpeg = info["ffmpeg"];
        var ffOk = ffmpeg?["ffmpeg_ok"]?.GetValue<bool>() == true;
        var ffVer = ffmpeg?["version"]?.GetValue<string>() ?? "";

        Check("Python 版本可见", py.Length > 0, py);
        Check("test_logic 可导入", testLogic);
        Check("dpl_format 可导入", dpl);
        Check("ip2region 可用", ip2, ip2 ? null : info["ip2region_error"]?.GetValue<string>());
        Check("FFmpeg 可用", ffOk, ffVer);
    }

    // ── 5. parse：M3U 内容 ───────────────────────────────────────────────
    const string m3u = """
        #EXTM3U
        #EXTINF:-1 group-title="央视",CCTV-1 综合
        http://example.com/cctv1.m3u8
        #EXTINF:-1 group-title="央视",CCTV-2 财经
        http://example.com/cctv2.m3u8
        #EXTINF:-1 group-title="卫视",湖南卫视
        http://example.com/hunan.m3u8
        """;
    var parsed = await RequestAsync("parse", new { content = m3u });
    var pCount = parsed?["count"]?.GetValue<int>() ?? 0;
    Check("parse M3U 得到 3 条", pCount == 3, $"count={pCount}");
    Check("parse 返回 format=m3u", parsed?["format"]?.GetValue<string>() == "m3u");
    var firstCh = parsed?["channels"]?.AsArray()?.FirstOrDefault();
    Check("频道 name 正确", firstCh?["name"]?.GetValue<string>() == "CCTV-1 综合",
          firstCh?["name"]?.GetValue<string>());
    Check("频道 group 正确", firstCh?["group"]?.GetValue<string>() == "央视",
          firstCh?["group"]?.GetValue<string>());

    // ── 6. parse：DPL 内容（含多镜像）────────────────────────────────────
    var dplText = "DAUMPLAYLIST\r\n" +
                  "playname=测试\r\n" +
                  "topindex=0\r\n" +
                  "saveplaypos=0\r\n" +
                  "1*file*http://example.com/a1.m3u8\r\n" +
                  "1*title*频道A\r\n" +
                  "2*file*http://example.com/a2.m3u8\r\n" +
                  "2*title*频道A\r\n" +
                  "3*file*http://example.com/b1.m3u8\r\n" +
                  "3*title*频道B\r\n";
    var dplParsed = await RequestAsync("parse", new { content = dplText });
    var dCount = dplParsed?["count"]?.GetValue<int>() ?? 0;
    Check("parse DPL 识别格式", dplParsed?["format"]?.GetValue<string>() == "dpl");
    Check("DPL 同频道多镜像保留为多条", dCount == 3, $"count={dCount}");

    // ── 7. export：M3U / DPL ─────────────────────────────────────────────
    var tmp = Path.Combine(Path.GetTempPath(), "iptv_e2e_" + Guid.NewGuid().ToString("N")[..8]);
    Directory.CreateDirectory(tmp);

    var outM3u = Path.Combine(tmp, "out.m3u");
    var m3uRes = await RequestAsync("export", new
    {
        format = "m3u",
        path = outM3u,
        channels = new object[]
        {
            new { name = "频道A", url = "http://example.com/a1.m3u8", group = "测试组", fluency_score = 42 },
            new { name = "频道B", url = "http://example.com/b1.m3u8", group = "测试组" },
        },
    });
    Check("export M3U 成功", File.Exists(outM3u) && new FileInfo(outM3u).Length > 0);
    Check("export M3U entries=2", m3uRes?["entries"]?.GetValue<int>() == 2);

    var outDpl = Path.Combine(tmp, "out.dpl");
    var dplRes = await RequestAsync("export", new
    {
        format = "dpl",
        path = outDpl,
        playname = "导出测试",
        channels = new object[]
        {
            new { name = "频道A", url = "http://example.com/a1.m3u8", group = "测试组" },
            new { name = "频道A", url = "http://example.com/a2.m3u8", group = "测试组" },
        },
    });
    Check("export DPL 成功", File.Exists(outDpl) && new FileInfo(outDpl).Length > 0);
    Check("export DPL entries=2", dplRes?["entries"]?.GetValue<int>() == 2);

    // DPL 必须能被自己读回来（往返一致性）
    if (File.Exists(outDpl))
    {
        var back = await RequestAsync("parse", new { path = outDpl });
        Check("DPL 往返解析一致",
              back?["format"]?.GetValue<string>() == "dpl" &&
              back?["count"]?.GetValue<int>() == 2,
              $"count={back?["count"]?.GetValue<int>()}");
    }

    // ── 8. test：本地不可达地址，应全部判为不可用且不崩 ──────────────────
    var testRes = await RequestAsync("test", new
    {
        channels = new object[]
        {
            new { name = "坏地址1", url = "http://127.0.0.1:1/none.m3u8", group = "t" },
            new { name = "坏地址2", url = "http://127.0.0.1:2/none.m3u8", group = "t" },
        },
        config = new
        {
            max_workers = 2,
            timeout = 3,
            check_fluency = false,
            check_live = false,
            strict_validation = true,
        },
        mode = "http",
    }, 120);
    var tTotal = testRes?["total"]?.GetValue<int>() ?? 0;
    var tValid = testRes?["valid"]?.GetValue<int>() ?? -1;
    Check("test 处理了 2 个地址", tTotal == 2, $"total={tTotal}");
    Check("test 正确判定全不可用", tValid == 0, $"valid={tValid}");

    // ── 9. event 通道确实推送了 progress ─────────────────────────────────
    await Task.Delay(300);
    List<JsonNode> evts;
    lock (gate) evts = events.ToList();
    var progress = evts.Where(e => e["event"]?.GetValue<string>() == "progress").ToList();
    Check("test 推送 progress 事件", progress.Count >= 2, $"count={progress.Count}");
    Check("progress 带 done/total",
          progress.Count > 0 &&
          progress[^1]["done"] is not null &&
          progress[^1]["total"] is not null);

    // ── 10. ffmpeg_check：无效地址应优雅失败 ─────────────────────────────
    JsonNode? ffRes = null;
    Exception? ffErr = null;
    try
    {
        ffRes = await RequestAsync("ffmpeg_check", new
        {
            urls = new[] { "http://127.0.0.1:1/none.m3u8" },
            decode_seconds = 2,
        }, 120);
    }
    catch (Exception ex) { ffErr = ex; }
    Check("ffmpeg_check 有返回（成功或报错均可）", ffRes is not null || ffErr is not null);
    if (ffRes is not null)
    {
        var items = ffRes["items"]?.AsArray();
        Check("ffmpeg_check 返回 1 条", items?.Count == 1, $"count={items?.Count}");
        var okFlag = items?[0]?["ok"]?.GetValue<bool>() == true;
        Check("无效地址判定为不可解码", !okFlag);
    }

    // ── 11. 未知命令应报错而不是崩 ───────────────────────────────────────
    Exception? unknownErr = null;
    try { await RequestAsync("no_such_cmd"); }
    catch (Exception ex) { unknownErr = ex; }
    Check("未知命令返回 error", unknownErr is not null, unknownErr?.Message);

    // ── 12. worker 仍存活 ────────────────────────────────────────────────
    Check("worker 全程未崩溃", !proc.HasExited);

    // ── 13. stderr 有诊断输出但未污染 stdout ─────────────────────────────
    List<string> errs;
    lock (gate) errs = stderrLines.ToList();
    Check("stderr 有日志输出（说明通道分离生效）", errs.Count > 0, $"{errs.Count} 行");
    Check("stderr 里没有 JSON 协议行",
          !errs.Any(l => l.TrimStart().StartsWith("{\"id\":")));

    // 清理
    try { Directory.Delete(tmp, true); } catch { }
}
catch (Exception ex)
{
    Console.Error.WriteLine($"EXCEPTION: {ex}");
    failed++;
}
finally
{
    try
    {
        if (!proc.HasExited)
        {
            await proc.StandardInput.WriteLineAsync("quit");
            await proc.StandardInput.FlushAsync();
            proc.StandardInput.Close();
            using var cts = new CancellationTokenSource(3000);
            try { await proc.WaitForExitAsync(cts.Token); } catch { proc.Kill(true); }
        }
    }
    catch { }
}

Console.WriteLine();
Console.WriteLine(failed == 0 ? "全部通过" : $"{failed} 项失败");
return failed == 0 ? 0 : 1;
