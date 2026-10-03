"""JARVIS local agent harness.

Connects microphone -> speech recognition -> OpenAI reasoning -> allowlisted PC tools -> speech.
This is the first real local agent layer; the visual orb remains separate.
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
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = os.getenv("JARVIS_MODEL", "gpt-6-luna")
TRANSCRIPTION_LANGUAGE = "id-ID"

SYSTEM_PROMPT = """
You are JARVIS, a local desktop AI assistant for the user.
Speak natural casual Indonesian by default, using gue/lu when appropriate.
Address the user as "Sir" occasionally, not every sentence.
Be concise, smart, practical, and conversational.
You can decide which local tool to use when a request matches an available tool.
Never claim an action succeeded unless the tool returned success.
If a request cannot be performed by the available tools, say so plainly.
Never invent access to files, apps, websites, or system state.
For dangerous, destructive, privacy-sensitive, financial, or irreversible actions, do not execute them automatically.
"""

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
    key = name.lower().strip()
    if key not in APPS:
        return f"Aplikasi {key} belum tersedia."
    try:
        subprocess.Popen(APPS[key], shell=False)
        return f"Membuka {key}."
    except OSError as exc:
        return f"Gagal membuka {key}: {exc}"


def open_site(name: str) -> str:
    key = name.lower().strip()
    if key not in SITES:
        return f"Website {key} belum tersedia."
    webbrowser.open(SITES[key], new=2)
    return f"Membuka {key} di browser."


def open_folder(name: str) -> str:
    key = name.lower().strip()
    folder = FOLDERS.get(key)
    if folder is None:
        return f"Folder {key} belum tersedia."
    if not folder.exists():
        return f"Folder {key} tidak ditemukan."
    try:
        os.startfile(str(folder))
        return f"Membuka folder {key}."
    except OSError as exc:
        return f"Gagal membuka folder {key}: {exc}"


def pc_status() -> str:
    free_gb = shutil.disk_usage(Path.home()).free / (1024 ** 3)
    return (
        f"OS {platform.system()} {platform.release()}, "
        f"komputer {platform.node()}, "
        f"Python {platform.python_version()}, "
        f"sisa ruang sekitar {free_gb:.1f} GB."
    )


def execute_simple_tool_request(text: str) -> str | None:
    phrase = text.lower().strip()

    if any(v in phrase for v in ("status komputer", "status pc", "kondisi komputer", "kondisi pc")):
        return pc_status()

    if any(v in phrase for v in ("buka", "jalankan", "open", "nyalakan")):
        aliases = {
            "notepad": "notepad",
            "catatan": "notepad",
            "kalkulator": "calculator",
            "calculator": "calculator",
            "chrome": "chrome",
            "google chrome": "chrome",
            "browser": "chrome",
            "vscode": "vscode",
            "vs code": "vscode",
            "visual studio code": "vscode",
            "explorer": "explorer",
            "file explorer": "explorer",
        }
        for alias, app in aliases.items():
            if alias in phrase:
                return open_app(app)

        for site, aliases in {
            "youtube": ("youtube",),
            "google": ("google",),
            "github": ("github",),
        }.items():
            if any(alias in phrase for alias in aliases):
                return open_site(site)

        for folder, aliases in {
            "downloads": ("download", "downloads"),
            "documents": ("dokumen", "documents"),
            "desktop": ("desktop", "layar utama"),
            "home": ("folder utama", "home"),
        }.items():
            if any(alias in phrase for alias in aliases):
                return open_folder(folder)

    return None


def ask_ai(client: OpenAI, user_text: str) -> str:
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_text,
    )
    return response.output_text.strip()


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY belum diatur.")
        return

    client = OpenAI(api_key=api_key)

    say("Sistem aktif, Sir. Saya siap mendengarkan.")

    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)
            say("Mikrofon siap.")

            while True:
                print("Mendengarkan...")
                try:
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=10)
                    heard = recognizer.recognize_google(
                        audio,
                        language=TRANSCRIPTION_LANGUAGE,
                    ).strip()
                except sr.UnknownValueError:
                    continue
                except sr.RequestError:
                    say("Layanan pengenalan suara sedang bermasalah. Periksa koneksi internet.")
                    continue

                if not heard:
                    continue

                print(f"Sir: {heard}")

                if heard.lower().strip() in {
                    "shutdown jarvis",
                    "matikan jarvis",
                    "berhenti mendengarkan",
                    "matikan mode suara",
                }:
                    say("Mode suara dihentikan, Sir.")
                    break

                local_result = execute_simple_tool_request(heard)
                if local_result is not None:
                    say(local_result)
                    continue

                try:
                    reply = ask_ai(client, heard)
                    say(reply)
                except Exception as exc:
                    say(f"Saya gagal memproses permintaan itu, Sir. {exc}")

    except OSError as exc:
        say(f"Mikrofon tidak bisa diakses. {exc}")


if __name__ == "__main__":
    main()
