"""winter.cc Windows desktop app.

This is a native WebView2 window showing the *live* winter.cc site.
It uses the same server, frontend, login, and result handling as the web app.
Requires Internet access and the Microsoft Edge WebView2 Runtime.
"""

import os
from pathlib import Path

import webview


SITE_URL = "https://winter-cc.onrender.com/"


def main():
    # Cookies persist so the website's Remember Me option works.
    storage_base = Path(
        os.environ.get("LOCALAPPDATA", str(Path.home()))
    ) / "winter.cc" / "WebView2"
    storage_base.mkdir(parents=True, exist_ok=True)

    # Match the site's download/export and external profile links.
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    webview.create_window(
        title="winter.cc",
        url=SITE_URL,
        width=1250,
        height=820,
        min_size=(740, 540),
        resizable=True,
        background_color="#120b20",
        text_select=True,
    )

    webview.start(
        gui="edgechromium",
        private_mode=False,
        storage_path=str(storage_base),
        debug=False,
    )


if __name__ == "__main__":
    main()
