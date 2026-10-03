"""JARVIS desktop control panel.

Starts/stops/restarts the actual agent harness and visual orb.
The optional Windows startup toggle creates a small .bat launcher in the
current user's Startup folder.
"""
from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
AGENT = APP_DIR / "agent_harness.py"
ORB = APP_DIR / "desktop_orb.py"
STARTUP_DIR = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
STARTUP_FILE = STARTUP_DIR / "JARVIS.bat"

agent_process = None
orb_process = None
root = None
status_var = None
startup_var = None


def _running(process):
    return process is not None and process.poll() is None


def start():
    global agent_process, orb_process

    if not _running(agent_process):
        agent_process = subprocess.Popen([sys.executable, str(AGENT)], cwd=APP_DIR)

    if not _running(orb_process):
        orb_process = subprocess.Popen([sys.executable, str(ORB)], cwd=APP_DIR)

    status_var.set("JARVIS: ONLINE")


def stop():
    global agent_process, orb_process

    for process in (agent_process, orb_process):
        if _running(process):
            process.terminate()

    agent_process = None
    orb_process = None
    status_var.set("JARVIS: OFFLINE")


def restart():
    stop()
    root.after(300, start)


def startup_enabled():
    return STARTUP_FILE.exists()


def set_startup(enabled: bool):
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)

    if enabled:
        python_exe = Path(sys.executable).resolve()
        content = (
            "@echo off\n"
            f'cd /d "{APP_DIR}"\n'
            f'"{python_exe}" "{APP_DIR / "jarvis_launcher.py"}" --autostart\n'
        )
        STARTUP_FILE.write_text(content, encoding="utf-8")
        startup_var.set(True)
    else:
        if STARTUP_FILE.exists():
            STARTUP_FILE.unlink()
        startup_var.set(False)


def on_close():
    stop()
    root.destroy()


def launch_control_panel():
    global root, status_var, startup_var

    root = tk.Tk()
    root.title("JARVIS Control")
    root.geometry("360x310")
    root.resizable(False, False)

    tk.Label(root, text="J.A.R.V.I.S", font=("Segoe UI", 22, "bold")).pack(pady=(18, 2))
    tk.Label(root, text="Desktop Agent Control", font=("Segoe UI", 10)).pack()

    status_var = tk.StringVar(value="JARVIS: OFFLINE")
    tk.Label(root, textvariable=status_var, font=("Segoe UI", 11, "bold")).pack(pady=(14, 10))

    tk.Button(root, text="START JARVIS", width=28, command=start).pack(pady=4)
    tk.Button(root, text="STOP JARVIS", width=28, command=stop).pack(pady=4)
    tk.Button(root, text="RESTART", width=28, command=restart).pack(pady=4)

    startup_var = tk.BooleanVar(value=startup_enabled())
    tk.Checkbutton(
        root,
        text="Start JARVIS with Windows",
        variable=startup_var,
        command=lambda: set_startup(startup_var.get()),
    ).pack(pady=(14, 4))

    tk.Label(root, text="Close = stop agent + orb", font=("Segoe UI", 8)).pack()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    if "--autostart" in sys.argv:
        # No control window at Windows login.
        # The user can launch this file normally to open the control panel.
        status_var = type("StartupStatus", (), {"set": lambda self, value: None})()
        start()
    else:
        launch_control_panel()
