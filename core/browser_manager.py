import threading
import os
from playwright.sync_api import sync_playwright
from utils.config import AUTH_FILE

class BrowserPool:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._playwright = None
                    cls._instance._browser = None
        return cls._instance

    def _ensure_browser(self):
        if self._browser is None or not self._browser.is_connected():
            self._restart()

    def _restart(self):
        if self._playwright:
            try:
                self._playwright.stop()
            except:
                pass
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
    def new_context(self):
        self._ensure_browser()
        storage = AUTH_FILE if os.path.exists(AUTH_FILE) else None
        context = self._browser.new_context(
            storage_state=storage,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            )
        )
        return context

    def close_context(self, context):
        try:
            context.close()
        except:
            pass

    def restart_browser(self):
        self._restart()

browser_pool = BrowserPool()