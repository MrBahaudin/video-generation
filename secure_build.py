# Created by Zakariya
"""
secure_build.py — Complete Installable EXE Builder.

Steps:
  1. Nuitka: Python -> C++ -> native machine code (standalone .exe)
  2. Inno Setup: Wrap into a proper Windows Installer (.exe setup file)

Features of the final installer:
  - Chrome (Chromium) bundled inside — no external browser needed
  - ffmpeg bundled inside — no external install needed
  - All Python dependencies compiled in — no Python needed on target PC
  - Desktop shortcut, Start Menu entry, Uninstaller
  - Works on ANY Windows 10/11 PC out of the box
  - 100% portable — one installer, everything included

Usage:
    python secure_build.py
"""

import os
import sys
import subprocess
import shutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe")
SECURE_DIR = os.path.join(PROJECT_DIR, "secure_build")
DIST_DIR = os.path.join(SECURE_DIR, "dist")
INSTALLER_DIR = os.path.join(SECURE_DIR, "installer")

# Inno Setup compiler locations to check
ISCC_PATHS = [
    r"C:\InnoSetup6\iscc.exe",                                    # Custom install path
    r"C:\Program Files (x86)\Inno Setup 6\iscc.exe",
    r"C:\Program Files\Inno Setup 6\iscc.exe",
    r"C:\Program Files (x86)\Inno Setup 5\iscc.exe",
    r"C:\Program Files\Inno Setup 5\iscc.exe",
    # WinGet / AppData installs
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\iscc.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 5\iscc.exe"),
    os.path.expandvars(r"%APPDATA%\Programs\Inno Setup 6\iscc.exe"),
    os.path.expandvars(r"%APPDATA%\Inno Setup 6\iscc.exe"),
    # Direct drive installs
    r"D:\Inno Setup 6\iscc.exe",
    r"D:\Program Files\Inno Setup 6\iscc.exe",
    r"D:\Program Files (x86)\Inno Setup 6\iscc.exe",
]


def log(msg):
    try:
        print(f"[BUILD] {msg}")
    except UnicodeEncodeError:
        safe = msg.encode('ascii', 'replace').decode('ascii')
        print(f"[BUILD] {safe}")


def find_iscc():
    for path in ISCC_PATHS:
        if os.path.exists(path):
            return path
    # Try PATH
    result = shutil.which("iscc")
    return result


def step1_nuitka():
    """Compile Python to native C++ machine code."""
    log("=" * 60)
    log("  STEP 1: Nuitka Compilation (Python -> Machine Code)")
    log("=" * 60)

    os.makedirs(DIST_DIR, exist_ok=True)

    icon_path = os.path.join(PROJECT_DIR, "app_icon.ico")
    pw_browsers = os.path.join(PROJECT_DIR, "playwright_browsers")
    logo_path = os.path.join(PROJECT_DIR, "app_logo.png")
    splash_path = os.path.join(PROJECT_DIR, "ui", "splash.png")
    ffmpeg_path = os.path.join(PROJECT_DIR, "ffmpeg.exe")

    cmd = [
        VENV_PYTHON, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--assume-yes-for-downloads",
        "--windows-console-mode=hide",       # Fixed: hide console instead of disable so Playwright browser can show

        # ── Qt Plugin (fixes blank window / Qt errors) ──
        "--enable-plugin=pyqt6",

        # ── Security / Obfuscation ──
        "--python-flag=no_docstrings",
        "--python-flag=no_warnings",
        "--python-flag=isolated",

        # ── Include all required packages ──
        "--include-package=PyQt6",
        "--include-package=playwright",
        "--include-package=requests",
        "--include-package=packaging",
        "--include-package=certifi",
        "--include-package=urllib3",
        "--include-package=charset_normalizer",
        "--include-package=core",
        "--include-package=ui",
    ]

    # Icon
    if os.path.exists(icon_path):
        cmd.append(f"--windows-icon-from-ico={icon_path}")

    # Data files (ONLY very small critical files that should be packed in EXE)
    cmd.append("--include-data-files=VERSION=VERSION")

    # Output
    cmd += [
        f"--output-dir={DIST_DIR}",
        "--output-filename=DolaVideoGen.exe",
        "--jobs=4",
        "bot_ui_pyqt6.py",
    ]

    log("  Compiling... (5-15 minutes first time)")
    log("")
    result = subprocess.run(cmd, cwd=PROJECT_DIR)

    if result.returncode != 0:
        log("[FAILED] Nuitka compilation failed!")
        return False

    exe = os.path.join(DIST_DIR, "DolaVideoGen.exe")
    if not os.path.exists(exe):
        log("[FAILED] EXE not found after compilation!")
        return False

    size_mb = os.path.getsize(exe) / (1024 * 1024)
    log(f"  EXE compiled: {size_mb:.1f} MB [OK]")
    return True


def step2_inno_setup():
    """Create Windows installer using Inno Setup."""
    log("")
    log("=" * 60)
    log("  STEP 2: Creating Windows Installer (Inno Setup)")
    log("=" * 60)

    iscc = find_iscc()
    if not iscc:
        log("  WARNING: Inno Setup not found — skipping installer creation.")
        log("  The portable DolaVideoGen.exe is still usable directly.")
        log("  To create installer: install Inno Setup 6 from https://jrsoftware.org")
        return False

    log(f"  Found Inno Setup: {iscc}")
    os.makedirs(INSTALLER_DIR, exist_ok=True)

    version = "1.0.0"
    version_file = os.path.join(PROJECT_DIR, "VERSION")
    if os.path.exists(version_file):
        with open(version_file) as f:
            version = f.read().strip()

    icon_path = os.path.join(PROJECT_DIR, "app_icon.ico")
    exe_source = os.path.join(DIST_DIR, "DolaVideoGen.exe")

    iss_content = f"""
; Zakariya Automator — Inno Setup Script
; Auto-generated by secure_build.py

#define AppName "Zakariya Automator"
#define AppVersion "{version}"
#define AppPublisher "Zakariya"
#define AppExeName "DolaVideoGen.exe"

[Setup]
AppId={{{{8F4A2E1B-3C9D-4F7E-B2A1-5D8C6E0F4321}}}}
AppName={{#AppName}}
AppVersion={{#AppVersion}}
AppPublisher={{#AppPublisher}}
AppPublisherURL=https://github.com/MrBahaudin/video-generation
AppSupportURL=https://github.com/MrBahaudin/video-generation
AppUpdatesURL=https://github.com/MrBahaudin/video-generation/releases
DefaultDirName={{autopf}}\\{{#AppName}}
DefaultGroupName={{#AppName}}
AllowNoIcons=yes
OutputDir={INSTALLER_DIR}
OutputBaseFilename=ZakariyaAutomator_v{version}_Setup
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={icon_path if os.path.exists(icon_path) else ""}
WizardStyle=modern
DisableWelcomePage=no
LicenseFile=
PrivilegesRequired=admin
UninstallDisplayIcon={{app}}\\{{#AppExeName}}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
; Main executable
Source: "{exe_source}"; DestDir: "{{app}}"; Flags: ignoreversion
; Heavy assets and browsers (extracted directly to app dir)
Source: "{os.path.join(PROJECT_DIR, 'playwright_browsers', '*')}"; DestDir: "{{app}}\\playwright_browsers"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{os.path.join(PROJECT_DIR, 'ffmpeg.exe')}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{os.path.join(PROJECT_DIR, 'app_logo.png')}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{os.path.join(PROJECT_DIR, 'app_icon.ico')}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{os.path.join(PROJECT_DIR, 'VERSION')}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{os.path.join(PROJECT_DIR, 'ui', 'splash.png')}"; DestDir: "{{app}}\\ui"; Flags: ignoreversion

[Icons]
Name: "{{group}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExeName}}"; IconFilename: "{{app}}\\app_icon.ico"
Name: "{{group}}\\Uninstall {{#AppName}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExeName}}"; IconFilename: "{{app}}\\app_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#AppExeName}}"; Description: "Launch {{#AppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{{app}}\\{{#AppExeName}}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}"
"""

    iss_path = os.path.join(SECURE_DIR, "installer.iss")
    with open(iss_path, "w") as f:
        f.write(iss_content)

    log("  Compiling installer...")
    result = subprocess.run([iscc, iss_path], cwd=PROJECT_DIR)

    if result.returncode != 0:
        log("[FAILED] Inno Setup compilation failed!")
        return False

    # Find output
    for f in os.listdir(INSTALLER_DIR):
        if f.endswith(".exe"):
            installer = os.path.join(INSTALLER_DIR, f)
            size_mb = os.path.getsize(installer) / (1024 * 1024)
            log("")
            log("=" * 60)
            log("  INSTALLER CREATED!")
            log("=" * 60)
            log(f"  File: {installer}")
            log(f"  Size: {size_mb:.1f} MB")
            log("")
            log("  This installer includes:")
            log("  [OK] DolaVideoGen.exe (compiled native code)")
            log("  [OK] Chrome/Chromium browser (bundled)")
            log("  [OK] ffmpeg (bundled)")
            log("  [OK] All Python dependencies (compiled in)")
            log("  [OK] Desktop shortcut + Start Menu")
            log("  [OK] Uninstaller")
            log("")
            log("  Works on ANY Windows 10/11 PC — no extra installs needed!")
            return True

    log("[FAILED] Installer file not found!")
    return False


def main():
    print()
    print("=" * 60)
    print("  ZAKARIYA AUTOMATOR — COMPLETE INSTALLER BUILD")
    print("  Nuitka + Inno Setup | Maximum Security + Portability")
    print("=" * 60)
    print()

    # Step 1: Compile with Nuitka
    if not step1_nuitka():
        print("\n[FAILED] Build stopped at Nuitka step.")
        return

    # Step 2: Create installer
    step2_inno_setup()

    print()
    print("Build process complete.")


if __name__ == "__main__":
    main()
