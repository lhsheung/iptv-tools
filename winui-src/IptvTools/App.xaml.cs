using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace IptvTools;

public partial class App : Application
{
    private Window? _window;

    /// <summary>供文件选择器等需要 Window 句柄的地方引用。</summary>
    public static Window? MainWindowInstance { get; private set; }

    /// <summary>UI 线程调度器，供后台线程回调编组。</summary>
    public static DispatcherQueue Dispatcher { get; private set; } = null!;

    public App()
    {
        InitializeComponent();
        Dispatcher = DispatcherQueue.GetForCurrentThread();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        MainWindowInstance = _window;
        _window.Activate();
    }
}
