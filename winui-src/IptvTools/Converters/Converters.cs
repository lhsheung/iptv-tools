using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Media;
using IptvTools.ViewModels;

namespace IptvTools.Converters;

/// <summary>行状态 → 背景色（浅底圆点）。</summary>
public sealed class StateBgConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, string language)
    {
        var key = value switch
        {
            RowState.Valid => "AppSuccessSoftBrush",
            RowState.Invalid => "AppDangerSoftBrush",
            RowState.Testing => "AppAccentSoftBrush",
            _ => "AppSurfaceAltBrush",
        };
        return Lookup(key);
    }

    public object ConvertBack(object value, Type targetType, object parameter, string language)
        => throw new NotSupportedException();

    internal static Brush Lookup(string key)
    {
        if (Application.Current.Resources.TryGetValue(key, out var v) && v is Brush b)
            return b;
        return new SolidColorBrush(Microsoft.UI.Colors.Gray);
    }
}

/// <summary>行状态 → 前景色（图标）。</summary>
public sealed class StateFgConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, string language)
    {
        var key = value switch
        {
            RowState.Valid => "AppSuccessBrush",
            RowState.Invalid => "AppDangerBrush",
            RowState.Testing => "AccentTextFillColorPrimaryBrush",
            _ => "AppTextTertiaryBrush",
        };
        return StateBgConverter.Lookup(key);
    }

    public object ConvertBack(object value, Type targetType, object parameter, string language)
        => throw new NotSupportedException();
}

/// <summary>bool → Visibility。</summary>
public sealed class BoolToVisibilityConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, string language)
        => value is true ? Visibility.Visible : Visibility.Collapsed;

    public object ConvertBack(object value, Type targetType, object parameter, string language)
        => value is Visibility.Visible;
}
