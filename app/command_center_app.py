"""
Dronor MKT — Command Center Desktop App
Opens Command Center (localhost:8899) in a native macOS window via pywebview.
Waits for backend to be ready before opening the window.
"""
import webview
import threading
import time
import urllib.request
import urllib.error
import sys
import os

BACKEND_URL = "http://localhost:8899"
TITLE = "Dronor / MKT — Command Center"
WIDTH = 1440
HEIGHT = 900
MAX_WAIT_SEC = 30


def wait_for_backend(url: str, timeout: int) -> bool:
    """Poll backend until it responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/cc/setup-check", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    # Create window immediately with a loading screen
    loading_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body {
        background: #0d1117;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100vh;
        font-family: 'SF Mono', 'Menlo', monospace;
        color: #00d4aa;
      }
      .logo { font-size: 28px; font-weight: 700; letter-spacing: .15em; margin-bottom: 8px; }
      .sub  { font-size: 11px; color: #8b949e; letter-spacing: .2em; margin-bottom: 48px; }
      .dot  {
        width: 8px; height: 8px; border-radius: 50%;
        background: #00d4aa;
        display: inline-block;
        margin: 0 4px;
        animation: pulse 1.2s ease-in-out infinite;
      }
      .dot:nth-child(2) { animation-delay: .2s; }
      .dot:nth-child(3) { animation-delay: .4s; }
      @keyframes pulse {
        0%,80%,100% { opacity: .2; transform: scale(.8); }
        40% { opacity: 1; transform: scale(1.2); }
      }
      .status { margin-top: 24px; font-size: 10px; color: #444d56; letter-spacing: .1em; }
    </style>
    </head>
    <body>
      <div class="logo">DRONOR / MKT</div>
      <div class="sub">COMMAND CENTER</div>
      <div>
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </div>
      <div class="status" id="s">CONNECTING TO BACKEND...</div>
      <script>
        let dots = 0;
        setInterval(() => {
          dots = (dots + 1) % 4;
          document.getElementById('s').textContent =
            'CONNECTING TO BACKEND' + '.'.repeat(dots);
        }, 400);
      </script>
    </body>
    </html>
    """

    window = webview.create_window(
        title=TITLE,
        html=loading_html,
        width=WIDTH,
        height=HEIGHT,
        min_size=(900, 600),
        background_color='#0d1117',
    )

    def on_ready():
        """Called after webview is shown — wait for backend then load URL."""
        ready = wait_for_backend(BACKEND_URL, MAX_WAIT_SEC)
        if ready:
            window.load_url(BACKEND_URL)
        else:
            window.load_html("""
            <!DOCTYPE html><html><head>
            <style>
              body{background:#0d1117;color:#e74c3c;font-family:monospace;
                   display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column}
              .title{font-size:18px;font-weight:700;margin-bottom:12px}
              .msg{font-size:12px;color:#8b949e;text-align:center;max-width:400px;line-height:1.8}
              button{margin-top:24px;background:#00d4aa;color:#0d1117;border:none;
                     font-family:monospace;font-size:11px;padding:8px 20px;
                     border-radius:3px;cursor:pointer;font-weight:700;letter-spacing:.1em}
            </style></head><body>
            <div class="title">⚠ BACKEND NOT RESPONDING</div>
            <div class="msg">localhost:8899 не отвечает.<br>
            Проверь что cc_backend.py запущен.</div>
            <button onclick="location.reload()">RETRY</button>
            </body></html>
            """)

    threading.Thread(target=on_ready, daemon=True).start()

    webview.start(debug=False)


if __name__ == '__main__':
    main()
