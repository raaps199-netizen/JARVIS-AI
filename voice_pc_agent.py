"""
JARVIS Hands-Free PC Agent (Windows)
Voice input: Google Web Speech API via SpeechRecognition (internet required).
Voice output: Windows SAPI via pyttsx3 (local/offline).
Only supports an allowlist of actions; never executes arbitrary shell commands.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path

import pyttsx3
import speech_recognition as sr

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
FOLDERS = {
    "home": Path.home(),
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
}

recognizer = sr.Recognizer()
speaker = pyttsx3.init()
speaker.setProperty("rate", 172)
speaker.setProperty("volume", 1.0)


def say(text: str) -> None:
    print(f"JARVIS: {text}")
    speaker.say(text)
    speaker.runAndWait()


def open_app(name: str) -> str:
    key = name.lower()
    if key not in APPS:
        return "Aplikasi itu belum ada di daftar yang diizinkan, Sir."
    try:
        subprocess.Popen(APPS[key], shell=False)
        return f"Baik, Sir. Membuka {key}."
    except OSError:
        return f"Maaf, Sir. {key} tidak ditemukan. Pastikan aplikasinya terpasang."


def open_site(name: str) -> str:
    key = name.lower()
    if key not in SITES:
        return "Website itu belum ada di daftar yang diizinkan, Sir."
    webbrowser.open(SITES[key], new=2)
    return f"Baik, Sir. Membuka {key}."


def open_folder(name: str) -> str:
    key = name.lower()
    folder = FOLDERS.get(key)
    if folder is None:
        return "Folder itu belum ada di daftar yang diizinkan, Sir."
    if not folder.exists():
        return f"Maaf, Sir. Folder {key} tidak ditemukan."
    try:
        os.startfile(str(folder))
        return f"Baik, Sir. Membuka folder {key}."
    except OSError:
        return f"Maaf, Sir. Folder {key} gagal dibuka."


def pc_status() -> str:
    free_gb = shutil.disk_usage(Path.home()).free / (1024 ** 3)
    return (
        f"Sir, sistem operasi {platform.system()} {platform.release()}. "
        f"Nama komputer {platform.node()}. "
        f"Sisa ruang drive sekitar {free_gb:.1f} gigabyte."
    )


def handle_command(text: str) -> str | None:
    phrase = text.lower().strip()
    for prefix in ("jarvis", "hey jarvis", "hai jarvis"):
        phrase = phrase.replace(prefix, " ")
    phrase = " ".join(phrase.split()).strip(" .,!?")
    if not phrase:
        return None

    if any(x in phrase for x in ("berhenti mendengarkan", "matikan mode suara", "tidur jarvis", "shutdown jarvis")):
        say("Mode suara dihentikan, Sir.")
        raise SystemExit

    if any(x in phrase for x in ("status komputer", "status pc", "kondisi komputer", "kondisi pc")):
        return pc_status()

    # Explicit, constrained intents. No free-form command or shell execution.
    app_aliases = {
        "notepad": ("notepad", "catatan"),
        "calculator": ("kalkulator", "calculator", "kalkulator"),
        "chrome": ("chrome", "google chrome", "browser"),
        "vscode": ("visual studio code", "vs code", "vscode"),
        "explorer": ("file explorer", "explorer"),
    }
    for app, aliases in app_aliases.items():
        if any(a in phrase for a in aliases) and any(v in phrase for v in ("buka", "jalankan", "open", "nyalakan")):
            return open_app(app)

    site_aliases = {
        "youtube": ("youtube",),
        "google": ("google",),
        "github": ("github",),
    }
    if any(v in phrase for v in ("buka", "kunjungi", "open", "akses")):
        for site, aliases in site_aliases.items():
            if any(a in phrase for a in aliases):
                return open_site(site)

    if any(v in phrase for v in ("buka", "tampilkan", "open")):
        folder_aliases = {
            "downloads": ("download", "downloads"),
            "documents": ("dokumen", "documents"),
            "desktop": ("desktop", "layar utama"),
            "home": ("folder utama", "home"),
        }
        for folder, aliases in folder_aliases.items():
            if any(a in phrase for a in aliases):
                return open_folder(folder)

    return "Maaf, Sir. Perintah itu belum tersedia. Coba minta buka Notepad, YouTube, folder Downloads, atau cek status PC."


def main() -> None:
    if os.name != "nt":
        print("Peringatan: agent ini dirancang untuk Windows.")
    say("Sistem aktif, Sir. Saya siap mendengarkan. Katakan, Hey Jarvis, buka Notepad.")
    try:
        with sr.Microphone() as source:
            say("Mengkalibrasi mikrofon sebentar, Sir.")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            say("Mikrofon siap. Katakan perintah Anda.")
            while True:
                try:
                    print("Mendengarkan...")
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=7)
                    print("Memproses suara...")
                    try:
                        heard = recognizer.recognize_google(audio, language="id-ID")
                        print(f"Sir: {heard}")
                    except sr.UnknownValueError:
                        say("Maaf, Sir. Saya tidak menangkap perintahnya.")
                        continue
                    except sr.RequestError:
                        say("Koneksi pengenalan suara bermasalah, Sir. Periksa internet Anda.")
                        continue

                    response = handle_command(heard)
                    if response:
                        say(response)
                except KeyboardInterrupt:
                    say("Sampai jumpa, Sir.")
                    break
                except sr.WaitTimeoutError:
                    continue
    except OSError as exc:
        say(f"Mikrofon tidak bisa diakses, Sir. Periksa izin mikrofon Windows. Detail: {exc}")


if __name__ == "__main__":
    main()
