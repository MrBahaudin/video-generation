# Created by Zakariya
"""
bot_ui_pyqt6.py — Main Entry Point for Zakariya Automator.
Launch sequence: Splash Screen → Login Window → Main Window
"""

import sys
import os

# ── Base Directory Detection ──
# Supports: dev mode, PyInstaller, Nuitka onefile/standalone
def _get_base_dir():
    # PyInstaller
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    # Nuitka onefile — __file__ resolves to the temp extraction directory
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        # Verify this is a real extracted dir (not source)
        if os.path.exists(os.path.join(base, 'playwright_browsers')):
            return base
        if os.path.exists(os.path.join(base, 'VERSION')):
            return base
    except Exception:
        pass
    # Fallback: executable directory
    return os.path.dirname(os.path.abspath(sys.argv[0]))

base_dir = _get_base_dir()
sys.path.insert(0, base_dir)

# ── Security Check (Anti-Debug / Anti-Crack) ──
# Must run BEFORE any UI loads — silent exit if tampered
try:
    from core.security import enforce_security
    enforce_security()
except Exception:
    pass  # Security module failure should never break the app

# ── Playwright Browser Path (Chrome bundled) ──
# Searches for playwright_browsers in multiple locations
def _setup_playwright_browsers():
    search_paths = [
        os.path.join(base_dir, "playwright_browsers"),
        os.path.join(os.path.dirname(sys.argv[0]), "playwright_browsers"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "playwright_browsers"),
    ]
    for path in search_paths:
        if os.path.exists(path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = path
            # Also set PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD to prevent auto-download attempts
            os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
            return path
    return None

_setup_playwright_browsers()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── Step 1: Show Splash Screen ──
    from ui.splash_screen import SplashScreen
    splash = SplashScreen()
    splash.show()
    app.processEvents()

    # Simulate loading progress
    splash.set_status("Initializing modules...")
    splash.set_progress(0.2)
    app.processEvents()

    # Import heavy modules while splash is showing
    splash.set_status("Loading UI components...")
    splash.set_progress(0.4)
    app.processEvents()

    from ui.main_window import MainWindow
    splash.set_progress(0.6)
    app.processEvents()

    splash.set_status("Preparing workspace...")
    splash.set_progress(0.8)
    app.processEvents()

    # ── Step 2: Finalize splash (updates handled by MainWindow) ──
    splash.set_status("Starting...")
    splash.set_progress(1.0)
    app.processEvents()

    # ── Step 3: Finish Splash & Show App ──
    def on_splash_done():
        # Show Main Window
        window = MainWindow()
        window.show()

        # Keep reference to prevent garbage collection
        app._main_window = window

    splash.finished.connect(on_splash_done)

    # Trigger splash finish after a short delay (let user see the splash)
    QTimer.singleShot(1500, splash.finish)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
