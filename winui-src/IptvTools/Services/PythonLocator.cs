using System.Diagnostics;

namespace IptvTools.Services;

/// <summary>
/// 定位可用的 Python 运行时。
///
/// 查找顺序（先近后远，保证"便携版优先"）：
/// 1. 程序目录下 <c>python\python.exe</c>（随包分发的嵌入式运行时）
/// 2. 程序目录下 <c>worker\python.exe</c>
/// 3. 环境变量 <c>IPTVTOOLS_PYTHON</c>
/// 4. PATH 上的 <c>python.exe</c>
/// 5. 常见安装位置兜底
/// </summary>
public static class PythonLocator
{
    private static string? _cached;

    public static string? Find(bool refresh = false)
    {
        if (!refresh && _cached is not null) return _cached;

        var baseDir = AppContext.BaseDirectory;

        foreach (var rel in new[] { @"python\python.exe", @"worker\python.exe" })
        {
            var p = Path.Combine(baseDir, rel);
            if (File.Exists(p)) return _cached = p;
        }

        var env = Environment.GetEnvironmentVariable("IPTVTOOLS_PYTHON");
        if (!string.IsNullOrWhiteSpace(env) && File.Exists(env)) return _cached = env;

        // PATH 查找
        var fromPath = Which("python.exe");
        if (fromPath is not null) return _cached = fromPath;

        // 常见位置
        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                         @"Programs\Python"),
            @"C:\Python313", @"C:\Python312", @"C:\Python311", @"C:\Python310",
        };
        foreach (var root in candidates)
        {
            if (!Directory.Exists(root)) continue;
            try
            {
                foreach (var dir in Directory.EnumerateDirectories(root, "Python3*")
                                              .OrderByDescending(d => d))
                {
                    var exe = Path.Combine(dir, "python.exe");
                    if (File.Exists(exe)) return _cached = exe;
                }
            }
            catch { }
            var direct = Path.Combine(root, "python.exe");
            if (File.Exists(direct)) return _cached = direct;
        }

        return null;
    }

    private static string? Which(string exe)
    {
        var pathVar = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var dir in pathVar.Split(Path.PathSeparator,
                                          StringSplitOptions.RemoveEmptyEntries))
        {
            try
            {
                var full = Path.Combine(dir.Trim('"'), exe);
                if (File.Exists(full)) return full;
            }
            catch { }
        }
        return null;
    }

    /// <summary>检查 Python 能否运行且版本足够。</summary>
    public static async Task<(bool ok, string version, string? error)> CheckAsync(string exe)
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = "-c \"import sys;print('%d.%d.%d'%sys.version_info[:3])\"",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var p = Process.Start(psi)!;
            var outp = await p.StandardOutput.ReadToEndAsync();
            var errp = await p.StandardError.ReadToEndAsync();
            await p.WaitForExitAsync();
            var v = outp.Trim();
            if (p.ExitCode == 0 && v.Length > 0) return (true, v, null);
            return (false, "", errp.Trim());
        }
        catch (Exception ex)
        {
            return (false, "", ex.Message);
        }
    }
}
