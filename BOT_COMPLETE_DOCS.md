# Zakariya Automator — Complete Bot Documentation
> **Version:** Direct API Mode (Browserless)
> **Last Updated:** July 1, 2026
> **Status:** Fully Working — Ratio, Title, Watermark, Multi-Account all operational
> **Purpose:** Give this file to any AI to recreate the exact same bot from scratch

---

## Table of Contents

1. [What This Bot Does](#1-what-this-bot-does)
2. [File Structure](#2-file-structure)
3. [Complete API Flow](#3-complete-api-flow)
4. [dola_direct.py — Core API Client](#4-dola_directpy--core-api-client)
5. [UI — dola_video_gen_page.py](#5-ui--dola_video_gen_pagepy)
6. [All Features Implemented](#6-all-features-implemented)
7. [CSV Format](#7-csv-format)
8. [File Naming Modes](#8-file-naming-modes)
9. [Watermark Removal](#9-watermark-removal)
10. [Title On Video ffmpeg drawtext](#10-title-on-video-ffmpeg-drawtext)
11. [Title in Text File](#11-title-in-text-file)
12. [Multi-Account Round-Robin](#12-multi-account-round-robin)
13. [Prompt Sanitizer Timelapse Bypass](#13-prompt-sanitizer-timelapse-bypass)
14. [Cookies Setup](#14-cookies-setup)
15. [Settings Saved and Loaded](#15-settings-saved-and-loaded)
16. [Architecture Diagram](#16-architecture-diagram)
17. [Troubleshooting](#17-troubleshooting)

---

## 1. What This Bot Does

Automatically generates AI videos from Dola.com in bulk using reverse-engineered direct API calls.

- No browser window — pure HTTP urllib + minimal Playwright headless for polling
- Bulk generation — load CSV/TXT with hundreds of prompts, runs concurrently
- Multi-account — multiple cookie accounts rotate round-robin per task
- Watermark removal — ffmpeg delogo/crop filter removes Dola logo
- Title on video — ffmpeg drawtext burns numbered title on video
- Title in text file — saves matching .txt with title alongside video
- Aspect ratio control — 1:1, 3:4, 4:3, 9:16, 16:9, 21:9 all sent to API

---

## 2. File Structure

```
d:\Antigravity\Dola 15s\
|
+-- bot_ui_pyqt6.py              <- Entry point. Shows splash -> MainWindow
+-- dola_direct.py               <- Core API: send, poll, download
+-- ffmpeg.exe                   <- Bundled ffmpeg (194MB) for watermark/title
+-- settings.json                <- UI settings (concurrency, duration, ratio etc.)
+-- user_data.json               <- Cookie accounts + proxy list (large file)
|
+-- ui/
|   +-- dola_video_gen_page.py   <- Main page widget + DolaBotWorker thread
|   +-- main_window.py           <- Sidebar navigation, page switching
|   +-- settings_page.py         <- Cookies Manager + Proxy Manager UI
|   +-- splash_screen.py         <- Startup loading screen
|   +-- dashboard_page.py        <- Stats dashboard
|   +-- styles.py                <- All PyQt6 CSS/QSS styles
|
+-- core/
|   +-- settings_manager.py      <- load_settings(), save_settings(), load_user_data()
|   +-- stats_tracker.py         <- Per-session stats tracking
|   +-- security.py              <- Anti-debug check (runs silently on startup)
|
+-- downloads/                   <- Temp download folder (auto-cleaned)
```

---

## 3. Complete API Flow

### Per-Video Flow (every video goes through all 5 steps)

```
STEP 1: POST /chat/completion  (urllib — no browser needed)
----------------------------------------------------------
URL: https://www.dola.com/chat/completion?
     version_code=20800&language=en&device_platform=web
     &aid=495671&real_aid=495671&pkg_type=release_version
     &pc_version=3.25.1&samantha_web=1&web_platform=browser
     &use-olympus-account=1&region=PK&sys_region=PK
     &device_id=...&web_id=...&tea_uuid=...
     &a_bogus=...   <- STATIC reused value (works for /chat/completion)
     &web_tab_id={uuid.uuid4()}

Headers:
  content-type: application/json
  agw-js-conv: str
  referer: https://www.dola.com/chat/create-image
  cookie: {cookie_string from account}

Body (JSON):
{
  "client_meta": {
    "local_conversation_id": "local_{timestamp_ms}",
    "conversation_id": "",
    "bot_id": "7339470689562525703",    <- Dola bot ID (static)
    "last_section_id": "",
    "last_message_index": null
  },
  "messages": [{
    "local_message_id": "{uuid4}",
    "content_block": [{
      "block_type": 10000,
      "content": {
        "text_block": {
          "text": "Create {duration}s video: {sanitized_prompt}",
          "icon_url": "", "icon_url_dark": "", "summary": ""
        },
        "pc_event_block": ""
      },
      "block_id": "{uuid4}",
      "parent_id": "",
      "meta_info": [], "append_fields": []
    }],
    "message_status": 0
  }],
  "option": {
    "send_message_scene": "", "create_time_ms": {now_ms},
    "collect_id": "", "is_audio": false,
    "answer_with_suggest": false, "tts_switch": false,
    "need_deep_think": 0, "click_clear_context": false,
    "from_suggest": false, "is_regen": false, "is_replace": false,
    "is_from_click_option": false, "is_from_click_softlink": false,
    "disable_sse_cache": false, "select_text_action": "",
    "is_select_text": false, "resend_for_regen": false,
    "scene_type": 0, "unique_key": "{uuid4}", "start_seq": 0,
    "need_create_conversation": true,
    "conversation_init_option": {"need_ack_conversation": true},
    "regen_query_id": [], "edit_query_id": [], "regen_instruction": "",
    "no_replace_for_regen": false, "message_from": 0,
    "shared_app_name": "", "shared_app_id": "",
    "sse_recv_event_options": {"support_chunk_delta": true},
    "is_ai_playground": false, "is_old_user": false,
    "recovery_option": {
      "is_recovery": false,
      "req_create_time_sec": {now_sec},
      "append_sse_event_scene": 0
    },
    "message_storage_type": 0
  },
  "chat_ability": {
    "ability_type": 17,
    "ability_param": "{\"model\":\"seedance_v2.0\",\"duration\":15,\"ratio\":\"9:16\"}"
    NOTE: ability_param is a JSON STRING not a nested object
  },
  "user_context": [],
  "ext": {
    "answer_with_suggest": "0",
    "fp": "verify_direct_fp",
    "sub_conv_firstmet_type": "1",
    "collection_id": "",
    "conversation_init_option": "{\"need_ack_conversation\":true}",
    "commerce_credit_config_enable": "0"
  }
}

Response: SSE stream (text/event-stream)
  -> Parse lines for "conversation_id" field
  -> Extract conv_id e.g. "38415625230845201"
  -> If "credit_exhausted" in text -> mark account failed, skip polling
  -> If "content_policy" in text -> mark rejected, skip polling


STEP 2: POLL /im/chain/single  (Playwright headless — required for a_bogus)
----------------------------------------------------------------------------
WHY Playwright here:
  /im/chain/single requires a_bogus signature
  a_bogus = HMAC signature generated by Dola JavaScript in real browser
  We cannot generate it ourselves (obfuscated JS)
  Solution: page.route() intercepts browser OWN request -> we read response

Playwright setup:
  - Headless browser opens https://www.dola.com/chat/{conv_id}
  - All cookies injected via context.add_cookies()
  - page.route("**/im/chain/single**", handler)
  - Handler captures response JSON
  - browser.close() after video URL found OR timeout

Response parsing (find video URL):
  downlink_body
    pull_singe_chain_downlink_body
      messages[]
        content (JSON string) -> parse
          block_type: 2074
            creation_block
              creations[]
                type: 1  -> Image (ignore, appears ~30s)
                type: 2  -> VIDEO <- WAIT FOR THIS
                  video.download_url = "http://v16-dola.dola.com/..."


STEP 3: DOWNLOAD  (urllib)
--------------------------
urllib.request.urlopen(video_url) -> read chunks -> save to downloads/{uuid}.mp4
Average size: 1.8-2.5 MB


STEP 4: WATERMARK REMOVAL  (ffmpeg)
-------------------------------------
Mode "Blur (Delogo)":
  Probe dimensions: ffmpeg -i video.mp4 -> parse stderr for "720x1280"
  Filter: delogo=x={v_width-180}:y={v_height-60}:w=170:h=50

Mode "Crop":
  Filter: crop=iw:ih-80:0:0

Mode "None":
  Skip


STEP 5: POST-PROCESSING (Title/Naming)
---------------------------------------
See sections 8-11 below.
```

---

## 4. dola_direct.py — Core API Client

### Key Constants

```python
BOT_ID       = "7339470689562525703"   # Dola bot -- static
ABILITY_TYPE = 17                       # Video generation
MODEL        = "seedance_v2.0"          # Current Dola model

# a_bogus -- static string, works for /chat/completion (not validated strictly)
# Only /im/chain/single validates it (hence Playwright for that endpoint)
```

### Key Functions

```python
def _sanitize_prompt(prompt: str) -> str:
    """
    Removes ALL duration/timestamp clues from prompt text.
    Prevents Dola AI from detecting >10s and making timelapse.
    """

def _build_body(prompt: str, duration: int, ratio: str = "9:16") -> bytes:
    """
    Builds the JSON body for POST /chat/completion.
    ability_param = json.dumps({"model": MODEL, "duration": duration, "ratio": ratio})
    NOTE: ability_param must be a JSON STRING, not a nested object.
    """

def send_video_request(prompt, duration=15, log=print, cookies=None,
                       _reject_out=None, ratio="9:16") -> str | None:
    """
    POSTs to /chat/completion, parses SSE stream for conv_id.
    Returns conv_id string on success, None on failure.
    """

async def poll_for_video(conv_id, timeout=600, log=print, cookies=None) -> str | None:
    """
    Opens Playwright headless browser, navigates to dola.com/chat/{conv_id},
    intercepts /im/chain/single responses via page.route(),
    waits for type=2 creation block with video URL.
    Returns download_url on success, None on timeout.
    """

def download_video(url, prompt, idx, log=print) -> str | None:
    """
    Downloads video from CDN URL to downloads/ folder.
    Returns local file path on success, None on failure.
    """
```

---

## 5. UI — dola_video_gen_page.py

### DolaBotWorker (QThread) Parameters

```python
class DolaBotWorker(QThread):
    def __init__(self,
        prompt_data,           # list of {"prompt": str, "caption": str|None}
        duration,              # int seconds e.g. 15
        total,                 # total number of tasks
        concurrency,           # asyncio Semaphore value
        output_dir,            # output folder path
        wait_timeout=600,      # poll timeout seconds
        start_delay=2,         # seconds before first task
        next_delay=5,          # seconds between task starts
        headless=True,
        watermark_mode="Blur (Delogo)",
        proxy_list=None,
        mobile_mode=True,
        naming_mode="Title On Video",
        process_start_timeout=60,
        ratio="9:16",
        cookies_list=None      # list of accounts (each = list of cookie dicts)
    ):
```

---

## 6. All Features Implemented

| Feature | Status | Details |
|---------|--------|---------|
| Bulk video generation | OK | Async batch, configurable concurrency |
| Direct API no browser UI | OK | urllib POST, Playwright only for poll |
| Duration control | OK | Sent in ability_param (5s, 10s, 15s) |
| Aspect ratio | OK | ratio field in ability_param JSON string |
| Watermark removal | OK | ffmpeg delogo or crop |
| Title On Video | OK | ffmpeg drawtext, numbered, bottom-center |
| Title in Text File | OK | Random mp4 name + matching .txt |
| Multi-account round-robin | OK | N accounts rotate per task |
| Account cooldown | OK | 3 videos then 2hr cooldown per account |
| Credit exhaustion detection | OK | 2 consecutive fails disables account |
| Prompt sanitizer | OK | 14+ timestamp formats removed |
| CSV loading | OK | Column A = prompt, Column B = caption/title |
| TXT loading | OK | One prompt per line or double-newline separated |
| Proxy support | OK | Proxy list in Settings page |
| Settings persistence | OK | settings.json + user_data.json |
| Stats tracking | OK | Dashboard with session stats |
| Auto-loop | OK | Repeats batch after completion |
| Export logs | OK | Save terminal log to .txt file |

---

## 7. CSV Format

```
Column A (Prompt),Column B (Title/Caption)
"a red car racing on mountain road","A Car Race"
"ocean waves at golden sunset","Ocean Sunset"
"cat playing in snow","Cute Cat"
```

- Column A -> sent to Dola API as the video prompt
- Column B -> used for file naming / title overlay / text file
- No header row needed (auto-detected)
- Both quoted and unquoted formats work

---

## 8. File Naming Modes

### Mode 1: "Title On Video"

Output file format:
```
01. A Car Race.mp4
^^  ^
|   +-- Column B caption (invalid filename chars stripped)
+-- instance_id zero-padded 2 digits
```

Same title repeated = different numbers:
  01. A Car Race.mp4
  03. A Car Race.mp4

Code:
```python
cap_clean = re.sub(r'[<>:"/\\|?*]', '', caption.strip())
filename = f"{instance_id:02d}. {cap_clean}.mp4"
```

### Mode 2: "Title in Text File"

Output files:
```
s78ekde9.mp4      <- random 8-char hex
s78ekde9.txt      <- SAME name, contains Column B title
```

txt file contents: just the raw caption text

Code:
```python
rand_id = uuid.uuid4().hex[:8]
filename = f"{rand_id}.mp4"
txt_path = os.path.splitext(dest)[0] + ".txt"
with open(txt_path, 'w', encoding='utf-8') as f:
    f.write(caption)
```

---

## 9. Watermark Removal

Dola logo position on 720x1280 video:
```
x = v_width - 180   (~540)
y = v_height - 60   (~1220)
w = 170
h = 50
```

ffmpeg Blur (Delogo) command:
```bash
ffmpeg -y -i input.mp4 -vf "delogo=x=540:y=1220:w=170:h=50" -c:v libx264 -preset ultrafast -crf 23 -c:a copy output.mp4
```

ffmpeg Crop command:
```bash
ffmpeg -y -i input.mp4 -vf "crop=iw:ih-80:0:0" -c:v libx264 -preset ultrafast -crf 23 -c:a copy output.mp4
```

Note: ffmpeg.exe must be in same folder as bot_ui_pyqt6.py

---

## 10. Title On Video (ffmpeg drawtext)

Burns "01. Caption" onto video bottom-center, white text, black border.

```python
async def _burn_title(self, video_path: str, instance_id: int, caption: str):
    title_text = f"{instance_id:02d}. {caption.strip()}"

    # Escape for ffmpeg drawtext
    escaped = title_text.replace("\\", "\\\\").replace("'", "\\'") \
                        .replace(":", "\\:").replace("%", "\\%")

    # Font: Arial if available, else built-in
    arial = r"C:\Windows\Fonts\arial.ttf"
    if os.path.exists(arial):
        font_part = "fontfile='C\\:/Windows/Fonts/arial.ttf':"
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

    cmd = [ffmpeg_exe, "-y", "-i", video_path,
           "-vf", drawtext,
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
           "-c:a", "copy", temp_path]
```

---

## 11. Title in Text File

```python
elif self.naming_mode == "Title in Text File" and caption:
    txt_path = os.path.splitext(dest)[0] + ".txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(caption)
```

Result in output folder:
```
a3f9b21c.mp4   <- video (random name)
a3f9b21c.txt   <- text file with title from CSV Column B
d82e7f04.mp4
d82e7f04.txt
```

---

## 12. Multi-Account Round-Robin

```python
# Each account = list of cookie dicts
# Assignment: task N -> account (N-1) % len(accounts)
# Task 1 -> Account 1
# Task 2 -> Account 2
# Task 4 -> Account 1 (wraps around)

# Cooldown: 3 successful videos per account -> 2hr cooldown
MAX_VIDEOS_PER_ACCT = 3
COOLDOWN_HOURS = 2

# Credit exhaustion: 2 consecutive credit_exhausted -> account DISABLED for batch
```

---

## 13. Prompt Sanitizer (Timelapse Bypass)

WHY needed: Dola AI reads prompt text. If it detects >10s mentions, it generates timelapse.
SOLUTION: Strip ALL duration clues from text. Duration goes in ability_param only.

What gets removed:

```
Format 0:  "0-toddler", "1.5-reveal", "12-ends"
Format 1:  [0.0-2.] [2.5-5.]  bracket+decimal
Format 2:  [0-3] [3-7]  bracket+integer
Format 3:  0-3s: 3-7s: 7-11s:
Format 4:  (0:00-0:03) timecodes in parens
Format 5:  0:00-0:03 bare timecodes
Format 5b: From 0:02 to 0:04
Format 5c: 0:02 to 0:04
Format 5d: At 0:07
Format 6:  0-3: 3-7: plain range+colon
Format 7:  Second 0-3: Seconds 3-7:
Format 8:  at 5s / from 3s / around 11s
Format 9:  5s: or 5.5s:
Format 10: for 5s / for 3 seconds / for final 3s
Format 11: standalone 15s / 10 seconds long
Format 12: [7.5] single bracket timestamps
Format 13: 15 seconds (without abbreviation)
Format 14: M:SS timestamps (keeps ratio like 9:16 -- smart preserve)

Also removed:
  - Scene transitions: "Same location, later." / "Meanwhile," / "Later,"
  - Voice-over/narration sections (multi-line)
  - Negative prompt sections
  - Long prompts >800 chars: truncated at scene break
```

---

## 14. Cookies Setup

1. Install Chrome extension: "Get cookies.txt LOCALLY"
2. Login to www.dola.com
3. Click extension -> Export for this domain -> Copy (Netscape format)
4. In App -> Settings tab -> Cookies Manager -> Paste -> Add Account

### Netscape Format Example
```
# Netscape HTTP Cookie File
.dola.com	TRUE	/	FALSE	0	sessionid	abc123xyz
.dola.com	TRUE	/	TRUE	0	sid_tt	abc123xyz
.dola.com	TRUE	/	FALSE	0	uid_tt	def456uvw
www.dola.com	FALSE	/chat	FALSE	0	hook_slardar_session_id	xyz789
```

### Required Cookies
| Cookie | Purpose |
|--------|---------|
| sessionid | Main session auth |
| sessionid_ss | Secure session |
| sid_tt | ByteDance session token |
| sid_guard | Session guard |
| uid_tt | User ID |
| uid_tt_ss | Secure user ID |
| passport_csrf_token | CSRF protection |
| oauth_token | OAuth token |
| odin_tt | Device auth token |
| ttwid | Tab/window ID |

Cookie expiry: ~60 days. Sign: status_code 712012002 in logs.

---

## 15. Settings Saved and Loaded

### settings.json
```json
{
  "concurrency": "3",
  "duration": "15s",
  "ratio": "9:16",
  "timeout_min": "30",
  "process_start_timeout": "80",
  "start_delay": "5",
  "next_delay": "5",
  "show_browser": false,
  "watermark_mode": "Blur (Delogo)",
  "auto_loop": false,
  "mobile_mode": false,
  "naming_mode": "Title On Video"
}
```

### user_data.json
```json
{
  "cookie_accounts": [
    [{"name": "sessionid", "value": "abc...", "domain": ".dola.com", "path": "/"}, ...],
    [{"name": "sessionid", "value": "def...", "domain": ".dola.com", "path": "/"}, ...]
  ],
  "proxy_list": ["http://user:pass@1.2.3.4:8080"]
}
```

---

## 16. Architecture Diagram

```
bot_ui_pyqt6.py  (Entry Point)
  |
  +-- SplashScreen -> MainWindow
        |
        +-- DolaVideoGenPage  (main UI)
        |     |
        |  [LAUNCH] -> DolaBotWorker (QThread)
        |                 |
        |             asyncio loop -> batch_manager()
        |                 |
        |         Task 1 ... Task N  (concurrent via Semaphore)
        |                 |
        |          _run_single(instance_id, prompt, caption)
        |                 |
        |     _pick_account() -> round-robin cookie selection
        |                 |
        |     send_video_request() -> urllib POST /chat/completion
        |                 |
        |     poll_for_video() -> Playwright headless /im/chain/single
        |                 |
        |     download_video() -> urllib CDN download
        |                 |
        |     _remove_watermark() -> ffmpeg delogo/crop
        |                 |
        |     shutil.move() -> output_dir/filename
        |                 |
        |     _burn_title()           OR     write .txt file
        |     (Title On Video mode)          (Title in Text File mode)
        |
        +-- SettingsPage
        |     +-- Cookies Manager -> user_data.json
        |     +-- Proxy Manager   -> user_data.json
        |
        +-- DashboardPage -> StatsTracker
```

---

## 17. Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| status_code: 712012002 | Cookies expired | Get fresh cookies, re-add |
| conv_id None immediately | Bad cookies | Refresh cookies |
| credit_exhausted instantly | Account credit done | New account or fresh cookies |
| Video URL not found timeout | Generation slow | Increase timeout to 60 min |
| ffmpeg not found | ffmpeg.exe missing | Place ffmpeg.exe next to bot_ui_pyqt6.py |
| Video is 5s not 15s | API limit | Check ability_param duration value |
| Timelapse generated | Prompt has time mentions | Sanitizer should catch -- check log |
| Logo still visible | Watermark None selected | Change to Blur Delogo |
| 429 Too Many Requests | Too many threads | Max 3 threads per cookie |
| Title not burned | ffmpeg drawtext error | Check terminal log for ffmpeg detail |
| .txt file not created | No caption in Column B | CSV must have Column B |
| Same file overwrites | Same title, no number | Use Title On Video -- numbers prevent clash |
| Account keeps skipped | In cooldown 3 videos | Wait 2 hours or add more accounts |

### Normal Log (healthy run)
```
Starting 5 tasks | Concurrency: 3 | Ratio: 9:16
[1] POST /chat/completion -- 'a red car driving in rain'
[1] status=200
[1] conv_id: 38415625230845201
[1] Polling conv ... for video...
[1] 30s elapsed, refreshing...
[1] VIDEO URL: http://v16-dola.dola.com/...
[1] Removing watermark Blur Delogo...
[1] Watermark removed!
[1] Saved: C:/Output/01. A Car Race.mp4
[1] Burning title on video...
[1] Title burned!
Progress: 1/5
```

---

End of documentation. Bot is fully working as of July 1, 2026.
