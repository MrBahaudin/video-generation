# Created by Zakariya
"""
security.py — Anti-tamper and anti-debug protection for Zakariya Automator.
Detects debuggers, reverse engineering tools, and tampered code.
"""

import sys
import os
import ctypes
import hashlib


def _check_debugger():
    """Check if a debugger is attached (Windows)."""
    try:
        if sys.platform == "win32":
            # Windows API: IsDebuggerPresent
            is_debugged = ctypes.windll.kernel32.IsDebuggerPresent()
            if is_debugged:
                return True
            # CheckRemoteDebuggerPresent
            result = ctypes.c_int(0)
            ctypes.windll.kernel32.CheckRemoteDebuggerPresent(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(result)
            )
            if result.value:
                return True
    except Exception:
        pass
    return False


def _check_analysis_tools():
    """Check if common reverse engineering tools are running."""
    suspicious_processes = [
        "ollydbg", "x64dbg", "x32dbg", "ida", "ida64", "idaq", "idaq64",
        "ghidra", "radare2", "r2", "cutter", "binary ninja",
        "cheatengine", "cheat engine", "processhacker",
        "wireshark", "fiddler", "charles",
        "dnspy", "dotpeek", "ilspy", "de4dot",
        "pyinstxtractor", "pycdc", "uncompyle",
    ]
    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True,
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
            running = result.stdout.lower()
            for proc in suspicious_processes:
                if proc in running:
                    return True
    except Exception:
        pass
    return False


def _check_vm():
    """Basic VM detection (optional — can disable if distributing to VM users)."""
    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["wmic", "computersystem", "get", "model"],
                capture_output=True, text=True,
                creationflags=0x08000000
            )
            output = result.stdout.lower()
            vm_indicators = ["virtual", "vmware", "virtualbox", "qemu", "xen", "kvm"]
            for vm in vm_indicators:
                if vm in output:
                    return True
    except Exception:
        pass
    return False


def security_check(strict=False):
    """
    Run all security checks.
    
    Args:
        strict: If True, also check for VMs (may block legitimate users)
    
    Returns:
        (is_safe: bool, reason: str)
    """
    if _check_debugger():
        return False, "debugger_detected"
    
    if _check_analysis_tools():
        return False, "analysis_tool_detected"
    
    if strict and _check_vm():
        return False, "vm_detected"
    
    return True, "ok"


def enforce_security():
    """Run security checks and exit if tampering detected."""
    # Only enforce in frozen (compiled) mode
    if not getattr(sys, 'frozen', False):
        return
    
    is_safe, reason = security_check(strict=False)
    if not is_safe:
        # Silent exit — don't tell attacker why
        sys.exit(1)
