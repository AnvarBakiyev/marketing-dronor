"""
Marketing Dronor — Desktop App
PyWebView wrapper around Flask backend.
Works on macOS, Windows, Linux.

Usage:
    python app.py              # normal launch
    python app.py --debug      # show browser devtools
"""
import sys
import threading
import time
import socket
import webbrowser
import argparse
from pathlib import Path

# ── ensure we can import project modules ──────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── pick a free port ─────────────────────────────────────────────────────────
def find_free_port(start: int = 5555) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                return port
    return start  # fallback

PORT = find_free_port(5555)
URL  = f'http://localhost:{PORT}'

# ── start Flask in background thread ─────────────────────────────────────────
def start_flask():
    import os
    os.chdir(ROOT / 'command_center')  # Flask serves cc_frontend.html from here
    from command_center.cc_backend import app
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

flask_thread = threading.Thread(target=start_flask, daemon=True)
flask_thread.start()

# ── wait until Flask is ready ─────────────────────────────────────────────────
def wait_for_server(url: str, timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('localhost', PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False

# ── launch ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='Open DevTools')
    parser.add_argument('--browser', action='store_true', help='Open in system browser instead of window')
    args = parser.parse_args()

    if not wait_for_server(URL):
        print(f'ERROR: Flask did not start on port {PORT}')
        sys.exit(1)

    if args.browser:
        webbrowser.open(URL)
        print(f'Opened {URL} in browser. Press Ctrl+C to stop.')
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    try:
        import webview
    except ImportError:
        print('pywebview not installed — falling back to system browser')
        webbrowser.open(URL)
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    window = webview.create_window(
        title='Marketing Dronor — Command Center',
        url=URL,
        width=1280,
        height=820,
        min_size=(900, 600),
        background_color='#0a0a0b',
    )

    webview.start(
        debug=args.debug,
        http_server=False,  # we use our own Flask server
    )


if __name__ == '__main__':
    main()
