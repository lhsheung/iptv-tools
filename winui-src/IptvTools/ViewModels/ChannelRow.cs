using CommunityToolkit.Mvvm.ComponentModel;

namespace IptvTools.ViewModels;

/// <summary>单个频道在界面上的行状态。</summary>
public enum RowState
{
    Pending,
    Testing,
    Valid,
    Invalid,
}

/// <summary>
/// 频道行视图模型：一个 URL 一行，承载原始信息与测试结果。
/// 同一频道多个镜像就是多行，与 DPL 的存储语义一致。
/// </summary>
public sealed partial class ChannelRow : ObservableObject
{
    [ObservableProperty] private string _name = "";
    [ObservableProperty] private string _url = "";
    [ObservableProperty] private string _group = "";
    [ObservableProperty] private string _logo = "";

    /// <summary>来源 DPL 的元数据，导出时回写。</summary>
    public bool DplPlayed { get; init; }
    public bool DplInvalid { get; init; }
    public string? DplDuration { get; init; }

    [ObservableProperty] private RowState _state = RowState.Pending;

    [ObservableProperty] private string _responseMs = "";
    [ObservableProperty] private string _location = "";
    [ObservableProperty] private string _codec = "";
    [ObservableProperty] private int? _fluencyScore;
    [ObservableProperty] private string _fluencyLevel = "";
    [ObservableProperty] private double? _bitrateKbps;
    [ObservableProperty] private string _finalUrl = "";
    [ObservableProperty] private string _error = "";

    [ObservableProperty] private bool? _decodeOk;
    [ObservableProperty] private string _decodeSummary = "";

    /// <summary>是否被勾选（只有勾选的参与批量测试/导出）。</summary>
    [ObservableProperty] private bool _isSelected = true;

    public string StateGlyph => State switch
    {
        RowState.Valid => "\uE73E",      // CheckMark
        RowState.Invalid => "\uE711",    // Cancel
        RowState.Testing => "\uE895",    // Sync（旋转由 XAML 控制）
        _ => "\uE7BA",                   // Warning / 未测
    };

    public string StateText => State switch
    {
        RowState.Valid => "可用",
        RowState.Invalid => "不可用",
        RowState.Testing => "测试中",
        _ => "未测试",
    };

    public string MetaLine
    {
        get
        {
            var parts = new List<string>(4);
            if (!string.IsNullOrEmpty(ResponseMs)) parts.Add(ResponseMs);
            if (!string.IsNullOrEmpty(Location) && Location != "未测试") parts.Add(Location);
            if (!string.IsNullOrEmpty(Codec)) parts.Add(Codec);
            if (FluencyScore is not null) parts.Add($"流畅度 {FluencyScore}");
            if (BitrateKbps is > 0) parts.Add($"{BitrateKbps:0} kbps");
            return parts.Count == 0 ? "—" : string.Join("  ·  ", parts);
        }
    }

    partial void OnStateChanged(RowState value)
    {
        OnPropertyChanged(nameof(StateGlyph));
        OnPropertyChanged(nameof(StateText));
    }

    partial void OnResponseMsChanged(string value) => OnPropertyChanged(nameof(MetaLine));
    partial void OnLocationChanged(string value) => OnPropertyChanged(nameof(MetaLine));
    partial void OnCodecChanged(string value) => OnPropertyChanged(nameof(MetaLine));
    partial void OnFluencyScoreChanged(int? value) => OnPropertyChanged(nameof(MetaLine));
    partial void OnBitrateKbpsChanged(double? value) => OnPropertyChanged(nameof(MetaLine));

    /// <summary>从 Worker 的扁平 channel JSON 构造。</summary>
    public static ChannelRow FromModel(Models.Channel c) => new()
    {
        Name = c.Name,
        Url = c.Url,
        Group = c.Group,
        Logo = c.Logo,
        DplPlayed = c.Played,
        DplInvalid = c.Invalid,
        DplDuration = c.Duration,
        // 来自 DPL 的 invalid 条目默认不勾选，避免用户误测
        IsSelected = !c.Invalid,
    };

    /// <summary>把测试结果灌回本行。</summary>
    public void ApplyResult(Models.TestItem r)
    {
        ResponseMs = r.ResponseMs ?? "";
        Location = r.Location ?? "";
        Codec = r.Codec ?? "";
        FluencyScore = r.FluencyScore;
        FluencyLevel = r.FluencyLevel ?? "";
        BitrateKbps = r.BitrateKbps;
        FinalUrl = r.FinalUrl ?? "";
        Error = r.Error ?? "";
        DecodeOk = r.DecodeOk;
        DecodeSummary = r.Summary ?? "";
        State = r.Ok ? RowState.Valid : RowState.Invalid;
    }

    public void ResetResult()
    {
        ResponseMs = "";
        Location = "";
        Codec = "";
        FluencyScore = null;
        FluencyLevel = "";
        BitrateKbps = null;
        Error = "";
        DecodeOk = null;
        DecodeSummary = "";
        State = RowState.Pending;
    }

    /// <summary>导出给 Worker 的精简结构。</summary>
    public object ToWire() => new
    {
        name = Name,
        url = Url,
        group = Group,
        logo = Logo,
        played = DplPlayed,
        invalid = DplInvalid,
        duration = DplDuration,
        final_url = FinalUrl,
        fluency_score = FluencyScore,
        response_ms = ResponseMs,
    };
}
