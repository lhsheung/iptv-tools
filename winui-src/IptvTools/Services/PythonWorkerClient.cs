using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace IptvTools.Services;

/// <summary>
/// Python Worker 客户端：管理子进程生命周期，按 NDJSON 协议收发。
///
/// 设计要点
/// --------
/// - **stdout 只读 JSON**：逐行解析，非 JSON 行直接丢弃（不可能出现，但防御）。
/// - **stderr 单独收集**：作为诊断日志，不影响协议。
/// - **请求-响应按 id 配对**：同一个 Worker 可并发处理多个请求，
///   <c>progress</c> 等事件通过回调推送。
/// - **进程意外退出可感知**：读线程结束时触发 <see cref="ProcessExited"/>。
/// </summary>
public sealed class PythonWorkerClient : IAsyncDisposable
{
    private readonly Process _process;
    private readonly Dictionary<long, TaskCompletionSource<JsonNode?>> _pending = new();
    private readonly object _lock = new();
    private long _nextId;
    private readonly StringBuilder _stderrBuffer = new();
    private bool _disposed;

    /// <summary>收到 event 类型消息时触发（例如 test 的 progress）。</summary>
    public event Action<JsonNode>? EventReceived;

    /// <summary>收到 stderr 日志时触发。</summary>
    public event Action<string>? LogReceived;

    /// <summary>Worker 进程退出时触发。</summary>
    public event Action<int?>? ProcessExited;

    /// <summary>Worker 发来的 ready 事件里的信息。</summary>
    public JsonNode? ReadyInfo { get; private set; }

    public bool IsRunning => !_disposed && !_process.HasExited;

    private PythonWorkerClient(Process process)
    {
        _process = process;
    }

    /// <summary>
    /// 启动 Worker。
    /// </summary>
    /// <param name="pythonExe">python.exe 路径；传 null 则用内置运行时。</param>
    /// <param name="workerScript">worker.py 路径。</param>
    /// <param name="workingDirectory">工作目录（决定了相对路径解析）。</param>
    public static PythonWorkerClient Start(string pythonExe, string workerScript,
                                           string? workingDirectory = null)
    {
        var psi = new ProcessStartInfo
        {
            FileName = pythonExe,
            Arguments = $"\"{workerScript}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            StandardInputEncoding = Encoding.UTF8,
            WorkingDirectory = workingDirectory ?? Path.GetDirectoryName(workerScript) ?? ".",
        };
        // 让 Python 不缓冲输出，事件才能实时到达
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";

        var proc = Process.Start(psi)
                   ?? throw new InvalidOperationException("无法启动 Python Worker");

        var client = new PythonWorkerClient(proc);
        client.StartPump();
        return client;
    }

    private void StartPump()
    {
        // stdout：NDJSON 主通道
        _ = Task.Run(async () =>
        {
            try
            {
                string? line;
                while ((line = await _process.StandardOutput.ReadLineAsync()) != null)
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;
                    Dispatch(line);
                }
            }
            catch (Exception ex)
            {
                LogReceived?.Invoke($"[stdout 读取结束] {ex.Message}");
            }
            finally
            {
                int? code = null;
                try { if (_process.HasExited) code = _process.ExitCode; } catch { }
                FailAllPending(new InvalidOperationException("Worker 进程已退出"));
                ProcessExited?.Invoke(code);
            }
        });

        // stderr：诊断日志
        _ = Task.Run(async () =>
        {
            try
            {
                string? line;
                while ((line = await _process.StandardError.ReadLineAsync()) != null)
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;
                    lock (_lock) _stderrBuffer.AppendLine(line);
                    LogReceived?.Invoke(line);
                }
            }
            catch { /* 进程结束时正常抛错，忽略 */ }
        });
    }

    private void Dispatch(string line)
    {
        JsonNode? node;
        try
        {
            node = JsonNode.Parse(line);
        }
        catch (JsonException)
        {
            // 理论上不会发生（Worker 保证 stdout 纯 JSON）
            LogReceived?.Invoke($"[非 JSON 行已忽略] {line[..Math.Min(120, line.Length)]}");
            return;
        }
        if (node is not JsonObject obj) return;

        var type = obj["type"]?.GetValue<string>();

        if (type == "ready")
        {
            ReadyInfo = obj;
            return;
        }

        if (type == "event")
        {
            EventReceived?.Invoke(obj);
            return;
        }

        var idNode = obj["id"];
        if (idNode is null) return;

        long id;
        try { id = idNode.GetValue<long>(); }
        catch { return; }

        TaskCompletionSource<JsonNode?>? tcs;
        lock (_lock)
        {
            if (!_pending.Remove(id, out tcs)) return;
        }

        if (type == "error")
        {
            var message = obj["message"]?.GetValue<string>() ?? "未知错误";
            tcs.TrySetException(new WorkerException(message,
                obj["detail"]?.GetValue<string>()));
        }
        else
        {
            tcs.TrySetResult(obj["data"]);
        }
    }

    private void FailAllPending(Exception ex)
    {
        List<TaskCompletionSource<JsonNode?>> list;
        lock (_lock)
        {
            list = _pending.Values.ToList();
            _pending.Clear();
        }
        foreach (var t in list) t.TrySetException(ex);
    }

    /// <summary>最近一次分配给请求的 id，便于调用方把 event 与请求对应起来。</summary>
    public long LastIssuedId => Interlocked.Read(ref _nextId);

    /// <summary>发送请求并等待结果。</summary>
    public async Task<JsonNode?> RequestAsync(string cmd, object? parameters = null,
                                              CancellationToken ct = default)
    {
        if (!IsRunning) throw new InvalidOperationException("Worker 未运行");

        long id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonNode?>(
            TaskCreationOptions.RunContinuationsAsynchronously);

        lock (_lock) _pending[id] = tcs;

        var payload = new JsonObject
        {
            ["id"] = id,
            ["cmd"] = cmd,
            ["params"] = parameters is null
                ? new JsonObject()
                : JsonSerializer.SerializeToNode(parameters),
        };

        try
        {
            await _process.StandardInput.WriteLineAsync(payload.ToJsonString());
            await _process.StandardInput.FlushAsync();
        }
        catch (Exception ex)
        {
            lock (_lock) _pending.Remove(id);
            throw new WorkerException($"向 Worker 写入失败: {ex.Message}");
        }

        await using var reg = ct.Register(() =>
        {
            lock (_lock) _pending.Remove(id);
            tcs.TrySetCanceled(ct);
        });

        var result = await tcs.Task;
        return result;
    }

    /// <summary>带类型的请求。</summary>
    public async Task<T?> RequestAsync<T>(string cmd, object? parameters = null,
                                          CancellationToken ct = default)
    {
        var node = await RequestAsync(cmd, parameters, ct);
        if (node is null) return default;
        return node.Deserialize<T>(JsonOpts);
    }

    public static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true,
        NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowReadingFromString,
    };

    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        _disposed = true;

        try
        {
            if (!_process.HasExited)
            {
                try { await _process.StandardInput.WriteLineAsync("quit"); } catch { }
                try { await _process.StandardInput.FlushAsync(); } catch { }
                _process.StandardInput.Close();

                using var cts = new CancellationTokenSource(1500);
                try { await _process.WaitForExitAsync(cts.Token); }
                catch (OperationCanceledException) { try { _process.Kill(true); } catch { } }
            }
        }
        catch { }
        finally
        {
            FailAllPending(new ObjectDisposedException(nameof(PythonWorkerClient)));
            _process.Dispose();
        }
    }
}

public sealed class WorkerException : Exception
{
    public string? Detail { get; }
    public WorkerException(string message, string? detail = null) : base(message)
        => Detail = detail;
}
