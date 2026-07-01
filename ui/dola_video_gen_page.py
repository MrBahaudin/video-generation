# Created by Zakariya
"""
dola_video_gen_page.py — Dola Browser Bot page widget.
Extracts the full Dola browser-based automation UI from bot_ui_pyqt6.py
into a reusable QWidget page for sidebar navigation.
"""

import os
import re
import csv
import io
import sys
import time
import json
import asyncio
import subprocess
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QTextEdit, QProgressBar,
    QFileDialog, QMessageBox, QCheckBox, QFrame, QScrollArea, QSpinBox,
    QApplication, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QTextCursor, QColor

# Add project root to path so we can import headless_bot and core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.settings_manager import load_settings, save_settings, load_user_data, save_user_data
from core.stats_tracker import StatsTracker

# Import direct API client (no browser needed for sending)
try:
    import dola_direct as _dd
    _DIRECT_AVAILABLE = True
except Exception:
    _DIRECT_AVAILABLE = False



def _card(parent=None):
    f = QFrame(parent)
    f.setObjectName("card")
    f.setFrameShape(QFrame.Shape.StyledPanel)
    return f

def _label(text, obj_name=None, parent=None):
    lbl = QLabel(text, parent)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl

def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(212, 163, 115, 0.08);")
    return line


class DolaBotWorker(QThread):
    """
    Background worker — Direct API video generation (no browser window).
    Uses dola_direct.py: urllib POST /chat/completion + Playwright headless
    poll /im/chain/single for video URL + urllib download.
    Same signal interface as before for full UI compatibility.
    """
    log_signal = pyqtSignal(str, str)           # message, level
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int, int)  # completed, total, successes
    stats_signal = pyqtSignal(int, int, int, int, int)  # total, queued, active, ok, failed
    finished_signal = pyqtSignal()

    def __init__(self, prompt_data, duration, total, concurrency, output_dir,
                 wait_timeout=600, start_delay=2, next_delay=5,
                 headless=True, watermark_mode="Blur (Delogo)", proxy_list=None,
                 mobile_mode=True, naming_mode="Title in CSV",
                 process_start_timeout=60, ratio="9:16", cookies_list=None):
        super().__init__()
        self.prompt_data       = prompt_data
        self.duration          = duration
        self.total             = total
        self.concurrency       = concurrency
        self.output_dir        = output_dir
        self.wait_timeout      = wait_timeout
        self.current_timeout   = wait_timeout
        self.start_delay       = start_delay
        self.next_delay        = next_delay
        self.headless          = headless
        self.watermark_mode    = watermark_mode
        self.proxy_list        = proxy_list or []
        self.mobile_mode       = mobile_mode
        self.naming_mode       = naming_mode
        self.process_start_timeout = process_start_timeout
        self.ratio             = ratio
        self._is_stopped       = False
        self._failed_prompts   = []
        # all_cookie_accounts: list of accounts (each account = list of cookie dicts)
        # Round-robin: task N uses account N % len(accounts)
        raw = cookies_list or []
        # Support both "flat" (list of dicts = 1 account) and "nested" (list of lists)
        if raw and isinstance(raw[0], dict):
            self._all_accounts = [raw]   # wrap single account
        else:
            self._all_accounts = raw or [[]]
        # Per-account rate limiting
        self._credit_failures   = {}   # acct_idx -> consecutive "credit_exhausted" count
        self._exhausted_accts   = set()  # permanently disabled this batch
        self._success_count     = {}   # acct_idx -> successful video count this cycle
        self._cooldown_until    = {}   # acct_idx -> datetime when cooldown ends
        self.MAX_VIDEOS_PER_ACCT = 3   # videos per account before cooldown
        self.COOLDOWN_HOURS      = 2   # hours to wait after limit reached

    # ── Per-account video success tracking ─────────────────────────────────────
    def _on_account_success(self, acct_idx: int):
        """Call after a video is successfully downloaded. Starts cooldown after 3 videos."""
        from datetime import datetime, timedelta
        n = len(self._all_accounts)
        self._success_count[acct_idx] = self._success_count.get(acct_idx, 0) + 1
        count = self._success_count[acct_idx]
        if count >= self.MAX_VIDEOS_PER_ACCT:
            until = datetime.now() + timedelta(hours=self.COOLDOWN_HOURS)
            self._cooldown_until[acct_idx] = until
            self._success_count[acct_idx]  = 0   # reset for next cycle
            self.log(
                f"[⏰] Account {acct_idx+1}/{n} limit reached "
                f"({self.MAX_VIDEOS_PER_ACCT} videos) — cooldown until "
                f"{until.strftime('%H:%M')} ({self.COOLDOWN_HOURS}h)", "warn"
            )

    def _on_account_fail(self, acct_idx: int):
        """Track credit-exhausted rejections. 2 consecutive = disable for this batch."""
        n = len(self._all_accounts)
        self._credit_failures[acct_idx] = self._credit_failures.get(acct_idx, 0) + 1
        failures = self._credit_failures[acct_idx]
        if failures >= 2:
            self._exhausted_accts.add(acct_idx)
            self.log(
                f"[✖] Account {acct_idx+1}/{n} DISABLED "
                f"(2 consecutive credit errors) — won't be used again this batch", "warn"
            )
        else:
            self.log(
                f"[!] Account {acct_idx+1}/{n} credit warning "
                f"({failures}/2 before disable)", "warn"
            )

    def _pick_account(self, instance_id: int):
        """
        Round-robin account selection.
        Skips: permanently exhausted accounts, accounts in cooldown.
        Returns (acct_idx, cookies) or (None, None) if all unavailable.
        """
        from datetime import datetime
        n = max(len(self._all_accounts), 1)
        base_idx = (instance_id - 1) % n
        now = datetime.now()

        for offset in range(n):
            candidate = (base_idx + offset) % n

            # Skip permanently disabled
            if candidate in self._exhausted_accts:
                continue

            # Check cooldown
            if candidate in self._cooldown_until:
                if now < self._cooldown_until[candidate]:
                    remaining = int((self._cooldown_until[candidate] - now).total_seconds() / 60)
                    # (don't log every pick — only log when all are cooling)
                    continue
                else:
                    # Cooldown expired — re-enable
                    del self._cooldown_until[candidate]
                    n_accts = len(self._all_accounts)
                    self.log(f"[✓] Account {candidate+1}/{n_accts} cooldown ended — re-enabled")

            # This account is available
            cookies = self._all_accounts[candidate] if self._all_accounts else None
            return candidate, cookies

        # All accounts unavailable — log why
        n_accts = len(self._all_accounts)
        in_cooldown = [i for i in range(n_accts) if i in self._cooldown_until and i not in self._exhausted_accts]
        disabled    = list(self._exhausted_accts)
        if in_cooldown:
            soonest = min(self._cooldown_until[i] for i in in_cooldown)
            remaining_min = max(1, int((soonest - now).total_seconds() / 60))
            self.log(
                f"[⏸] All {n_accts} accounts in cooldown. "
                f"Next available in ~{remaining_min} min. Failing remaining tasks.", "warn"
            )
        elif disabled:
            self.log(f"[✖] All accounts disabled (credit errors). Failing remaining tasks.", "warn")
        return None, None


    def stop(self):
        self._is_stopped = True

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.batch_manager())
        loop.close()
        self.finished_signal.emit()

    def log(self, message):
        print(f"[GUI Log] {message}")
        self.log_signal.emit(message, "info")

    def log_error(self, message):
        print(f"[GUI Error] {message}")
        self.error_signal.emit(message)

    async def _run_single(self, instance_id: int, prompt_text: str, caption=None) -> bool:
        """
        Generate one video via direct API. Returns True on success.
        Round-robin: selects account by (instance_id - 1) % num_accounts.
        Skips exhausted (credit-depleted) accounts automatically.
        """
        import re as _re
        import shutil

        # ── Round-robin cookie selection (skip exhausted accounts) ─────────
        acct_idx, cookies = self._pick_account(instance_id)
        if acct_idx is None:
            self.log_error(f"[{instance_id}] All accounts exhausted — skipping task")
            return False

        n_accts = len(self._all_accounts)
        self.log(f"[{instance_id}] POST /chat/completion — '{prompt_text[:40]}'")
        if n_accts > 1:
            self.log(f"[{instance_id}] Using account {acct_idx + 1}/{n_accts}")

        # ── Step 1: Send request (no browser) ─────────────────────────────
        reject_out = [None]
        conv_id = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _dd.send_video_request(
                prompt_text, self.duration,
                log=lambda m: self.log(f"[{instance_id}] {m}"),
                cookies=cookies,
                _reject_out=reject_out,
                ratio=self.ratio,
            )
        )
        if not conv_id:
            # Track credit exhaustion
            if reject_out[0] == "credit_exhausted":
                self._on_account_fail(acct_idx)
                self.log_error(f"[{instance_id}] ❌ Account credit exhausted — please add fresh cookies in Settings")
            elif reject_out[0] == "content_policy":
                self.log_error(f"[{instance_id}] ❌ Content policy rejection — try a different prompt")
            elif reject_out[0] == "duration":
                self.log_error(f"[{instance_id}] ❌ Duration rejected by Dola — prompt may mention time")
            elif reject_out[0] == "cookies_invalid":
                self.log_error(
                    f"[{instance_id}] ❌ Cookies invalid/expired! "
                    f"Please go to Settings and update your Dola.com cookies."
                )
            else:
                # reason=None means cookies are invalid/expired or network error
                self.log_error(
                    f"[{instance_id}] ❌ Failed to get conv_id — "
                    f"Cookies are invalid or expired! "
                    f"Please update cookies in Settings tab and try again."
                )
            return False

        if self._is_stopped:
            return False

        # ── Step 2: Poll for video URL via Playwright headless ─────────────
        self.log(f"[{instance_id}] Polling conv {conv_id} for video...")
        video_url = await _dd.poll_for_video(
            conv_id,
            timeout=self.current_timeout,
            log=lambda m: self.log(f"[{instance_id}] {m}"),
            cookies=cookies,
        )
        if not video_url:
            self.log_error(f"[{instance_id}] Video URL not found (timeout)")
            self._failed_prompts.append({"prompt": prompt_text})
            return False

        if self._is_stopped:
            return False

        # ── Step 3: Download ───────────────────────────────────────────────
        # Determine output filename based on naming mode
        import uuid as _uuid
        if self.naming_mode == "Title in Text File":
            # Random 8-char hex name — matching .txt created after save
            rand_id = _uuid.uuid4().hex[:8]
            filename = f"{rand_id}.mp4"
        elif self.naming_mode == "Title On Video":
            # Use CSV Column B if available, otherwise use prompt text as title
            title_src = caption.strip() if caption else prompt_text[:50].strip()
            cap_clean = _re.sub(r'[<>:"/\\|?*]', '', title_src)
            filename = f"{instance_id:02d}. {cap_clean}.mp4"

        tmp_path = _dd.download_video(
            video_url, prompt_text, instance_id,
            log=lambda m: self.log(f"[{instance_id}] {m}")
        )
        if not tmp_path:
            self.log_error(f"[{instance_id}] Download failed")
            return False

        # ── Step 4: Watermark Removal (ffmpeg) ────────────────────────────
        if self.watermark_mode in ["Blur (Delogo)", "Crop"]:
            tmp_path = await self._remove_watermark(tmp_path, instance_id)

        # Move to output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        dest = os.path.join(self.output_dir, filename)
        try:
            shutil.move(tmp_path, dest)
            self.log(f"[{instance_id}] ✅ Saved: {dest}")
        except Exception as e:
            self.log(f"[{instance_id}] Move failed ({e}), file at: {tmp_path}")
            return True

        # ── Step 5: Post-processing based on naming mode ───────────────────
        if self.naming_mode == "Title On Video":
            title_src = caption.strip() if caption else prompt_text[:50].strip()
            await self._burn_title(dest, instance_id, title_src)

        elif self.naming_mode == "Title in Text File" and caption:
            txt_path = os.path.splitext(dest)[0] + ".txt"
            try:
                with open(txt_path, 'w', encoding='utf-8') as _f:
                    _f.write(caption)
                self.log(f"[{instance_id}] ✅ Text file: {txt_path}")
            except Exception as e:
                self.log_error(f"[{instance_id}] TXT write failed: {e}")

        return True

    async def _remove_watermark(self, video_path: str, instance_id: int) -> str:
        """
        Remove Dola watermark/logo from downloaded video using ffmpeg.
        Returns path to processed video (replaces original in-place).
        """
        import subprocess as _sp, sys as _sys, re as _re

        self.log(f"[{instance_id}] Removing watermark ({self.watermark_mode})...")

        # Locate ffmpeg.exe
        if getattr(_sys, 'frozen', False):
            app_dir = os.path.dirname(_sys.executable)
        else:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_exe = os.path.join(app_dir, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_exe):
            meipass = getattr(_sys, '_MEIPASS', None)
            if meipass:
                ffmpeg_exe = os.path.join(meipass, "ffmpeg.exe")
            if not meipass or not os.path.exists(ffmpeg_exe):
                ffmpeg_exe = "ffmpeg"  # system PATH fallback

        # Probe video dimensions
        v_width, v_height = 720, 1280
        try:
            probe = _sp.run(
                [ffmpeg_exe, "-i", video_path],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True
            )
            m = _re.search(r'Video:.*?\s(\d{3,5})x(\d{3,5})', probe.stderr)
            if m:
                v_width, v_height = int(m.group(1)), int(m.group(2))
        except Exception:
            pass

        # Build filter
        if self.watermark_mode == "Blur (Delogo)":
            w, h = 170, 50
            x = max(0, v_width - w - 10)
            y = max(0, v_height - h - 10)
            filter_cmd = f"delogo=x={x}:y={y}:w={w}:h={h}"
        else:  # Crop
            filter_cmd = "crop=iw:ih-80:0:0"

        temp_path = video_path + ".wm_temp.mp4"
        cmd = [
            ffmpeg_exe, "-y", "-i", video_path,
            "-vf", filter_cmd,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "copy", temp_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            if process.returncode == 0 and os.path.exists(temp_path):
                os.remove(video_path)
                os.rename(temp_path, video_path)
                self.log(f"[{instance_id}] ✅ Watermark removed!")
            else:
                err = stderr.decode("utf-8", errors="ignore")[-200:]
                self.log_error(f"[{instance_id}] ffmpeg error: {err}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
        except FileNotFoundError:
            self.log_error(f"[{instance_id}] ffmpeg not found! Logo NOT removed.")
        except Exception as e:
            self.log_error(f"[{instance_id}] Watermark removal failed: {e}")

        return video_path  # Return original path (modified in-place)

    async def _burn_title(self, video_path: str, instance_id: int, caption: str):
        """
        Burn numbered title onto video using ffmpeg drawtext.
        Text: '{instance_id}. {caption}' — bottom-center, white + black border.
        """
        import subprocess as _sp, sys as _sys

        self.log(f"[{instance_id}] Burning title on video...")

        # Locate ffmpeg
        if getattr(_sys, 'frozen', False):
            app_dir = os.path.dirname(_sys.executable)
        else:
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_exe = os.path.join(app_dir, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_exe):
            meipass = getattr(_sys, '_MEIPASS', None)
            if meipass:
                ffmpeg_exe = os.path.join(meipass, "ffmpeg.exe")
            if not meipass or not os.path.exists(ffmpeg_exe):
                ffmpeg_exe = "ffmpeg"

        # Numbered title text — escape ffmpeg special chars
        title_text = f"{instance_id:02d}. {caption.strip()}"
        escaped = (
            title_text
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "\\%")
        )

        # Font: try Arial, fallback to built-in
        arial = r"C:\Windows\Fonts\arial.ttf"
        if os.path.exists(arial):
            font_part = "fontfile='C\\\\:/Windows/Fonts/arial.ttf':"
        else:
            font_part = ""

        drawtext = (
            f"drawtext={font_part}"
            f"text='{escaped}':"
            f"fontsize=40:"
            f"fontcolor=white:"
            f"borderw=3:"
            f"bordercolor=black:"
            f"x=(w-text_w)/2:"
            f"y=h-th-50"
        )

        temp_path = video_path + ".title_tmp.mp4"
        cmd = [
            ffmpeg_exe, "-y", "-i", video_path,
            "-vf", drawtext,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-c:a", "copy", temp_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            if process.returncode == 0 and os.path.exists(temp_path):
                os.remove(video_path)
                os.rename(temp_path, video_path)
                self.log(f"[{instance_id}] ✅ Title burned!")
            else:
                err = stderr.decode('utf-8', errors='ignore')[-300:]
                self.log_error(f"[{instance_id}] Title burn failed: {err}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
        except FileNotFoundError:
            self.log_error(f"[{instance_id}] ffmpeg not found — title NOT burned.")
        except Exception as e:
            self.log_error(f"[{instance_id}] Title burn error: {e}")

    async def batch_manager(self):

        sem       = asyncio.Semaphore(self.concurrency)
        completed = 0
        successes = 0
        active    = 0

        self.log("🚀 Direct API mode — no browser window needed")
        self.log(f"   Duration: {self.duration}s  |  Concurrency: {self.concurrency}")

        async def worker(instance_id):
            nonlocal completed, successes, active
            async with sem:
                if self._is_stopped:
                    return

                # Pre-delay
                for _ in range(self.next_delay):
                    if self._is_stopped:
                        return
                    await asyncio.sleep(1)

                # Mark active
                active += 1
                failed_cnt = completed - successes
                queued_cnt = self.total - completed - active
                self.stats_signal.emit(self.total, queued_cnt, active, successes, failed_cnt)

                data        = self.prompt_data[(instance_id - 1) % len(self.prompt_data)]
                prompt_text = data.get("prompt", "")
                caption     = data.get("caption", None)

                self.log(f"[{instance_id}] ▶ '{prompt_text[:50]}'")

                success = False
                try:
                    success = await self._run_single(instance_id, prompt_text, caption)
                except Exception as e:
                    self.log_error(f"[{instance_id}] Unexpected error: {e}")

                active -= 1
                completed += 1
                if success:
                    successes += 1
                else:
                    self._failed_prompts.append(data)

                failed = completed - successes
                queued = self.total - completed - active
                self.progress_signal.emit(completed, self.total, successes)
                self.stats_signal.emit(self.total, queued, active, successes, failed)

        tasks = []
        for i in range(1, self.total + 1):
            if self._is_stopped:
                break
            tasks.append(asyncio.create_task(worker(i)))
            # Stagger starts
            for _ in range(self.start_delay):
                if self._is_stopped:
                    break
                await asyncio.sleep(1)

        if tasks:
            await asyncio.gather(*tasks)

        self.log(f"✅ Batch done: {successes}/{self.total} succeeded")


class DolaVideoGenPage(QWidget):
    """
    Dola Browser Bot page — full browser-based video generation.
    Extracted from the original bot_ui_pyqt6.py ModernUI class.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._start_time = 0
        self._timer_seconds = 0
        self._last_process_events = 0  # Throttle processEvents calls
        self._error_stats = {"timeout": 0, "captcha": 0, "policy": 0, "textbox": 0, "high_demand": 0}
        self._stats_tracker = StatsTracker.instance()

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_timer)

        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # Status Badge
        self._status_badge = QLabel("● READY")
        self._status_badge.setStyleSheet("color: #52b788; font-weight: 700; font-size: 10px;")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Config Card
        root.addWidget(self._build_config_card())

        # Flood Mode Card

        # Stats Card
        root.addWidget(self._build_stats_card())

        # Console Card
        root.addWidget(self._build_console_card())

        # Action Buttons
        root.addLayout(self._build_actions())

        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _build_config_card(self):
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # ── Output Folder ──
        lay.addWidget(_label("📁  OUTPUT FOLDER", "section_title"))
        folder_row = QHBoxLayout()
        self._folder_entry = QLineEdit()
        self._folder_entry.setPlaceholderText("Select output folder...")
        self._folder_entry.setReadOnly(True)
        folder_row.addWidget(self._folder_entry)
        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("btn_browse")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        lay.addLayout(folder_row)

        naming_row = QHBoxLayout()
        naming_row.addWidget(QLabel("File Naming:"))
        self._naming_combo = QComboBox()
        self._naming_combo.addItems(["Title On Video", "Title in Text File"])
        naming_row.addWidget(self._naming_combo)
        naming_row.addStretch()
        lay.addLayout(naming_row)

        lay.addWidget(_hline())

        # ── Prompts ──
        prompt_hdr = QHBoxLayout()
        prompt_hdr.addWidget(_label("📝  PROMPTS", "section_title"))
        
        prompt_hdr.addSpacing(16)
        prompt_hdr.addWidget(QLabel("Separator:"))
        self._separator_combo = QComboBox()
        self._separator_combo.addItems(["Double Newline", "Single Newline", "---", "***"])
        self._separator_combo.currentTextChanged.connect(lambda: self._update_prompt_count())
        prompt_hdr.addWidget(self._separator_combo)
        
        prompt_hdr.addStretch()
        self._prompt_count_lbl = QLabel("0 prompts")
        self._prompt_count_lbl.setStyleSheet("color:#52b788; font-size:11px; font-weight:700;")
        prompt_hdr.addWidget(self._prompt_count_lbl)
        lay.addLayout(prompt_hdr)
        
        file_row = QHBoxLayout()
        self._prompt_file_input = QLineEdit()
        self._prompt_file_input.setPlaceholderText("Browse a prompt file or CSV...")
        self._prompt_file_input.setReadOnly(True)
        file_row.addWidget(self._prompt_file_input, 1)
        
        csv_btn = QPushButton("📄 CSV")
        csv_btn.setMinimumWidth(62)
        csv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        csv_btn.clicked.connect(self._load_csv_file)
        file_row.addWidget(csv_btn)
        
        txt_btn = QPushButton("📂 TXT")
        txt_btn.setMinimumWidth(62)
        txt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        txt_btn.clicked.connect(self._load_txt_file)
        file_row.addWidget(txt_btn)
        lay.addLayout(file_row)

        self._prompt_entry = QTextEdit()
        self._prompt_entry.setPlaceholderText(
            'Or paste your prompts here...\n\n'
            'Plain text: Separate each prompt with 2 blank lines.\n'
            'CSV Format: "Prompt text", "Caption text" (1 per line)'
        )
        self._prompt_entry.setMinimumHeight(80)
        self._prompt_entry.setMaximumHeight(120)
        self._prompt_entry.textChanged.connect(self._update_prompt_count)
        lay.addWidget(self._prompt_entry)

        lay.addWidget(_hline())

        # ── Settings Grid ──
        lay.addWidget(_label("⚙️  GENERATION SETTINGS", "section_title"))

        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)

        # Row 0: Labels for Row 1
        grid.addWidget(QLabel("Duration"), 0, 0)
        grid.addWidget(QLabel("Concurrent Threads"), 0, 1)
        grid.addWidget(QLabel("Timeout (min)  ⓘ"), 0, 2)
        grid.addWidget(QLabel("Process Start (s)  ⓘ"), 0, 3)

        # Row 1: Inputs
        self._dur_combo = QComboBox()
        self._dur_combo.addItems(["15s", "10s", "5s"])
        grid.addWidget(self._dur_combo, 1, 0)

        self._conc_entry = QLineEdit()
        self._conc_entry.setText("20")
        grid.addWidget(self._conc_entry, 1, 1)

        self._timeout_entry = QLineEdit()
        self._timeout_entry.setText("30")
        self._timeout_entry.textChanged.connect(self._update_dynamic_timeout)
        self._timeout_entry.setToolTip(
            "Stage 2 Timeout: How long to wait for video URL to appear.\n"
            "If video is not found in this time → prompt marked as failed."
        )
        grid.addWidget(self._timeout_entry, 1, 2)

        self._process_start_entry = QLineEdit()
        self._process_start_entry.setText("80")
        self._process_start_entry.setToolTip(
            "Stage 1 Timeout: How long to wait for Dola to START processing.\n"
            "If no generation activity detected in this time → browser auto-closes.\n"
            "Default: 80s. Increase if your internet is slow."
        )
        grid.addWidget(self._process_start_entry, 1, 3)

        # Add vertical spacing between grid rows
        grid.setRowMinimumHeight(1, grid.rowMinimumHeight(1) + 6)

        # Row 2: Labels for Row 3
        grid.addWidget(QLabel("Start Delay (s)"), 2, 0)
        grid.addWidget(QLabel("Next Task Delay (s)"), 2, 1)
        grid.addWidget(QLabel("Watermark Removal"), 2, 2)
        grid.addWidget(QLabel("Aspect Ratio"), 2, 3)

        # Row 3: Inputs
        self._delay_entry = QLineEdit()
        self._delay_entry.setText("5")
        grid.addWidget(self._delay_entry, 3, 0)

        self._next_delay_entry = QLineEdit()
        self._next_delay_entry.setText("5")
        grid.addWidget(self._next_delay_entry, 3, 1)

        self._watermark_combo = QComboBox()
        self._watermark_combo.addItems(["Blur (Delogo)", "Crop", "None"])
        grid.addWidget(self._watermark_combo, 3, 2)

        self._ratio_combo = QComboBox()
        self._ratio_combo.addItems(["9:16", "16:9"])
        self._ratio_combo.setCurrentText("9:16")
        self._ratio_combo.setToolTip(
            "Video aspect ratio:\n"
            "1:1  — Square (Instagram post)\n"
            "3:4  — Portrait\n"
            "4:3  — Classic landscape\n"
            "9:16 — Vertical (TikTok, Reels, Shorts)\n"
            "16:9 — Horizontal (YouTube, landscape)\n"
            "21:9 — Ultrawide cinematic"
        )
        grid.addWidget(self._ratio_combo, 3, 3)

        lay.addLayout(grid)

        # ── Timeout Info Panel ──
        timeout_info = QFrame()
        timeout_info.setStyleSheet(
            "QFrame { background: rgba(96,165,250,0.07); border: 1px solid rgba(96,165,250,0.18); border-radius: 6px; }"
        )
        ti_lay = QVBoxLayout(timeout_info)
        ti_lay.setContentsMargins(12, 8, 12, 8)
        ti_lay.setSpacing(4)

        ti_title = QLabel("⏱  How Timeout Works")
        ti_title.setStyleSheet("color: #60a5fa; font-size: 10px; font-weight: 700; background: transparent; border: none;")
        ti_lay.addWidget(ti_title)

        stage1_row = QHBoxLayout()
        s1_badge = QLabel("STAGE 1")
        s1_badge.setStyleSheet(
            "color: #f59e0b; font-size: 9px; font-weight: 700; "
            "background: rgba(245,158,11,0.15); border: 1px solid rgba(245,158,11,0.3); "
            "border-radius: 3px; padding: 1px 5px;"
        )
        s1_badge.setFixedWidth(52)
        self._stage1_info_lbl = QLabel("80s - waits for Dola to START processing your prompt")
        self._stage1_info_lbl.setStyleSheet("color: #94a3b8; font-size: 10px; background: transparent; border: none;")
        stage1_row.addWidget(s1_badge)
        stage1_row.addSpacing(6)
        stage1_row.addWidget(self._stage1_info_lbl)
        stage1_row.addStretch()
        ti_lay.addLayout(stage1_row)

        stage2_row = QHBoxLayout()
        s2_badge = QLabel("STAGE 2")
        s2_badge.setStyleSheet(
            "color: #10b981; font-size: 9px; font-weight: 700; "
            "background: rgba(16,185,129,0.15); border: 1px solid rgba(16,185,129,0.3); "
            "border-radius: 3px; padding: 1px 5px;"
        )
        s2_badge.setFixedWidth(52)
        self._timeout_info_lbl = QLabel("30 min — waits for video URL to appear (your Timeout setting)")
        self._timeout_info_lbl.setStyleSheet("color: #94a3b8; font-size: 10px; background: transparent; border: none;")
        stage2_row.addWidget(s2_badge)
        stage2_row.addSpacing(6)
        stage2_row.addWidget(self._timeout_info_lbl)
        stage2_row.addStretch()
        ti_lay.addLayout(stage2_row)

        fail_row = QHBoxLayout()
        f_badge = QLabel("FAIL")
        f_badge.setStyleSheet(
            "color: #ef4444; font-size: 9px; font-weight: 700; "
            "background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); "
            "border-radius: 3px; padding: 1px 5px;"
        )
        f_badge.setFixedWidth(52)
        f_desc = QLabel("If video not found in Stage 2 time → browser closes, prompt marked as failed")
        f_desc.setStyleSheet("color: #94a3b8; font-size: 10px; background: transparent; border: none;")
        fail_row.addWidget(f_badge)
        fail_row.addSpacing(6)
        fail_row.addWidget(f_desc)
        fail_row.addStretch()
        ti_lay.addLayout(fail_row)

        lay.addWidget(timeout_info)

        # Connect timeout fields to update info labels live
        self._timeout_entry.textChanged.connect(self._update_timeout_info_label)
        self._process_start_entry.textChanged.connect(self._update_stage1_info_label)

        lay.addSpacing(8)

        # Row 4: Toggles
        row3 = QHBoxLayout()
        row3.setSpacing(24)

        # NOTE: Loop, Mobile Emulation, and Hide Browser are all hidden
        self._loop_cb = QCheckBox("Auto-Loop Batch")
        self._loop_cb.setChecked(False)
        self._loop_cb.hide()

        # Browser is always headless (hidden) — no user toggle needed
        self._headless_cb = QCheckBox("Hide Browser")
        self._headless_cb.setChecked(True)
        self._headless_cb.hide()

        self._mobile_cb = QCheckBox("Mobile Emulation")
        self._mobile_cb.setChecked(False)
        self._mobile_cb.hide()

        row3.addStretch()
        lay.addLayout(row3)

        lay.addWidget(_hline())

        # ── Settings Redirect Banner ──
        redirect_frame = QFrame()
        redirect_frame.setStyleSheet(
            "QFrame { background: rgba(96,165,250,0.07); "
            "border: 1px solid rgba(96,165,250,0.18); border-radius: 8px; }"
        )
        rd_lay = QHBoxLayout(redirect_frame)
        rd_lay.setContentsMargins(14, 10, 14, 10)
        rd_icon = QLabel("⚙️")
        rd_icon.setStyleSheet("font-size:18px; background:transparent; border:none;")
        rd_lay.addWidget(rd_icon)
        rd_text = QLabel(
            "<b style='color:#60a5fa'>Cookies &amp; Proxy settings</b> "
            "<span style='color:#64748b'>have been moved to the "
            "<b style='color:#f59e0b'>⚙️ Settings</b> page in the sidebar. "
            "Add cookie accounts there for Flood Mode &amp; batch rotation.</span>"
        )
        rd_text.setWordWrap(True)
        rd_text.setStyleSheet("background:transparent; border:none; font-size:11px;")
        rd_lay.addWidget(rd_text, 1)
        lay.addWidget(redirect_frame)

        return card


    def _build_stats_card(self):
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(_label("ENGINE TELEMETRY", "section_title"))
        title_row.addStretch()
        title_row.addWidget(self._status_badge)
        lay.addLayout(title_row)
        lay.addWidget(_hline())

        from PyQt6.QtWidgets import QGridLayout
        
        grid = QGridLayout()
        grid.setSpacing(12)
        
        def _create_badge(dot_color, text, obj_name):
            w = QFrame()
            w.setObjectName("stat_badge")
            w_lay = QHBoxLayout(w)
            w_lay.setContentsMargins(12, 10, 12, 10)
            w_lay.setSpacing(8)
            
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {dot_color}; font-size: 11px; background: transparent; border: none;")
            w_lay.addWidget(dot)
            
            lbl = QLabel(text)
            lbl.setObjectName("stat_badge_label")
            w_lay.addWidget(lbl)
            
            w_lay.addStretch()
            
            val = QLabel("0")
            val.setObjectName("stat_badge_value")
            w_lay.addWidget(val)
            
            setattr(self, obj_name, val)
            return w

        # Make columns stretch equally
        for i in range(5):
            grid.setColumnStretch(i, 1)

        # Row 1: Core Stats
        grid.addWidget(_create_badge("#a5b4fc", "Total", "_total_lbl"), 0, 0)
        grid.addWidget(_create_badge("#60a5fa", "Queued", "_queued_lbl"), 0, 1)
        grid.addWidget(_create_badge("#d97706", "Generating", "_active_lbl"), 0, 2)
        grid.addWidget(_create_badge("#10b981", "Done", "_ok_lbl"), 0, 3)
        grid.addWidget(_create_badge("#ef4444", "Failed", "_fail_lbl"), 0, 4)

        # Row 2: Error Stats
        grid.addWidget(_create_badge("#f59e0b", "Timeout Error", "_timeout_lbl"), 1, 0)
        grid.addWidget(_create_badge("#ef4444", "Captcha Block", "_captcha_lbl"), 1, 1)
        grid.addWidget(_create_badge("#ec4899", "Dola Policy", "_policy_lbl"), 1, 2)
        grid.addWidget(_create_badge("#8b5cf6", "No Textbox", "_textbox_lbl"), 1, 3)
        grid.addWidget(_create_badge("#eab308", "High Demand", "_high_demand_lbl"), 1, 4)

        lay.addLayout(grid)
        lay.addSpacing(6)

        progress_col = QHBoxLayout()
        self._timer_lbl = QLabel("⏱ 00:00:00")
        self._timer_lbl.setStyleSheet("color: #7b86a5; font-size: 11px;")
        progress_col.addWidget(self._timer_lbl)
        
        self._eta_lbl = QLabel("ETA: --:--")
        self._eta_lbl.setStyleSheet("color: #60a5fa; font-size: 11px; font-weight: 600;")
        progress_col.addWidget(self._eta_lbl)
        
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        progress_col.addWidget(self._progress_bar)
        
        self._progress_pct = QLabel("0%")
        self._progress_pct.setStyleSheet("color: #7b86a5; font-size: 11px;")
        progress_col.addWidget(self._progress_pct)
        
        lay.addLayout(progress_col)

        # Total videos counter
        total_row = QHBoxLayout()
        total_count = load_settings().get("total_videos_generated", 0)
        self._lifetime_lbl = QLabel(f"🎬 Lifetime: {total_count:,} videos generated")
        self._lifetime_lbl.setStyleSheet("color: #52b788; font-size: 11px; font-weight: 600;")
        total_row.addWidget(self._lifetime_lbl)
        total_row.addStretch()
        lay.addLayout(total_row)
        
        return card

    def _build_console_card(self):
        card = _card()
        card.setMinimumHeight(400)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)

        hdr = QHBoxLayout()
        hdr.addWidget(_label("TERMINAL OUTPUT", "section_title"))
        hdr.addStretch()

        clr = QPushButton("Clear")
        clr.setObjectName("btn_secondary")
        clr.clicked.connect(lambda: self._console.clear())
        hdr.addWidget(clr)
        lay.addLayout(hdr)

        self._console = QTextEdit()
        self._console.setObjectName("console")
        self._console.setReadOnly(True)
        self._console.setMinimumHeight(150)
        lay.addWidget(self._console)

        # Error console
        err_hdr = QHBoxLayout()
        err_hdr.addWidget(_label("ERROR LOG", "section_title"))
        err_hdr.addStretch()
        clr_err = QPushButton("Clear")
        clr_err.setObjectName("btn_secondary")
        clr_err.clicked.connect(lambda: self._error_console.clear())
        err_hdr.addWidget(clr_err)
        lay.addLayout(err_hdr)

        self._error_console = QTextEdit()
        self._error_console.setObjectName("error_console")
        self._error_console.setReadOnly(True)
        self._error_console.setMinimumHeight(100)
        self._error_console.setMaximumHeight(150)
        lay.addWidget(self._error_console)

        return card

    def _build_actions(self):
        row = QHBoxLayout()
        self._start_btn = QPushButton("⚡   LAUNCH BATCH GENERATION")
        self._start_btn.setObjectName("btn_start")
        self._start_btn.setFixedHeight(44)
        self._start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_btn.clicked.connect(self._start_batch)
        row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("🛑   STOP")
        self._stop_btn.setObjectName("btn_stop")
        self._stop_btn.setFixedHeight(44)
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.clicked.connect(self._stop_batch)
        self._stop_btn.setEnabled(False)
        row.addWidget(self._stop_btn)

        self._retry_btn = QPushButton("🔄 Retry Failed")
        self._retry_btn.setObjectName("btn_secondary")
        self._retry_btn.setFixedHeight(44)
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.clicked.connect(self._retry_failed)
        self._retry_btn.setEnabled(False)
        self._retry_btn.setToolTip("Retry all failed prompts from the last batch")
        row.addWidget(self._retry_btn)

        open_btn = QPushButton("📂 Open Folder")
        open_btn.setObjectName("btn_secondary")
        open_btn.setFixedHeight(44)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._open_folder)
        row.addWidget(open_btn)

        export_btn = QPushButton("📋 Export Logs")
        export_btn.setObjectName("btn_secondary")
        export_btn.setFixedHeight(44)
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._export_logs)
        row.addWidget(export_btn)

        row.addStretch()
        return row

    # ─── Settings Persistence ──────────────────────────

    def _get_safe_default_folder(self):
        """Return a safe writable default output folder on the user's Desktop."""
        desktop = os.path.join(os.path.expanduser("~"), "Desktop", "ZakariyaAutomator Videos")
        return desktop

    def _load_settings(self):
        s = load_settings()
        ud = load_user_data()
        self._folder_entry.setText("")  # Do not load saved location
        self._conc_entry.setText(s.get("concurrency", "10"))
        self._dur_combo.setCurrentText(s.get("duration", "15s"))
        self._ratio_combo.setCurrentText(s.get("ratio", "9:16"))
        self._timeout_entry.setText(s.get("timeout_min", "30"))
        self._process_start_entry.setText(s.get("process_start_timeout", "60"))
        self._delay_entry.setText(s.get("start_delay", "5"))
        self._next_delay_entry.setText(s.get("next_delay", "5"))
        self._headless_cb.setChecked(s.get("show_browser", True))
        self._watermark_combo.setCurrentText(s.get("watermark_mode", "Blur (Delogo)"))
        self._loop_cb.setChecked(s.get("auto_loop", False))
        self._mobile_cb.setChecked(False)

        # Load large data from user_data
        self._prompt_entry.setText("")  # Do not load saved prompt
        # (Cookies and Proxies are managed by SettingsPage)

    def _save_settings(self):
        # Save small UI settings
        s = load_settings()
        s["concurrency"] = self._conc_entry.text()
        s["duration"] = self._dur_combo.currentText()
        s["ratio"] = self._ratio_combo.currentText()
        s["timeout_min"] = self._timeout_entry.text()
        s["process_start_timeout"] = self._process_start_entry.text()
        s["start_delay"] = self._delay_entry.text()
        s["next_delay"] = self._next_delay_entry.text()
        s["show_browser"] = self._headless_cb.isChecked()
        s["watermark_mode"] = self._watermark_combo.currentText()
        s["auto_loop"] = self._loop_cb.isChecked()
        s["mobile_mode"] = self._mobile_cb.isChecked()
        # Proxy and Cookies are now managed by SettingsPage — not saved here
        save_settings(s)

    # ─── Actions ───────────────────────────────────────

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self._folder_entry.text())
        if folder:
            self._folder_entry.setText(folder)
            self._save_settings()

    def _browse_tor_exe(self):
        """Open file dialog to locate tor.exe."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select tor.exe", "", "Tor Executable (tor.exe);;All Files (*)"
        )
        if filepath:
            self._tor_exe_entry.setText(filepath)
            self._save_settings()

    def _on_tor_toggle(self, state):
        """Enable/disable Tor controls based on checkbox state."""
        enabled = bool(state)
        self._set_tor_controls_enabled(enabled)
        if enabled:
            # Auto-fill bundled tor.exe path if current entry is empty/invalid/default
            from core.tor_manager import TorManager as _TorMgr
            import os as _os
            current = self._tor_exe_entry.text().strip()
            if not current or not _os.path.isfile(current):
                resolved = _TorMgr.resolve_tor_exe("tor.exe")
                self._tor_exe_entry.setText(resolved)
                if _os.path.isfile(resolved):
                    self._log(f"[+] Tor: bundled tor.exe found → {resolved}", "success")
                else:
                    self._log(
                        "[!] Tor: bundled tor.exe not found. "
                        "Please set path to tor.exe manually.", "warn"
                    )
            # Warn if proxy list is non-empty
            if self._proxy_entry.toPlainText().strip():
                self._log(
                    "[i] Tor mode enabled — manual proxy list will be IGNORED while Tor is active.",
                    "warn"
                )
        self._save_settings()

    def _set_tor_controls_enabled(self, enabled: bool):
        """Enable or disable Tor-specific controls."""
        # Path field: editable when enabled (so advanced users can override)
        self._tor_exe_entry.setEnabled(enabled)
        self._tor_exe_entry.setReadOnly(False)  # Always editable when visible
        self._tor_base_port_entry.setEnabled(enabled)

    def _open_folder(self):
        path = self._folder_entry.text().strip()
        if path and os.path.exists(path):
            os.startfile(path)

    def _export_logs(self):
        """Export both console and error logs to a timestamped text file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"dola_logs_{timestamp}.txt"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Logs", default_name, "Text Files (*.txt);;All Files (*)"
        )
        if filepath:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write("=" * 60 + "\n")
                    f.write(f"  Zakariya Automator — Log Export\n")
                    f.write(f"  Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("=" * 60 + "\n\n")
                    f.write("── TERMINAL OUTPUT ──\n\n")
                    f.write(self._console.toPlainText())
                    f.write("\n\n── ERROR LOG ──\n\n")
                    f.write(self._error_console.toPlainText())
                self._log(f"[+] Logs exported to: {filepath}", "success")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not export logs:\n{e}")

    def _retry_failed(self):
        """Re-run all failed prompts from the last batch."""
        if not self._worker or not self._worker._failed_prompts:
            self._log("[-] No failed prompts to retry.", "warn")
            return

        failed_prompts = list(self._worker._failed_prompts)
        self._log(f"[*] Retrying {len(failed_prompts)} failed prompts...", "info")

        # Put failed prompts into the prompt box and restart
        retry_text = "\n\n".join([p.get("prompt", "") for p in failed_prompts])
        self._prompt_entry.setText(retry_text)
        self._retry_btn.setEnabled(False)
        self._retry_btn.setText("🔄 Retry Failed")

        # Auto-start the batch
        QTimer.singleShot(500, self._start_batch)

    def _load_csv_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open CSV File", "", "CSV Files (*.csv);;Text Files (*.txt);;All Files (*)"
        )
        if filepath:
            self._prompt_file_input.setText(filepath)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self._prompt_entry.setText(content)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not read file:\n{e}")

    def _load_txt_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open TXT File", "", "Text Files (*.txt);;All Files (*)"
        )
        if filepath:
            self._prompt_file_input.setText(filepath)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self._prompt_entry.setText(content)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not read file:\n{e}")

    def _parse_prompts(self, text):
        import re
        if not text:
            return []

        parsed_data = []

        is_csv = False
        first_line = text.split('\n')[0].lower()

        if 'prompt,' in first_line or ',title' in first_line or ',caption' in first_line or ',seo' in first_line:
            is_csv = True
        elif '",' in text or '","' in text or '", "' in text:
            is_csv = True

        if is_csv:
            reader = csv.reader(io.StringIO(text))
            for row in reader:
                if not row:
                    continue
                if row[0].strip().lower() in ['video prompt', 'prompt']:
                    continue
                if len(row) >= 2:
                    parsed_data.append({"prompt": row[0].strip(), "caption": row[1].strip()})
                elif len(row) == 1 and row[0].strip():
                    parsed_data.append({"prompt": row[0].strip(), "caption": None})
        else:
            separator = self._separator_combo.currentText() if hasattr(self, '_separator_combo') else "Double Newline"
            if separator == "Double Newline":
                raw_prompts = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
            elif separator == "Single Newline":
                raw_prompts = [p.strip() for p in text.split('\n') if p.strip()]
            elif separator == "---":
                raw_prompts = [p.strip() for p in text.split('---') if p.strip()]
            elif separator == "***":
                raw_prompts = [p.strip() for p in text.split('***') if p.strip()]
            else:
                raw_prompts = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

            for p in raw_prompts:
                parsed_data.append({"prompt": p, "caption": None})

        return parsed_data

    def _update_prompt_count(self):
        text = self._prompt_entry.toPlainText().strip()
        data = self._parse_prompts(text)
        self._prompt_count_lbl.setText(f"✓  {len(data)} prompt(s)")

    def _update_proxy_count(self):
        text = self._proxy_entry.toPlainText().strip()
        proxies = [p.strip() for p in text.splitlines() if p.strip()]
        self._proxy_count_lbl.setText(f"✓  {len(proxies)} proxy(ies)")

    def _load_proxy_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open Proxy File", "", "Text Files (*.txt);;All Files (*)"
        )
        if filepath:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self._proxy_entry.setText(content)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not read proxy file:\n{e}")

    # Noisy patterns to filter from terminal (internal bot details)
    _LOG_SUPPRESS = [
        "[V7 Route]", "[V7]", "[V7 JS",
        "... still waiting ...",
        "... still checking for process start",
        "Clicked duration trigger", "Clicked ratio trigger",
        "Selected 10s duration", "Selected 9:16",
        "Page interactive element found",
        "Network idle timeout",
        "Send button fallback error",
        "Could not set duration", "Could not set ratio",
        "Could not click Video tab",
        "Clicked Video mode using", "Clicked 'More' menu", "Video tab clicked via JS",
        "Desktop UA:", "Mobile emulation:",
        "No popups detected",
        "Auto-dismissed popup", "Auto-dismissed dialog",
        "Playwright route interception",
        "Clean prompt:",
        "Checking if generation process",
        "Checking for confirmation popups",
        "Selecting 'Video' tab", "Selecting 10s duration", "Selecting aspect ratio",
        "Clicked Send button as fallback",
        "Re-pressing Enter",
    ]

    def _log(self, message, level="info"):
        # Always check error tally even for filtered messages
        self._check_error_tally(message)

        # Suppress noisy internal messages from terminal display
        for pattern in self._LOG_SUPPRESS:
            if pattern in message:
                return

        colors = {"info": "#94a3b8", "success": "#10b981", "error": "#ef4444", "warn": "#f59e0b"}
        color = colors.get(level, "#94a3b8")
        timestamp = datetime.now().strftime("%H:%M:%S")
        cursor = self._console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat()
        fmt.setForeground(QColor("#475569"))
        cursor.setCharFormat(fmt)
        cursor.insertText(f"[{timestamp}] ")
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(f"{message}\n")
        self._console.setTextCursor(cursor)
        self._console.ensureCursorVisible()

        # Auto-trim console to prevent memory leak (keep last 2000 lines)
        if self._console.document().blockCount() > 2000:
            tc = self._console.textCursor()
            tc.movePosition(QTextCursor.MoveOperation.Start)
            tc.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, 500)
            tc.removeSelectedText()

        # Throttle processEvents to max 10 calls/second
        now = time.time()
        if now - self._last_process_events > 0.1:
            self._last_process_events = now
            QApplication.processEvents()

    def _log_error(self, message):
        self._error_console.append(message)
        scrollbar = self._error_console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._check_error_tally(message)

        # Auto-trim error console
        if self._error_console.document().blockCount() > 500:
            tc = self._error_console.textCursor()
            tc.movePosition(QTextCursor.MoveOperation.Start)
            tc.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, 200)
            tc.removeSelectedText()

        now = time.time()
        if now - self._last_process_events > 0.1:
            self._last_process_events = now
            QApplication.processEvents()

    def _check_error_tally(self, message):
        changed = False
        if "ERROR: Failed to get video URL within timeout" in message:
            self._error_stats["timeout"] += 1
            changed = True
        elif "ERROR: Blocked by Cloudflare or Captcha" in message:
            self._error_stats["captcha"] += 1
            changed = True
        elif "ERROR: Dola refused" in message:
            self._error_stats["policy"] += 1
            changed = True
        elif "Textbox not found!" in message:
            self._error_stats["textbox"] += 1
            changed = True
        elif "rate-limited" in message or "high demand" in message:
            self._error_stats["high_demand"] += 1
            changed = True

        if changed:
            self._timeout_lbl.setText(str(self._error_stats['timeout']))
            self._captcha_lbl.setText(str(self._error_stats['captcha']))
            self._policy_lbl.setText(str(self._error_stats['policy']))
            self._textbox_lbl.setText(str(self._error_stats['textbox']))
            self._high_demand_lbl.setText(str(self._error_stats['high_demand']))

    # ─── Progress & Timer ──────────────────────────────

    def _update_progress(self, completed, total, successes):
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(completed)
        pct = int(completed * 100 / total) if total > 0 else 0
        self._progress_pct.setText(f"{pct}%")

        # Update ETA
        if completed > 0 and self._start_time > 0:
            elapsed = time.time() - self._start_time
            avg_per_video = elapsed / completed
            remaining = total - completed
            eta_seconds = int(avg_per_video * remaining)
            eta_m, eta_s = divmod(eta_seconds, 60)
            eta_h, eta_m = divmod(eta_m, 60)
            if eta_h > 0:
                self._eta_lbl.setText(f"ETA: {eta_h}h {eta_m}m")
            else:
                self._eta_lbl.setText(f"ETA: {eta_m}m {eta_s}s")
        else:
            self._eta_lbl.setText("ETA: --:--")

        # Update lifetime counter
        s = load_settings()
        lifetime = s.get("total_videos_generated", 0) + (1 if successes > getattr(self, '_last_successes', 0) else 0)
        s["total_videos_generated"] = lifetime
        self._last_successes = successes
        save_settings(s)
        self._lifetime_lbl.setText(f"🎬 Lifetime: {lifetime:,} videos generated")

        # Update stats tracker with progress
        failed = completed - successes
        self._stats_tracker.update_progress(successes=successes, failures=failed)

    def _on_stats(self, total, queued, active, ok, failed):
        self._total_lbl.setText(str(total))
        self._queued_lbl.setText(str(queued))
        self._active_lbl.setText(str(active))
        self._ok_lbl.setText(str(ok))
        self._fail_lbl.setText(str(failed))

    def _update_timer(self):
        self._timer_seconds += 1
        hours, remainder = divmod(self._timer_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._timer_lbl.setText(f"⏱ {hours:02d}:{minutes:02d}:{seconds:02d}")

    def _update_dynamic_timeout(self, text):
        if self._worker:
            try:
                mins = int(text)
                self._worker.current_timeout = mins * 60
                self._log(f"[*] Dynamic Timeout updated to {mins} min for next tasks.", "info")
            except ValueError:
                pass

    def _update_timeout_info_label(self, text):
        """Update the Stage 2 label in the timeout info panel live."""
        try:
            mins = int(text)
            self._timeout_info_lbl.setText(
                f"{mins} min — waits for video URL to appear (your Timeout setting)"
            )
        except (ValueError, AttributeError):
            pass

    def _update_stage1_info_label(self, text):
        """Update the Stage 1 label in the timeout info panel live."""
        try:
            secs = int(text)
            self._stage1_info_lbl.setText(
                f"{secs}s — waits for Dola to START processing your prompt"
            )
        except (ValueError, AttributeError):
            pass

    # ─── Batch Control ─────────────────────────────────


    def _stop_batch(self):
        if self._worker and not self._worker._is_stopped:
            self._log("🛑 Stop requested. Waiting for active tasks to finish...", "warn")
            self._worker.stop()
            self._stop_btn.setEnabled(False)
            self._status_badge.setText("● STOPPING")
            self._status_badge.setStyleSheet("color: #e76f51; font-weight: 700; font-size: 10px;")

    def _batch_finished(self):
        self._elapsed_timer.stop()
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._eta_lbl.setText("ETA: Done!")
        self._status_badge.setText("● READY")
        self._status_badge.setStyleSheet("color: #52b788; font-weight: 700; font-size: 10px;")
        elapsed = time.time() - self._start_time
        mins, secs = divmod(int(elapsed), 60)

        # End stats session with error breakdown
        self._stats_tracker.end_session(error_stats=self._error_stats)

        # Check for failed prompts and enable retry
        failed_count = 0
        if self._worker and self._worker._failed_prompts:
            failed_count = len(self._worker._failed_prompts)
            self._retry_btn.setEnabled(True)
            self._retry_btn.setText(f"🔄 Retry Failed ({failed_count})")
        else:
            self._retry_btn.setEnabled(False)
            self._retry_btn.setText("🔄 Retry Failed")

        self._log(f"\n🎉 BATCH COMPLETED in {mins}m {secs}s!", "success")
        if failed_count > 0:
            self._log(f"⚠️ {failed_count} prompts failed. Click 'Retry Failed' to re-run them.", "warn")

        # Desktop notification (Windows)
        try:
            from ctypes import windll
            # Use PowerShell toast notification
            msg = f"Batch done! {mins}m {secs}s"
            if failed_count:
                msg += f" ({failed_count} failed)"
            subprocess.Popen(
                ['powershell', '-Command',
                 f'[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null; '
                 f'$n = New-Object System.Windows.Forms.NotifyIcon; '
                 f'$n.Icon = [System.Drawing.SystemIcons]::Information; '
                 f'$n.Visible = $true; '
                 f'$n.ShowBalloonTip(5000, "Zakariya Automator", "{msg}", "Info"); '
                 f'Start-Sleep 6; $n.Dispose()'],
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
        except Exception:
            pass

        if self._loop_cb.isChecked():
            self._log("🔄 Auto-Loop enabled! Starting same batch again in 5 seconds...", "info")
            QTimer.singleShot(5000, self._start_batch)
        else:
            QMessageBox.information(self, "Success", f"All prompts processed in {mins}m {secs}s!")

    def _start_batch(self):
        text = self._prompt_entry.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Error", "Prompt box is empty! Please enter prompts.")
            return

        out_dir = self._folder_entry.text().strip()

        # ── Check for protected paths (e.g. Program Files) ──
        protected_paths = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
            os.environ.get("SystemRoot", "C:\\Windows"),
        ]
        if out_dir and any(out_dir.lower().startswith(p.lower()) for p in protected_paths if p):
            safe_default = self._get_safe_default_folder()
            reply = QMessageBox.warning(
                self, "Protected Folder Detected",
                f"The selected folder is inside a Windows protected directory:\n\n"
                f"  {out_dir}\n\n"
                f"Files cannot be saved there without Administrator rights.\n\n"
                f"Switch to safe default folder?\n  {safe_default}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                out_dir = safe_default
                self._folder_entry.setText(out_dir)
            else:
                return

        # ── If no folder selected, use safe default ──
        if not out_dir:
            out_dir = self._get_safe_default_folder()
            self._folder_entry.setText(out_dir)
            self._log(f"[*] No folder selected — using default: {out_dir}", "warn")

        # ── Auto-create folder if it doesn't exist ──
        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
                self._log(f"[+] Created output folder: {out_dir}", "success")
            except PermissionError:
                QMessageBox.critical(
                    self, "Permission Denied",
                    f"Cannot create folder:\n{out_dir}\n\nPlease select a different folder."
                )
                return
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not create output folder:\n{e}")
                return

        # Reset stats
        self._error_stats = {"timeout": 0, "captcha": 0, "policy": 0, "textbox": 0, "high_demand": 0}
        self._timeout_lbl.setText("0")
        self._captcha_lbl.setText("0")
        self._policy_lbl.setText("0")
        self._textbox_lbl.setText("0")
        self._high_demand_lbl.setText("0")
        self._total_lbl.setText("0")
        self._queued_lbl.setText("0")
        self._active_lbl.setText("0")
        self._ok_lbl.setText("0")
        self._fail_lbl.setText("0")

        # Parse proxy list from Settings page if available
        if hasattr(self, '_settings_page_ref') and self._settings_page_ref:
            proxy_list = self._settings_page_ref.get_proxy_list()
        else:
            proxy_list = []
        mobile_mode = self._mobile_cb.isChecked()

        prompt_data = self._parse_prompts(text)

        try:
            total = len(prompt_data)
            concurrency = min(50, int(self._conc_entry.text()))
            self._conc_entry.setText(str(concurrency))  # visually fix it on UI if they typed 222222
            wait_timeout = int(self._timeout_entry.text()) * 60
            start_delay = int(self._delay_entry.text())
            next_delay = int(self._next_delay_entry.text())
            process_start_timeout = max(10, int(self._process_start_entry.text()))

            self._save_settings()

        except ValueError:
            self._log("[-] Error: Settings must be valid numbers.", "error")
            return

        if total == 0:
            self._log("[-] Error: No valid prompts found.", "error")
            return

        duration_str = self._dur_combo.currentText()   # e.g. "15s"
        duration_int = int(duration_str.replace("s", "").strip())
        ratio = self._ratio_combo.currentText()
        out_dir = self._folder_entry.text()
        is_headless = False
        watermark_mode = self._watermark_combo.currentText()
        naming_mode = self._naming_combo.currentText()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._start_time = time.time()

        # Start Timer
        self._timer_seconds = 0
        self._timer_lbl.setText("⏱ 00:00:00")
        self._elapsed_timer.start(1000)

        self._status_badge.setText("● RUNNING")
        self._status_badge.setStyleSheet("color: #f59e0b; font-weight: 700; font-size: 10px;")

        self._log(f"⚡ Starting {total} tasks | Concurrency: {concurrency} | Ratio: {ratio}", "info")
        self._log(f"Output: {out_dir} | Watermark: {watermark_mode} | Naming: {naming_mode}", "info")

        self._progress_bar.setValue(0)

        # Get ALL cookie accounts for round-robin rotation
        cookies_list = []
        if hasattr(self, '_settings_page_ref') and self._settings_page_ref:
            accounts = self._settings_page_ref.get_cookie_accounts()
            if accounts:
                cookies_list = accounts  # Pass ALL accounts for round-robin
                self._log(
                    f"[+] Using {len(accounts)} cookie account(s) from Settings "
                    f"(round-robin per task)", "info"
                )
            else:
                self._log("[!] No cookie accounts in Settings — using built-in cookies", "warn")
        else:
            self._log("[!] Settings page not linked — using built-in cookies", "warn")

        self._worker = DolaBotWorker(
            prompt_data, duration_int, total, concurrency, out_dir,
            wait_timeout, start_delay, next_delay, is_headless, watermark_mode,
            proxy_list=proxy_list, mobile_mode=mobile_mode, naming_mode=naming_mode,
            process_start_timeout=process_start_timeout, ratio=ratio,
            cookies_list=cookies_list,
        )
        self._worker.log_signal.connect(lambda msg, lvl: self._log(msg, lvl))
        self._worker.error_signal.connect(self._log_error)
        self._worker.progress_signal.connect(self._update_progress)
        self._worker.stats_signal.connect(self._on_stats)
        self._worker.finished_signal.connect(self._batch_finished)
        self._worker.start()

        # Start stats tracking session
        self._stats_tracker.start_session(total_prompts=total)

