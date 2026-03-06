"""
Dronor MKT — Command Center Desktop App
Uses PyObjC directly (no pywebview) for full JS compatibility.
"""
import objc
from AppKit import (
    NSApplication, NSWindow, NSObject,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSBackingStoreBuffered, NSMakeRect, NSApp,
    NSApplicationActivationPolicyRegular,
    NSBundle
)
from WebKit import (
    WKWebView, WKWebViewConfiguration,
    WKPreferences, WKUserContentController
)
import threading
import time
import urllib.request
import urllib.error
import sys
import os

BACKEND_URL = "http://localhost:8899"
TITLE = "Dronor / MKT"
WIDTH = 1440
HEIGHT = 900
MAX_WAIT_SEC = 30


def wait_for_backend():
    """Wait until backend is ready."""
    for _ in range(MAX_WAIT_SEC * 2):
        try:
            urllib.request.urlopen(BACKEND_URL + "/cc/setup-check", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


class AppDelegate(NSObject):
    window = None
    webview = None

    def applicationDidFinishLaunching_(self, notification):
        # Create window
        style = (NSWindowStyleMaskTitled |
                 NSWindowStyleMaskClosable |
                 NSWindowStyleMaskMiniaturizable |
                 NSWindowStyleMaskResizable)
        
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT),
            style,
            NSBackingStoreBuffered,
            False
        )
        self.window.setTitle_(TITLE)
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)

        # Configure WKWebView — explicitly enable JS
        config = WKWebViewConfiguration.alloc().init()
        prefs = WKPreferences.alloc().init()
        prefs.setJavaScriptEnabled_(True)
        # Disable fraud warnings that can block localhost
        prefs.setValue_forKey_(False, 'safeBrowsingEnabled')
        config.setPreferences_(prefs)
        
        # Allow mixed content and local file access
        config.setValue_forKey_(True, 'allowUniversalAccessFromFileURLs')
        config.setValue_forKey_(True, 'allowFileAccessFromFileURLs')

        # Create webview
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            self.window.contentView().bounds(),
            config
        )
        self.webview.setAutoresizingMask_(18)  # width+height flexible
        self.window.contentView().addSubview_(self.webview)

        # Load backend in background thread
        threading.Thread(target=self._load_when_ready, daemon=True).start()

    def _load_when_ready(self):
        import urllib.request as ur
        from Foundation import NSURL
        
        # Show loading page immediately
        loading_html = """<html><body style='background:#0d1117;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-family:monospace'><div style='color:#00d4aa;text-align:center'><div style='font-size:32px;font-weight:bold;letter-spacing:8px'>DRONOR / MKT</div><div style='color:#666;margin-top:16px;font-size:13px'>CONNECTING...</div></div></body></html>"""
        
        from AppKit import NSThread
        self.webview.performSelectorOnMainThread_withObject_waitUntilDone_(
            'loadHTMLString:baseURL:',
            None,
            False
        )
        
        # Wait for backend
        ready = wait_for_backend()
        
        # Load URL on main thread
        from Foundation import NSURL, NSURLRequest
        url = NSURL.URLWithString_(BACKEND_URL)
        request = NSURLRequest.requestWithURL_(url)
        
        # Must call UI on main thread
        self.webview.performSelectorOnMainThread_withObject_waitUntilDone_(
            objc.selector(None, b'loadRequest:'),
            request,
            True
        )

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    
    # Load backend in thread first, then start app
    def load_url_when_ready():
        time.sleep(0.5)  # wait for window to be ready
        ready = wait_for_backend()
        from Foundation import NSURL, NSURLRequest
        url = NSURL.URLWithString_(BACKEND_URL)
        request = NSURLRequest.requestWithURL_(url)
        if delegate.webview:
            delegate.webview.performSelectorOnMainThread_withObject_waitUntilDone_(
                objc.selector(None, b'loadRequest:'),
                request,
                False
            )
    
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == '__main__':
    main()
