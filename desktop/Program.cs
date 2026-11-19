// winter.cc 7.8 - transparent native WinForms/WebView2 website wrapper.
// No PyInstaller bootloader, process injection, persistence tasks, or
// antivirus exclusions. This loads the same live URL as the web version.
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace WinterCC;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new WinterWindow());
    }
}

public sealed class WinterWindow : Form
{
    private const string SiteUrl = "https://winter-cc.onrender.com/";
    private readonly WebView2 browser = new() { Dock = DockStyle.Fill };

    public WinterWindow()
    {
        Text = "winter.cc";
        Size = new Size(1240, 820);
        MinimumSize = new Size(760, 540);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(18, 11, 32);
        Controls.Add(browser);
        Shown += async (_, _) => await StartBrowserAsync();
    }

    private async Task StartBrowserAsync()
    {
        try
        {
            string localData = Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData);
            string profilePath = Path.Combine(localData, "winter.cc", "WebView2");
            Directory.CreateDirectory(profilePath);
            var environment = await CoreWebView2Environment.CreateAsync(
                userDataFolder: profilePath);
            await browser.EnsureCoreWebView2Async(environment);

            // Login cookies and downloads use WebView2's standard settings.
            browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
            browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;

            // User-clicked new windows open in their normal browser.
            browser.CoreWebView2.NewWindowRequested += (_, args) =>
            {
                args.Handled = true;
                OpenExternalLink(args.Uri);
            };
            // Keep navigations inside winter.cc. Outside links are not
            // permitted to take over the app window.
            browser.CoreWebView2.NavigationStarting += (_, args) =>
            {
                if (Uri.TryCreate(args.Uri, UriKind.Absolute, out var target)
                    && target.Scheme == Uri.UriSchemeHttps
                    && target.Host.Equals("winter-cc.onrender.com",
                        StringComparison.OrdinalIgnoreCase))
                    return;
                args.Cancel = true;
                OpenExternalLink(args.Uri);
            };
            browser.CoreWebView2.Navigate(SiteUrl);
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "winter.cc couldn't start its browser window. " +
                "Check that Microsoft Edge WebView2 Runtime is installed, " +
                "and that you have an internet connection.\n\n" + error.Message,
                "winter.cc", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static void OpenExternalLink(string? address)
    {
        if (!Uri.TryCreate(address, UriKind.Absolute, out var target)) return;
        if (target.Scheme is not ("https" or "http")) return;
        try
        {
            Process.Start(new ProcessStartInfo(target.AbsoluteUri)
            {
                UseShellExecute = true
            });
        }
        catch (Exception)
        {
            // Navigation to optional external profile links can fail safely.
        }
    }
}
