using System.Text.Json.Serialization;

namespace IptvTools.Models;

/// <summary>一个频道条目（与 Python Worker 的扁平 JSON 对齐）。</summary>
public sealed class Channel
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("url")] public string Url { get; set; } = "";
    [JsonPropertyName("group")] public string Group { get; set; } = "";
    [JsonPropertyName("logo")] public string Logo { get; set; } = "";
    [JsonPropertyName("played")] public bool Played { get; set; }
    [JsonPropertyName("invalid")] public bool Invalid { get; set; }
    [JsonPropertyName("duration")] public string? Duration { get; set; }
}

/// <summary>解析结果。</summary>
public sealed class ParseResult
{
    [JsonPropertyName("format")] public string Format { get; set; } = "";
    [JsonPropertyName("count")] public int Count { get; set; }
    [JsonPropertyName("channels")] public List<Channel> Channels { get; set; } = new();
}

/// <summary>单频道测试结果。</summary>
public sealed class TestItem
{
    [JsonPropertyName("url")] public string Url { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("group")] public string Group { get; set; } = "";
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("response_ms")] public string? ResponseMs { get; set; }
    [JsonPropertyName("location")] public string? Location { get; set; }
    [JsonPropertyName("codec")] public string? Codec { get; set; }
    [JsonPropertyName("fluency_score")] public int? FluencyScore { get; set; }
    [JsonPropertyName("fluency_level")] public string? FluencyLevel { get; set; }
    [JsonPropertyName("bitrate_kbps")] public double? BitrateKbps { get; set; }
    [JsonPropertyName("final_url")] public string? FinalUrl { get; set; }
    [JsonPropertyName("via")] public string? Via { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
    [JsonPropertyName("decode_ok")] public bool? DecodeOk { get; set; }
    [JsonPropertyName("summary")] public string? Summary { get; set; }
}

/// <summary>批量测试汇总。</summary>
public sealed class TestSummary
{
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("done")] public int Done { get; set; }
    [JsonPropertyName("valid")] public int Valid { get; set; }
    [JsonPropertyName("elapsed")] public double Elapsed { get; set; }
    [JsonPropertyName("cancelled")] public bool Cancelled { get; set; }
    [JsonPropertyName("mode")] public string Mode { get; set; } = "";
    [JsonPropertyName("results")] public List<TestItem> Results { get; set; } = new();
}

/// <summary>FFprobe 媒体信息。</summary>
public sealed class ProbeInfo
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
    [JsonPropertyName("format")] public string? Format { get; set; }
    [JsonPropertyName("video_codec")] public string? VideoCodec { get; set; }
    [JsonPropertyName("audio_codec")] public string? AudioCodec { get; set; }
    [JsonPropertyName("width")] public int Width { get; set; }
    [JsonPropertyName("height")] public int Height { get; set; }
    [JsonPropertyName("fps")] public string? Fps { get; set; }
    [JsonPropertyName("bitrate_kbps")] public double? BitrateKbps { get; set; }
    [JsonPropertyName("duration")] public double? Duration { get; set; }

    /// <summary>形如 "1920x1080"，无尺寸时为空。</summary>
    public string Resolution => Width > 0 && Height > 0 ? $"{Width}x{Height}" : "";
}

/// <summary>FFmpeg 解码结果。</summary>
public sealed class DecodeInfo
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("error")] public string? Error { get; set; }
    [JsonPropertyName("frames")] public int Frames { get; set; }
    [JsonPropertyName("decoded_seconds")] public double DecodedSeconds { get; set; }
    [JsonPropertyName("has_video")] public bool HasVideo { get; set; }
    [JsonPropertyName("has_audio")] public bool HasAudio { get; set; }
    [JsonPropertyName("frame_timeline")] public List<int> FrameTimeline { get; set; } = new();
}

/// <summary>解码检测组合结果。</summary>
public sealed class DecodeProbeResult
{
    [JsonPropertyName("url")] public string Url { get; set; } = "";
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("summary")] public string? Summary { get; set; }
    [JsonPropertyName("probe")] public ProbeInfo? Probe { get; set; }
    [JsonPropertyName("decode")] public DecodeInfo? Decode { get; set; }
}

/// <summary>运行环境与能力。</summary>
public sealed class EnvInfo
{
    [JsonPropertyName("python")] public string? Python { get; set; }
    [JsonPropertyName("platform")] public string? Platform { get; set; }
    [JsonPropertyName("requests")] public string? Requests { get; set; }
    [JsonPropertyName("test_logic")] public bool TestLogic { get; set; }
    [JsonPropertyName("ip2region")] public bool Ip2Region { get; set; }
    [JsonPropertyName("dpl")] public bool Dpl { get; set; }
    [JsonPropertyName("ffmpeg")] public FfmpegStatus? Ffmpeg { get; set; }
}

public sealed class FfmpegStatus
{
    [JsonPropertyName("ffmpeg")] public string? Ffmpeg { get; set; }
    [JsonPropertyName("ffprobe")] public string? Ffprobe { get; set; }
    [JsonPropertyName("ffmpeg_ok")] public bool FfmpegOk { get; set; }
    [JsonPropertyName("ffprobe_ok")] public bool FfprobeOk { get; set; }
    [JsonPropertyName("version")] public string? Version { get; set; }
}
