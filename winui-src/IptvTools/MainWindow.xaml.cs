using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using IptvTools.ViewModels;
using Windows.Graphics;
using WinRT.Interop;

namespace IptvTools;

public sealed partial class MainWindow : Window
{
    public MainViewModel ViewModel { get; } = new();

    private bool _logVisible;
    private DesktopAcrylicBackdrop? _acrylic;

    public MainWindow()
    {
        InitializeComponent();
        Title = "IPTV Tools";

        ConfigureWindow();
        TryApplyBackdrop();

        ViewModel.PropertyChanged += OnViewModelPropertyChanged;
        ViewModel.Channels.CollectionChanged += (_, _) => UpdateEmptyState();

        // 首次进入即启动后端；不阻塞 UI 线程
        _ = ViewModel.InitializeAsync();

        UpdateEmptyState();
        UpdateStatus();
    }

    // ── 窗口外观 ─────────────────────────────────────────────────────────────

    private void ConfigureWindow()
    {
        var hwnd = WindowNative.GetWindowHandle(this);
        var id = Win32Interop.GetWindowIdFromWindow(hwnd);
        var appWindow = AppWindow.GetFromWindowId(id);

        // 让自绘标题栏接管，系统按钮叠加在右侧
        if (AppWindowTitleBar.IsCustomizationSupported())
        {
            var tb = appWindow.TitleBar;
            tb.ExtendsContentIntoTitleBar = true;
            tb.ButtonBackgroundColor = Colors.Transparent;
            tb.ButtonInactiveBackgroundColor = Colors.Transparent;
        }
        SetTitleBar(TitleBarArea);

        // 初始尺寸：1440x900，居中
        var area = DisplayArea.GetFromWindowId(id, DisplayAreaFallback.Primary);
        var w = Math.Min(1440, area.WorkArea.Width - 80);
        var h = Math.Min(920, area.WorkArea.Height - 80);
        appWindow.MoveAndResize(new RectInt32(
            area.WorkArea.X + (area.WorkArea.Width - w) / 2,
            area.WorkArea.Y + (area.WorkArea.Height - h) / 2,
            w, h));

        appWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "app.ico"));
    }

    private void TryApplyBackdrop()
    {
        // 亚克力在 Win10 1809+ / Win11 都可用；失败则退回纯色画布，不影响功能
        try
        {
            if (DesktopAcrylicController.IsSupported())
            {
                _acrylic = new DesktopAcrylicBackdrop();
                SystemBackdrop = _acrylic;
            }
        }
        catch
        {
            _acrylic = null;
        }
    }

    // ── 主题切换 ─────────────────────────────────────────────────────────────

    private void OnToggleTheme(object sender, RoutedEventArgs e)
    {
        if (RootGrid.RequestedTheme == ElementTheme.Dark)
        {
            RootGrid.RequestedTheme = ElementTheme.Light;
            (ThemeButton.Content as FontIcon)!.Glyph = "\uE706"; // Sun
        }
        else
        {
            RootGrid.RequestedTheme = ElementTheme.Dark;
            (ThemeButton.Content as FontIcon)!.Glyph = "\uE708"; // Moon-ish
        }
    }

    private void OnToggleLog(object sender, RoutedEventArgs e)
    {
        _logVisible = !_logVisible;
        LogDrawer.Visibility = _logVisible ? Visibility.Visible : Visibility.Collapsed;
        StatusStrip.Margin = _logVisible ? new Thickness(0, 0, 0, 168) : new Thickness(0);
        if (_logVisible && ViewModel.Logs.Count > 0)
            LogList.ScrollIntoView(ViewModel.Logs[^1]);
    }

    private void OnClearLog(object sender, RoutedEventArgs e) => ViewModel.Logs.Clear();

    // ── 侧栏动作 ─────────────────────────────────────────────────────────────

    private async void OnImportFile(object sender, RoutedEventArgs e)
        => await ViewModel.ImportFileCommand.ExecuteAsync(null);

    private async void OnImportUrl(object sender, RoutedEventArgs e)
    {
        var box = new TextBox
        {
            PlaceholderText = "https://example.com/playlist.m3u",
            Width = 420,
        };
        var dlg = new ContentDialog
        {
            Title = "从网址载入播放列表",
            Content = box,
            PrimaryButtonText = "载入",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = RootGrid.XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        var url = box.Text.Trim();
        if (url.Length == 0) return;
        await ViewModel.ImportUrlCommand.ExecuteAsync(url);
    }

    private async void OnTestAll(object sender, RoutedEventArgs e)
        => await ViewModel.TestAllCommand.ExecuteAsync(null);

    private async void OnTestVisible(object sender, RoutedEventArgs e)
        => await ViewModel.TestVisibleCommand.ExecuteAsync(null);

    private async void OnTestFailed(object sender, RoutedEventArgs e)
        => await ViewModel.TestFailedCommand.ExecuteAsync(null);

    private async void OnCancelTest(object sender, RoutedEventArgs e)
        => await ViewModel.CancelTestCommand.ExecuteAsync(null);

    private async void OnExportM3u(object sender, RoutedEventArgs e)
        => await ViewModel.ExportM3uCommand.ExecuteAsync(null);

    private async void OnExportDpl(object sender, RoutedEventArgs e)
        => await ViewModel.ExportDplCommand.ExecuteAsync(null);

    private void OnClearAll(object sender, RoutedEventArgs e) => ViewModel.ClearAllCommand.Execute(null);

    private async void OnRowTest(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: ChannelRow row })
            await ViewModel.TestOneCommand.ExecuteAsync(row);
    }

    private async void OnRowDecode(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: ChannelRow row })
            await ViewModel.DecodeCheckCommand.ExecuteAsync(row);
    }

    // ── 筛选与搜索 ───────────────────────────────────────────────────────────

    private void OnSearchChanged(AutoSuggestBox sender, AutoSuggestBoxTextChangedEventArgs args)
    {
        if (args.Reason != AutoSuggestionBoxTextChangeReason.UserInput) return;
        ViewModel.SearchText = sender.Text;
        RefreshList();
    }

    private void OnFilterClick(object sender, RoutedEventArgs e)
    {
        if (sender is not ToggleButton btn) return;
        var tag = btn.Tag as string ?? "all";

        // 单选语义：点谁谁亮
        foreach (var b in new[] { FilterAll, FilterValid, FilterInvalid, FilterUntested })
            b.IsChecked = ReferenceEquals(b, btn);

        ViewModel.FilterText = tag == "all" ? "" : tag;
        RefreshList();
    }

    private void RefreshList()
    {
        ChannelList.ItemsSource = ViewModel.VisibleRows.ToList();
        UpdateEmptyState();
        UpdateStatus();
    }

    // ── 参数绑定 ─────────────────────────────────────────────────────────────

    private void OnModeChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ModeCombo.SelectedItem is not ComboBoxItem item) return;
        var tag = item.Tag as string ?? "http";
        ViewModel.Settings.Mode = tag;

        // 解码时长只在涉及 FFmpeg 时展示
        DecodeSecondsPanel.Visibility = tag == "http" ? Visibility.Collapsed : Visibility.Visible;
        ModeHint.Text = tag switch
        {
            "ffmpeg" => "用 FFmpeg 真实解码若干秒，能解出画面才算可用，最接近实际观看。",
            "both" => "先 HTTP 探活，再用 FFmpeg 解码复核，速度与准确度折中。",
            _ => "仅检查 HTTP 响应与流特征，速度最快。",
        };
    }

    private void OnWorkersChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        var v = (int)e.NewValue;
        ViewModel.Settings.MaxWorkers = v;
        WorkersValue.Text = v.ToString();
    }

    private void OnTimeoutChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        var v = (int)e.NewValue;
        ViewModel.Settings.Timeout = v;
        TimeoutValue.Text = $"{v} 秒";
    }

    private void OnDecodeChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        var v = (int)e.NewValue;
        ViewModel.Settings.DecodeSeconds = v;
        DecodeValue.Text = $"{v} 秒";
    }

    private void OnScoreChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        var v = (int)e.NewValue;
        ViewModel.Settings.MinFluencyScore = v;
        ScoreValue.Text = v.ToString();
    }

    private void OnToggleOption(object sender, RoutedEventArgs e)
    {
        ViewModel.Settings.CheckLive = CheckLiveSwitch.IsOn;
        ViewModel.Settings.CheckFluency = CheckFluencySwitch.IsOn;
        ViewModel.Settings.StrictValidation = StrictSwitch.IsOn;
        ViewModel.Settings.QuickLiveCheck = QuickLiveSwitch.IsOn;
    }

    // ── 状态刷新 ─────────────────────────────────────────────────────────────

    private void OnViewModelPropertyChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        switch (e.PropertyName)
        {
            case nameof(MainViewModel.IsBusy):
            case nameof(MainViewModel.IsTesting):
                CancelBtn.Visibility = ViewModel.IsTesting ? Visibility.Visible : Visibility.Collapsed;
                ProgressOverlay.Visibility = ViewModel.IsTesting ? Visibility.Visible : Visibility.Collapsed;
                SetActionsEnabled(!ViewModel.IsBusy);
                break;

            case nameof(MainViewModel.ProgressValue):
                MainProgress.Value = ViewModel.ProgressValue;
                UpdateStatus();
                break;

            case nameof(MainViewModel.ValidCount):
            case nameof(MainViewModel.ProgressDone):
            case nameof(MainViewModel.ProgressTotal):
                UpdateStats();
                break;

            case nameof(MainViewModel.LastMessage):
                StatusText.Text = ViewModel.LastMessage;
                break;

            case nameof(MainViewModel.WorkerStatus):
                WorkerStatusText.Text = ViewModel.WorkerStatus;
                break;

            case nameof(MainViewModel.Env):
                UpdateEnvText();
                break;

            case nameof(MainViewModel.SourceName):
            case nameof(MainViewModel.SourceFormat):
                UpdateStatus();
                break;
        }
    }

    private void SetActionsEnabled(bool enabled)
    {
        ImportFileBtn.IsEnabled = enabled;
        ImportUrlBtn.IsEnabled = enabled;
        TestAllBtn.IsEnabled = enabled;
        TestVisibleBtn.IsEnabled = enabled;
        TestFailedBtn.IsEnabled = enabled;
        ExportM3uBtn.IsEnabled = enabled;
        ExportDplBtn.IsEnabled = enabled;
    }

    private void UpdateEnvText()
    {
        var env = ViewModel.Env;
        if (env is null) return;

        var f = env.Ffmpeg;
        FfmpegStatusText.Text = f?.FfmpegOk == true
            ? $"FFmpeg {f.Version}  ·  {(f.FfprobeOk ? "ffprobe 就绪" : "缺 ffprobe")}"
            : "FFmpeg 未就绪（解码检测不可用）";
        FfmpegStatusText.Foreground = f?.FfmpegOk == true
            ? (Brush)Application.Current.Resources["AppTextTertiaryBrush"]
            : (Brush)Application.Current.Resources["AppWarnBrush"];

        Ip2RegionStatusText.Text = env.Ip2Region
            ? "离线归属地库已加载"
            : "归属地库缺失（仅影响 IP 归属显示）";
        Ip2RegionStatusText.Foreground = env.Ip2Region
            ? (Brush)Application.Current.Resources["AppTextTertiaryBrush"]
            : (Brush)Application.Current.Resources["AppWarnBrush"];

        UpdateStatus();
    }

    private void UpdateStats()
    {
        ValidStat.Text = ViewModel.ValidCount.ToString();
        TotalStat.Text = ViewModel.Channels.Count.ToString();
        UpdateStatus();
    }

    private void UpdateEmptyState()
    {
        var hasAny = ViewModel.Channels.Count > 0;
        EmptyState.Visibility = hasAny ? Visibility.Collapsed : Visibility.Visible;
        ChannelList.Visibility = hasAny ? Visibility.Visible : Visibility.Collapsed;
        TotalStat.Text = ViewModel.Channels.Count.ToString();
        ValidStat.Text = ViewModel.Channels.Count(c => c.State == RowState.Valid).ToString();
    }

    private void UpdateStatus()
    {
        var parts = new List<string>(4);
        if (!string.IsNullOrEmpty(ViewModel.SourceName))
            parts.Add(ViewModel.SourceFormat.Length > 0
                ? $"{ViewModel.SourceName} · {ViewModel.SourceFormat}"
                : ViewModel.SourceName);

        var total = ViewModel.Channels.Count;
        if (total > 0) parts.Add($"{total} 个地址");
        if (ViewModel.ProgressTotal > 0)
            parts.Add($"进度 {ViewModel.ProgressDone}/{ViewModel.ProgressTotal}");
        if (ViewModel.ValidCount > 0)
            parts.Add($"可用 {ViewModel.ValidCount}");

        SummaryLine.Text = parts.Count == 0
            ? "载入播放列表后即可开始测试"
            : string.Join("   ·   ", parts);
    }
}
