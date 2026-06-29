# Created by Zakariya
"""
auto_updater.py — GitHub Auto-Update System for Zakariya Automator.
Checks GitHub Releases for new versions and downloads updates automatically.
Source code stays hidden (private repo) — only compiled .exe is distributed.
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import requests
from packaging import version as pkg_version


# ── Configuration ──────────────────────────────────────────────
GITHUB_OWNER = "MrBahaudin"          # Your GitHub username
GITHUB_REPO = "video-generation"     # Your GitHub repo name

def _load_github_token():
    """Load token from config.json or environment."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        base = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base, "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = json.load(f)
            return cfg.get("github_token", "")
    except Exception:
        pass
    return ""

GITHUB_TOKEN = _load_github_token()

RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# App version
def _get_current_version():
    # Priority 1: Check VERSION file next to the exe (updated by auto-updater)
    if getattr(sys, 'frozen', False):
        try:
            ext_version = os.path.join(os.path.dirname(sys.executable), "VERSION")
            if os.path.exists(ext_version):
                with open(ext_version, "r") as f:
                    v = f.read().strip()
                if v:
                    return v
        except Exception:
            pass
    
    # Priority 2: Check bundled VERSION inside _MEIPASS (or dev source)
    try:
        base = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        version_file = os.path.join(base, "VERSION")
        if os.path.exists(version_file):
            with open(version_file, "r") as f:
                return f.read().strip()
    except Exception:
        pass
    return "1.0.0"


class AutoUpdater:
    """
    Checks GitHub Releases for new versions.
    
    Usage:
        updater = AutoUpdater()
        has_update, info = updater.check_for_update()
        if has_update:
            updater.download_and_install(info, progress_callback=lambda p, s: ...)
    """
    
    def __init__(self):
        self.current_version = _get_current_version()
        self._log(f"Current version: {self.current_version}")
    
    def _log(self, msg):
        print(f"[Updater] {msg}")
    
    def _get_headers(self):
        """Get request headers (with auth for private repo)."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        return headers
    
    def check_for_update(self):
        """
        Check if a newer version is available on GitHub Releases.
        
        Returns:
            (has_update: bool, info: dict or None)
            info contains: version, download_url, release_notes, file_name
        """
        try:
            self._log(f"Checking for updates... (current: {self.current_version})")
            resp = requests.get(RELEASES_URL, headers=self._get_headers(), timeout=15)
            
            # If token is revoked/invalid, retry WITHOUT token (repo may be public)
            if resp.status_code == 401:
                self._log("Token invalid/revoked — retrying without auth...")
                resp = requests.get(RELEASES_URL, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15)
            
            if resp.status_code == 404:
                self._log("No releases found.")
                return False, None
            
            if resp.status_code != 200:
                self._log(f"GitHub API error: {resp.status_code}")
                return False, None
            
            release = resp.json()
            latest_version = release.get("tag_name", "").lstrip("v")
            
            if not latest_version:
                self._log("No version tag found in release.")
                return False, None
            
            # Compare versions
            try:
                is_newer = pkg_version.parse(latest_version) > pkg_version.parse(self.current_version)
            except Exception:
                # Fallback: simple string comparison
                is_newer = latest_version != self.current_version
            
            if not is_newer:
                self._log(f"Already up to date ({self.current_version})")
                return False, None
            
            # Find the .exe download asset
            assets = release.get("assets", [])
            download_asset = None
            for asset in assets:
                name = asset.get("name", "").lower()
                if name.endswith(".exe") or name.endswith(".zip"):
                    download_asset = asset
                    break
            
            if not download_asset:
                self._log("No downloadable asset found in release.")
                return False, None
            
            info = {
                "version": latest_version,
                "download_url": download_asset["browser_download_url"],
                "file_name": download_asset["name"],
                "file_size": download_asset.get("size", 0),
                "release_notes": release.get("body", ""),
                "published_at": release.get("published_at", ""),
            }
            
            self._log(f"Update available: {self.current_version} → {latest_version}")
            return True, info
            
        except requests.exceptions.ConnectionError:
            self._log("No internet connection. Skipping update check.")
            return False, None
        except Exception as e:
            self._log(f"Update check failed: {e}")
            return False, None
    
    def download_update(self, info, progress_callback=None):
        """
        Download the update file.
        
        Args:
            info: dict from check_for_update()
            progress_callback: func(percent: float, status: str) — called during download
        
        Returns:
            download_path: str or None
        """
        try:
            url = info["download_url"]
            file_name = info["file_name"]
            file_size = info.get("file_size", 0)
            
            # Download to TEMP directory (avoids Program Files permission issues)
            import tempfile
            download_dir = tempfile.gettempdir()
            
            download_path = os.path.join(download_dir, f"_update_{file_name}")
            
            self._log(f"Downloading: {url}")
            self._log(f"Download location: {download_path}")
            if progress_callback:
                progress_callback(0.0, f"Downloading v{info['version']}...")
            
            headers = self._get_headers()
            resp = requests.get(url, headers=headers, stream=True, timeout=300)
            resp.raise_for_status()
            
            downloaded = 0
            with open(download_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if file_size > 0 and progress_callback:
                            percent = min(downloaded / file_size, 1.0)
                            progress_callback(percent, f"Downloading... {int(percent * 100)}%")
            
            if progress_callback:
                progress_callback(1.0, "Download complete!")
            
            # Verify downloaded file exists and has reasonable size
            if not os.path.exists(download_path):
                self._log("Download failed: file not found after download")
                return None
            
            actual_size = os.path.getsize(download_path)
            if actual_size < 1_000_000:  # Less than 1MB = probably corrupted
                self._log(f"Download seems corrupt: only {actual_size} bytes")
                os.remove(download_path)
                return None
            
            # NOTE: VERSION file is NOT updated here — only after successful install
            # This ensures failed installs don't prevent retry on next launch
            
            self._log(f"Downloaded to: {download_path} ({actual_size / 1024 / 1024:.1f} MB)")
            return download_path
            
        except Exception as e:
            self._log(f"Download failed: {e}")
            if progress_callback:
                progress_callback(0.0, f"Download failed: {e}")
            return None
    
    def install_and_restart(self, download_path, new_version=""):
        """
        Run the installer or replace the exe if it's portable.
        
        Args:
            download_path: Path to the downloaded installer/exe
            new_version: Version string for VERSION file update
        """
        if not sys.executable.endswith(".exe"):
            self._log("Not running as exe — skipping install. (Dev mode)")
            return
        
        # Verify the file actually exists before proceeding
        if not os.path.exists(download_path):
            self._log(f"Install failed: file not found at {download_path}")
            return
            
        try:
            self._log(f"Installing update from: {download_path}")
            
            # If the downloaded file is a Setup installer (Inno Setup)
            if "Setup" in os.path.basename(download_path):
                # Run the installer silently with proper flags
                import time as _time
                subprocess.Popen(
                    [
                        download_path,
                        "/SILENT",
                        "/SP-",
                        "/CLOSEAPPLICATIONS",
                        "/FORCECLOSEAPPLICATIONS",
                        "/MERGETASKS=desktopicon",
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                # Give the installer process time to initialize before we exit
                _time.sleep(2)
                sys.exit(0)
            
            # Legacy fallback for portable EXE replacement
            current_exe = sys.executable
            current_dir = os.path.dirname(current_exe)
            backup_path = current_exe + ".backup"
            
            bat_path = os.path.join(current_dir, "_updater.bat")
            
            # Write updated VERSION file path
            version_path = os.path.join(current_dir, "VERSION")
            # Extract version from downloaded filename if not provided
            if not new_version:
                import re
                fname = os.path.basename(download_path)
                ver_match = re.search(r'v?(\d+\.\d+\.\d+)', fname)
                if ver_match:
                    new_version = ver_match.group(1)
            
            bat_content = f"""@echo off
echo Updating Zakariya Automator...
timeout /t 3 /nobreak >nul
if exist "{backup_path}" del "{backup_path}"
ren "{current_exe}" "{os.path.basename(backup_path)}"
move "{download_path}" "{current_exe}"
"""
            # Update VERSION file ONLY in the bat script (after successful file replace)
            if new_version:
                bat_content += f'echo {new_version}> "{version_path}"\n'
            
            bat_content += f"""start "" "{current_exe}"
del "%~f0"
"""
            
            with open(bat_path, "w") as f:
                f.write(bat_content)
            
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            
            sys.exit(0)
            
        except Exception as e:
            self._log(f"Install failed: {e}")
    
    def check_and_update_async(self, progress_callback=None, on_complete=None):
        """
        Run the full update check + download in a background thread.
        
        Args:
            progress_callback: func(percent, status) — for splash screen
            on_complete: func(has_update, info) — called when done
        """
        def _worker():
            has_update, info = self.check_for_update()
            if has_update and info:
                download_path = self.download_update(info, progress_callback)
                if download_path and getattr(sys, 'frozen', False):
                    self.install_and_restart(download_path)
                elif on_complete:
                    on_complete(True, info)
            elif on_complete:
                on_complete(False, None)
        
        threading.Thread(target=_worker, daemon=True).start()
