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
            
            # Download to temp location
            if getattr(sys, 'frozen', False):
                download_dir = os.path.dirname(sys.executable)
            else:
                download_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
            download_path = os.path.join(download_dir, f"_update_{file_name}")
            
            self._log(f"Downloading: {url}")
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
            
            # ── Update local VERSION file to prevent update loop ──
            try:
                version_file = os.path.join(download_dir, "VERSION")
                with open(version_file, "w") as f:
                    f.write(info["version"])
                self._log(f"Updated VERSION file to {info['version']}")
            except Exception as e:
                self._log(f"Could not update VERSION file: {e}")
            
            self._log(f"Downloaded to: {download_path}")
            return download_path
            
        except Exception as e:
            self._log(f"Download failed: {e}")
            if progress_callback:
                progress_callback(0.0, f"Download failed: {e}")
            return None
    
    def install_and_restart(self, download_path):
        """
        Run the installer or replace the exe if it's portable.
        """
        if not sys.executable.endswith(".exe"):
            self._log("Not running as exe — skipping install. (Dev mode)")
            return
            
        try:
            self._log(f"Installing update from: {download_path}")
            
            # If the downloaded file is a Setup installer (Inno Setup)
            if "Setup" in os.path.basename(download_path):
                # Run the installer silently
                subprocess.Popen(
                    [download_path, "/SILENT", "/SP-"],
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                sys.exit(0)
            
            # Legacy fallback for portable EXE replacement
            current_exe = sys.executable
            current_dir = os.path.dirname(current_exe)
            backup_path = current_exe + ".backup"
            
            bat_path = os.path.join(current_dir, "_updater.bat")
            
            # Write updated VERSION file path
            version_path = os.path.join(current_dir, "VERSION")
            # Extract version from downloaded filename or info
            new_version = ""
            fname = os.path.basename(download_path)
            # Try to extract version like "v1.1.4" from filename
            import re
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
            # Add VERSION update to bat script
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
