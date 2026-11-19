"""Desktop entrypoint. Bundled FastAPI server + native window when pywebview is installed."""
import os, socket, threading, time, webbrowser, sys
from pathlib import Path
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def free_port():
    with socket.socket() as s: s.bind(('127.0.0.1',0));return s.getsockname()[1]
def main():
    root=Path(__file__).resolve().parent.parent
    os.environ.setdefault('WINTER_DB',str(Path.home()/'.wintercc'/'winter.db'))
    port=free_port();url=f'http://127.0.0.1:{port}'
    config=uvicorn.Config('app.main:app',host='127.0.0.1',port=port,log_level='warning')
    server=uvicorn.Server(config)
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.1):break
        except OSError:time.sleep(.1)
    try:
        import webview
        webview.create_window('winter.cc',url,width=1160,height=800,min_size=(500,550));webview.start()
    except ImportError:
        print('Opening local winter.cc at',url);webbrowser.open(url)
        try: thread.join()
        except KeyboardInterrupt: pass
    finally:server.should_exit=True
if __name__=='__main__':main()
