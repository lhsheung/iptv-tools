using CommunityToolkit.Mvvm.ComponentModel;

namespace IptvTools.ViewModels;

/// <summary>测试参数（与 Python TestConfig 字段一一对应）。</summary>
public sealed partial class TestSettings : ObservableObject
{
    [ObservableProperty] private int _maxWorkers = 8;
    [ObservableProperty] private int _timeout = 15;
    [ObservableProperty] private int _minFluencyScore = 10;
    [ObservableProperty] private bool _checkFluency = true;
    [ObservableProperty] private bool _checkLive = true;
    [ObservableProperty] private bool _quickLiveCheck = false;
    [ObservableProperty] private bool _strictValidation = true;

    /// <summary>http / ffmpeg / both</summary>
    [ObservableProperty] private string _mode = "http";

    /// <summary>FFmpeg 解码时长（秒）。</summary>
    [ObservableProperty] private int _decodeSeconds = 3;

    public object ToWire() => new
    {
        max_workers = MaxWorkers,
        timeout = Timeout,
        min_fluency_score = MinFluencyScore,
        check_fluency = CheckFluency,
        check_live = CheckLive,
        quick_live_check = QuickLiveCheck,
        strict_validation = StrictValidation,
    };

    public void Clamp()
    {
        if (MaxWorkers < 1) MaxWorkers = 1;
        if (MaxWorkers > 32) MaxWorkers = 32;
        if (Timeout < 1) Timeout = 1;
        if (Timeout > 120) Timeout = 120;
        if (DecodeSeconds < 1) DecodeSeconds = 1;
        if (DecodeSeconds > 30) DecodeSeconds = 30;
        if (MinFluencyScore < 0) MinFluencyScore = 0;
        if (MinFluencyScore > 100) MinFluencyScore = 100;
    }
}
