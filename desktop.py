
import sys
import webbrowser
import tkinter as tk
from tkinter import messagebox

APP_NAME = "winter.cc"
APP_URL = "https://winter-cc.onrender.com"

try:
    import webview
except ImportError:
    root = tk.Tk()
    root.withdraw()

    messagebox.showerror(
        APP_NAME,
        "The desktop application is missing a required component."
    )
    sys.exit(1)


def open_in_browser():
    webbrowser.open(APP_URL)


def main():
    try:
        webview.create_window(
            title="winter.cc — Username Studio",
            url=APP_URL,
            width=1200,
            height=800,
            min_size=(800, 550),
            resizable=True,
            background_color="#0b0912",
            text_select=True
        )

        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False
        )

    except Exception as error:
        root = tk.Tk()
        root.withdraw()

        retry = messagebox.askyesno(
            APP_NAME,
            "Unable to start the desktop window.\n\n"
            "Would you like to open winter.cc in your browser?"
        )

        if retry:
            open_in_browser()


if __name__ == "__main__":
    main()
