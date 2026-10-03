import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ORB = APP_DIR / "desktop_orb.py"
VOICE = APP_DIR / "voice_pc_agent.py"

orb_process = None
voice_process = None

def start():
    global orb_process, voice_process
    if orb_process is None or orb_process.poll() is not None:
        orb_process = subprocess.Popen([sys.executable, str(ORB)], cwd=APP_DIR)
    if voice_process is None or voice_process.poll() is not None:
        voice_process = subprocess.Popen([sys.executable, str(VOICE)], cwd=APP_DIR)

def stop():
    global orb_process, voice_process
    for p in (voice_process, orb_process):
        if p and p.poll() is None:
            p.terminate()
    orb_process = None
    voice_process = None

def restart():
    stop()
    start()

def on_close():
    stop()
    root.destroy()

root = tk.Tk()
root.title("JARVIS Control")
root.geometry("320x220")
root.resizable(False, False)

tk.Label(root, text="J.A.R.V.I.S", font=("Segoe UI", 20, "bold")).pack(pady=(18, 4))
tk.Label(root, text="Desktop Agent Control", font=("Segoe UI", 10)).pack()

tk.Button(root, text="START JARVIS", width=24, command=start).pack(pady=(18, 6))
tk.Button(root, text="STOP JARVIS", width=24, command=stop).pack(pady=4)
tk.Button(root, text="RESTART", width=24, command=restart).pack(pady=4)

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
