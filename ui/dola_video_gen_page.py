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
    QApplication,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QTextCursor, QColor

# Add project root to path so we can import headless_bot and core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from headless_bot import run_bot, internet_monitor
from core.settings_manager import load_settings, save_settings, load_user_data, save_user_data
from core.stats_tracker import StatsTracker


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
    """Background worker thread for Dola browser-based batch generation."""
    log_signal = pyqtSignal(str, str)      # message, level
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int, int)  # completed, total, successes
    stats_signal = pyqtSignal(int, int, int, int, int) # total, queued, active, ok, failed
    finished_signal = pyqtSignal()

    def __init__(self, prompt_data, duration, total, concurrency, output_dir,
                 wait_timeout=600, start_delay=2, next_delay=5,
                 headless=True, watermark_mode="Blur (Delogo)", proxy_list=None, mobile_mode=True, naming_mode="Title in CSV"):
        super().__init__()
        self.prompt_data = prompt_data
        self.duration = duration
        self.total = total
        self.concurrency = concurrency
        self.output_dir = output_dir
        self.wait_timeout = wait_timeout
        self.current_timeout = wait_timeout
        self.start_delay = start_delay
        self.next_delay = next_delay
        self.headless = headless
        self.watermark_mode = watermark_mode
        self.proxy_list = proxy_list or []
        self.mobile_mode = mobile_mode
        self.naming_mode = naming_mode
        self._is_stopped = False
        self._failed_prompts = []  # Track failed prompts for retry

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

    async def batch_manager(self):
        sem = asyncio.Semaphore(self.concurrency)
        ffmpeg_sem = asyncio.Semaphore(2)
        completed = 0
        successes = 0
        active = 0

        internet_task = asyncio.create_task(internet_monitor())

        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            self.log("Launching browser...")
            try:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"]
                )
            except Exception as e:
                self.log_error(f"[-] Failed to launch browser: {e}")
                return

            async def worker(instance_id):
                nonlocal completed, successes, active
                async with sem:
                    if self._is_stopped:
                        return
                        
                    is_generating = False
                    def on_generating():
                        nonlocal active, is_generating
                        if not is_generating:
                            is_generating = True
                            active += 1
                            failed_cnt = completed - successes
                            queued_cnt = self.total - completed - active
                            self.stats_signal.emit(self.total, queued_cnt, active, successes, failed_cnt)

                    failed = completed - successes
                    queued = self.total - completed - active
                    self.stats_signal.emit(self.total, queued, active, successes, failed)

                    if self.next_delay > 0:
                        self.log(f"[Bot {instance_id}] Waiting {self.next_delay}s before next task...")
                        # Uninterruptible sleep replaced with a loop to check stop flag
                        for _ in range(self.next_delay):
                            if self._is_stopped:
                                if is_generating:
                                    active -= 1
                                self.stats_signal.emit(self.total, queued, active, successes, failed)
                                return
                            await asyncio.sleep(1)

                    data = self.prompt_data[(instance_id - 1) % len(self.prompt_data)]
                    prompt_text = data.get("prompt", "")
                    caption = data.get("caption", None)

                    # Assign proxy via round-robin if proxy list is available
                    proxy = None
                    if self.proxy_list:
                        proxy = self.proxy_list[(instance_id - 1) % len(self.proxy_list)]
                        self.log(f"[Bot {instance_id}] Using proxy: {proxy}")

                    self.log(f"[Bot {instance_id}] Starting with prompt: '{prompt_text[:30]}...'")
                    try:
                        success = await run_bot(
                            browser=browser,
                            prompt_text=prompt_text,
                            duration=self.duration,
                            instance_id=instance_id,
                            watermark_mode=self.watermark_mode,
                            log_callback=self.log,
                            error_callback=self.log_error,
                            output_dir=self.output_dir,
                            caption=caption,
                            wait_timeout=self.current_timeout,
                            ffmpeg_sem=ffmpeg_sem,
                            stop_check=lambda: self._is_stopped,
                            proxy=proxy,
                            mobile_mode=self.mobile_mode,
                            naming_mode=self.naming_mode,
                            on_generating_callback=on_generating
                        )
                        
                        if success:
                            successes += 1
                        else:
                            # Track failed prompt for retry
                            self._failed_prompts.append(data)
                    except Exception as e:
                        self.log_error(f"[-] [Bot {instance_id}] Error in worker: {str(e)}")
                        success = False
                        
                    if is_generating:
                        active -= 1

                    completed += 1
                    failed = completed - successes
                    queued = self.total - completed - active
                    
                    self.progress_signal.emit(completed, self.total, successes)
                    self.stats_signal.emit(self.total, queued, active, successes, failed)

            tasks = []
            for i in range(1, self.total + 1):
                if self._is_stopped:
                    break
                tasks.append(asyncio.create_task(worker(i)))
                if self.start_delay > 0:
                    for _ in range(self.start_delay):
                        if self._is_stopped:
                            break
                        await asyncio.sleep(1)

            try:
                if tasks:
                    await asyncio.gather(*tasks)
            finally:
                # Always cleanup browser — even on crash or forced stop
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                internet_task.cancel()


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
        self._naming_combo.addItems(["Title in CSV", "Title On Video"])
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

        # Row 0: Labels for Row 1
        grid.addWidget(QLabel("Duration"), 0, 0)
        grid.addWidget(QLabel("Concurrent Threads"), 0, 1)
        grid.addWidget(QLabel("Timeout (min)"), 0, 2)

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
        grid.addWidget(self._timeout_entry, 1, 2)

        # Add vertical spacing between grid rows
        grid.setRowMinimumHeight(1, grid.rowMinimumHeight(1) + 6)

        # Row 2: Labels for Row 3
        grid.addWidget(QLabel("Start Delay (s)"), 2, 0)
        grid.addWidget(QLabel("Next Task Delay (s)"), 2, 1)
        grid.addWidget(QLabel("Watermark Removal"), 2, 2)

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

        lay.addLayout(grid)

        lay.addSpacing(8)

        # Row 4: Toggles
        row3 = QHBoxLayout()
        row3.setSpacing(24)

        # NOTE: Hide Browser, Loop, and Mobile Emulation disabled per user request
        self._loop_cb = QCheckBox("Auto-Loop Batch")
        self._loop_cb.setChecked(False)
        self._loop_cb.hide()
        self._headless_cb = QCheckBox("Hide Browser")
        self._headless_cb.setChecked(False)
        self._headless_cb.hide()
        
        self._mobile_cb = QCheckBox("Mobile Emulation")
        self._mobile_cb.setChecked(False)
        self._mobile_cb.hide()

        row3.addStretch()
        lay.addLayout(row3)

        lay.addWidget(_hline())

        # ── Proxy List ──
        proxy_hdr = QHBoxLayout()
        proxy_hdr.addWidget(_label("🌐  PROXY LIST (IP Rotation)", "section_title"))
        proxy_hdr.addStretch()
        self._proxy_count_lbl = QLabel("0 proxies")
        self._proxy_count_lbl.setStyleSheet("color:#60a5fa; font-size:11px; font-weight:700;")
        proxy_hdr.addWidget(self._proxy_count_lbl)
        lay.addLayout(proxy_hdr)

        self._proxy_entry = QTextEdit()
        self._proxy_entry.setPlaceholderText(
            'Paste proxies here (1 per line)...\n\n'
            'Formats supported:\n'
            '  host:port\n'
            '  user:pass@host:port\n'
            '  socks5://host:port\n'
            '  http://host:port\n\n'
            'Leave empty = no proxy (direct IP)'
        )
        self._proxy_entry.setMinimumHeight(60)
        self._proxy_entry.setMaximumHeight(100)
        self._proxy_entry.textChanged.connect(self._update_proxy_count)
        lay.addWidget(self._proxy_entry)

        proxy_file_row = QHBoxLayout()
        proxy_load_btn = QPushButton("📂 Load Proxy File")
        proxy_load_btn.setMinimumWidth(130)
        proxy_load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        proxy_load_btn.clicked.connect(self._load_proxy_file)
        proxy_file_row.addWidget(proxy_load_btn)
        proxy_file_row.addStretch()
        lay.addLayout(proxy_file_row)

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

    def _load_settings(self):
        s = load_settings()
        ud = load_user_data()
        self._folder_entry.setText("")  # Do not load saved location
        self._conc_entry.setText(s.get("concurrency", "20"))
        self._dur_combo.setCurrentText(s.get("duration", "15s"))
        self._timeout_entry.setText(s.get("timeout_min", "30"))
        self._delay_entry.setText(s.get("start_delay", "5"))
        self._next_delay_entry.setText(s.get("next_delay", "5"))
        self._headless_cb.setChecked(s.get("show_browser", True))
        self._watermark_combo.setCurrentText(s.get("watermark_mode", "Blur (Delogo)"))
        self._loop_cb.setChecked(s.get("auto_loop", False))
        self._mobile_cb.setChecked(False)

        # Load large data from user_data
        self._proxy_entry.setText(ud.get("proxy_list", ""))
        self._prompt_entry.setText("")  # Do not load saved prompt

    def _save_settings(self):
        # Save small UI settings
        s = load_settings()
        s["concurrency"] = self._conc_entry.text()
        s["duration"] = self._dur_combo.currentText()
        s["timeout_min"] = self._timeout_entry.text()
        s["start_delay"] = self._delay_entry.text()
        s["next_delay"] = self._next_delay_entry.text()
        s["show_browser"] = self._headless_cb.isChecked()
        s["watermark_mode"] = self._watermark_combo.currentText()
        s["auto_loop"] = self._loop_cb.isChecked()
        s["mobile_mode"] = self._mobile_cb.isChecked()
        save_settings(s)

        # Save large data separately
        ud = load_user_data()
        ud["proxy_list"] = self._proxy_entry.toPlainText().strip()
        save_user_data(ud)

    # ─── Actions ───────────────────────────────────────

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self._folder_entry.text())
        if folder:
            self._folder_entry.setText(folder)
            self._save_settings()

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

    # ─── Logging ───────────────────────────────────────

    def _log(self, message, level="info"):
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
        self._check_error_tally(message)

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
        if not out_dir or not os.path.exists(out_dir):
            QMessageBox.warning(self, "Error", "Please select a valid Output Folder before starting!")
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

        # Parse proxy list
        proxy_text = self._proxy_entry.toPlainText().strip()
        proxy_list = [p.strip() for p in proxy_text.splitlines() if p.strip()] if proxy_text else []
        mobile_mode = self._mobile_cb.isChecked()

        prompt_data = self._parse_prompts(text)

        try:
            total = len(prompt_data)
            concurrency = min(50, int(self._conc_entry.text()))
            self._conc_entry.setText(str(concurrency))  # visually fix it on UI if they typed 222222
            wait_timeout = int(self._timeout_entry.text()) * 60
            start_delay = int(self._delay_entry.text())
            next_delay = int(self._next_delay_entry.text())

            self._save_settings()

        except ValueError:
            self._log("[-] Error: Settings must be valid numbers.", "error")
            return

        if total == 0:
            self._log("[-] Error: No valid prompts found.", "error")
            return

        duration = self._dur_combo.currentText()
        out_dir = self._folder_entry.text()
        is_headless = self._headless_cb.isChecked()
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

        self._log("═══════════════════════════════════════════════", "info")
        self._log("⚡ INITIALIZING MULTI-THREADED GENERATION", "info")
        self._log(f"Tasks/Prompts: {total} | Concurrency: {concurrency}", "info")
        self._log(f"Output: {out_dir} | Timeout: {wait_timeout}s | Headless: {is_headless} | Watermark: {watermark_mode} | Naming: {naming_mode}", "info")
        if proxy_list:
            self._log(f"Proxies: {len(proxy_list)} loaded (round-robin)", "info")
        self._log(f"Mobile Emulation: {'ON (random device per instance)' if mobile_mode else 'OFF (desktop mode)'}", "info")
        self._log("═══════════════════════════════════════════════", "info")

        self._progress_bar.setValue(0)

        self._worker = DolaBotWorker(
            prompt_data, duration, total, concurrency, out_dir,
            wait_timeout, start_delay, next_delay, is_headless, watermark_mode,
            proxy_list=proxy_list, mobile_mode=mobile_mode, naming_mode=naming_mode
        )
        self._worker.log_signal.connect(lambda msg, lvl: self._log(msg, lvl))
        self._worker.error_signal.connect(self._log_error)
        self._worker.progress_signal.connect(self._update_progress)
        self._worker.stats_signal.connect(self._on_stats)
        self._worker.finished_signal.connect(self._batch_finished)
        self._worker.start()

        # Start stats tracking session
        self._stats_tracker.start_session(total_prompts=total)
