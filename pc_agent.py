"""
JARVIS PC Agent - Stage 1 (safe local prototype)
Runs on the user's Windows laptop, not on Vercel.
Only performs a small allowlist of local actions; never executes arbitrary shell commands.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path


APPS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "chrome": ["cmd", "/c", "start", "", "chrome"],
    "vscode": ["cmd", "/c", "start", "", "code"],
    "explorer": ["explorer.exe"],
}

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
}


def open_app(name: str) -> str:
    key = name.strip().lower()
    if key not in APPS:
        return f"App belum diizinkan: {key}. Pilihan: {', '.join(APPS)}"
    try:
        subprocess.Popen(APPS[key], shell=False)
        return f"Membuka {key}."
    except OSError as exc:
        return f"Gagal membuka {key}: {exc}. Pastikan aplikasinya terpasang dan ada di PATH."


def open_site(name: str) -> str:
    key = name.strip().lower()
    if key not in SITES:
        return f"Website belum diizinkan: {key}. Pilihan: {', '.join(SITES)}"
    webbrowser.open(SITES[key], new=2)
    return f"Membuka {key} di browser."


def open_folder(name: str) -> str:
    folders = {
        "home": Path.home(),
        "desktop": Path.home() / "Desktop",
        "documents": Path.home() / "Documents",
        "downloads": Path.home() / "Downloads",
    }
    key = name.strip().lower()
    folder = folders.get(key)
    if folder is None:
        return f"Folder belum diizinkan: {key}. Pilihan: {', '.join(folders)}"
    if not folder.exists():
        return f"Folder tidak ditemukan: {folder}"
    try:
        os.startfile(str(folder))  # Windows only
        return f"Membuka folder {key}."
    except (AttributeError, OSError) as exc:
        return f"Gagal membuka folder: {exc}"


def pc_status() -> str:
    home_free = shutil.disk_usage(Path.home()).free / (1024 ** 3)
    return (
        f"OS: {platform.system()} {platform.release()}\n"
        f"PC: {platform.node()}\n"
        f"Python: {platform.python_version()}\n"
        f"Sisa ruang drive lokasi home: {home_free:.1f} GB"
    )


def main() -> None:
    print("JARVIS PC Agent - Stage 1")
    print("Ketik: app <notepad|calculator|chrome|vscode|explorer>")
    print("       site <youtube|google|github>")
    print("       folder <home|desktop|documents|downloads>")
    print("       status")
    print("       help / exit")
    while True:
        try:
            raw = input("\nSir > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nJARVIS: Sampai jumpa, Sir.")
            break
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        if command in {"exit", "quit"}:
            print("JARVIS: Agent dihentikan, Sir.")
            break
        elif command == "help":
            print("Contoh: app notepad | site youtube | folder downloads | status")
        elif command == "status":
            print(pc_status())
        elif command == "app" and argument:
            print(open_app(argument))
        elif command == "site" and argument:
            print(open_site(argument))
        elif command == "folder" and argument:
            print(open_folder(argument))
        else:
            print("Perintah tidak dikenal. Ketik help. Perintah bebas/shell tidak didukung.")


if __name__ == "__main__":
    if os.name != "nt":
        print("Catatan: PC Agent ini ditujukan untuk Windows.")
    main()
