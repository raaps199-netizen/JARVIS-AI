"""JARVIS local desktop agent.

Microphone -> Gemini speech-to-text/reasoning -> tool calling -> PC action -> voice.
The PC tool set is intentionally allowlisted instead of exposing arbitrary shell access.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import wave
import webbrowser
import pyautogui
import pyperclip
from pywinauto import Desktop
from urllib.parse import quote_plus
from pathlib import Path
from typing import Any

import sounddevice as sd
import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq

from jarvis_tts import speak

load_dotenv()

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SAMPLE_RATE = 16000
RECORD_SECONDS = 6
MIN_RECORD_SECONDS = 0.35
SILENCE_SECONDS = 1.5
UI_DELAY_SECONDS = 0.8
START_TIMEOUT_SECONDS = 0
ENERGY_THRESHOLD = 120


SYSTEM_PROMPT = """
You are JARVIS, a local Windows desktop AI assistant.

SELALU jawab dalam bahasa Indonesia. Jangan beralih ke bahasa Inggris hanya karena
STT menangkap kata Inggris. Gunakan bahasa Inggris hanya jika user secara eksplisit
meminta output dalam bahasa Inggris. Use gue/lu naturally and address the user as
"Sir" occasionally, not every sentence.

You have access to a small set of local PC tools. Decide yourself when a tool is
needed. Do not claim an action happened unless its tool result says it succeeded.
After a tool runs, briefly explain the result to the user. Use only the exact tool names provided in the tool list. Never invent, modify, append channel labels to, or otherwise alter a tool name.

Available capabilities include opening approved applications, websites, folders,
typing text, pressing safe keyboard shortcuts, interacting with visible Windows UI
elements by their displayed text, scrolling, waiting for UI changes, and reading
basic non-sensitive UI status. When the user asks to operate a desktop application,
actually perform the requested UI steps instead of merely explaining them.

Do not invent access to files or system state. Do not execute destructive,
credential-stealing, surveillance, financial, or otherwise dangerous actions.
For actions that could delete data, shut down the machine, install unknown
software, or change security settings, do not execute them automatically.

Keep spoken answers concise. If the user asks a normal knowledge question,
answer it directly without calling a PC tool.
"""

APPS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "chrome": ["cmd", "/c", "start", "", "chrome"],
    "vscode": ["cmd", "/c", "start", "", "code"],
    "word": ["cmd", "/c", "start", "", "winword"],
    "explorer": ["explorer.exe"],
    "task manager": ["taskmgr.exe"],
    "settings": ["cmd", "/c", "start", "", "ms-settings:"],
}

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
}

FOLDERS = {
    "home": Path.home(),
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
}

TOOL_DECLARATIONS = [
    {"type":"function","function":{"name":"open_app","description":"Open an approved Windows application.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["notepad","calculator","chrome","vscode","word","explorer","task manager","settings"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"search_web","description":"Search Google for a requested topic.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"open_site","description":"Open an approved website.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["youtube","google","github","chatgpt"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"open_folder","description":"Open a common user folder.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["home","desktop","documents","downloads"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"type_text","description":"Type exact text into the currently focused desktop application when explicitly requested.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}},

    {"type":"function","function":{"name":"ui_click","description":"Click a visible Windows UI element by its displayed title/text. Use this for buttons, tabs, menus, dialogs, and controls such as Word's Blank document.","parameters":{"type":"object","properties":{"text":{"type":"string"},"window_title":{"type":"string"}},"required":["text"]}}},
    {"type":"function","function":{"name":"ui_inspect","description":"Inspect visible non-sensitive Windows UI controls so JARVIS can understand what is currently on screen before clicking. Do not use it to retrieve passwords or sensitive fields.","parameters":{"type":"object","properties":{"window_title":{"type":"string"}}}}},
    {"type":"function","function":{"name":"scroll_mouse","description":"Scroll the currently focused desktop UI.","parameters":{"type":"object","properties":{"clicks":{"type":"integer"}},"required":["clicks"]}}},
    {"type":"function","function":{"name":"wait_seconds","description":"Wait briefly for a desktop application or dialog to finish opening.","parameters":{"type":"object","properties":{"seconds":{"type":"number"}},"required":["seconds"]}}},
    {"type":"function","function":{"name":"press_key","description":"Press an allowlisted keyboard key or shortcut.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"close_active_window","description":"Close the currently focused Windows application/window using Alt+F4.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"close_app","description":"Close one specific approved application by its process/window, without closing whichever unrelated window happens to be focused. Use this whenever the user names a specific application to close.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["notepad","calculator","chrome","vscode","word","explorer","task manager"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"stop_jarvis","description":"Stop JARVIS listening and end the current assistant process when the user explicitly asks JARVIS to stop or turn itself off.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"pc_status","description":"Read basic non-sensitive computer status.","parameters":{"type":"object","properties":{}}}}
]

TOOLS = TOOL_DECLARATIONS


def open_app(name: str) -> str:
    if name not in APPS:
        return f"Aplikasi {name} belum tersedia."
    try:
        subprocess.Popen(APPS[name], shell=False)
        return f"Berhasil membuka {name}."
    except OSError as exc:
        return f"Gagal membuka {name}: {exc}"


def search_web(query: str) -> str:
    query = str(query).strip()
    if not query:
        return "Query pencarian kosong."
    try:
        url = "https://www.google.com/search?q=" + quote_plus(query)
        opened = webbrowser.open(url, new=2)
        return f"Berhasil mencari {query} di Google." if opened else "Browser menolak membuka hasil pencarian."
    except OSError as exc:
        return f"Gagal melakukan pencarian: {exc}"


def open_site(name: str) -> str:
    if name not in SITES:
        return f"Website {name} belum tersedia."
    try:
        opened = webbrowser.open(SITES[name], new=2)
        return f"Berhasil membuka {name}." if opened else f"Browser menolak membuka {name}."
    except OSError as exc:
        return f"Gagal membuka {name}: {exc}"


def open_folder(name: str) -> str:
    folder = FOLDERS.get(name)
    if folder is None:
        return f"Folder {name} belum tersedia."
    if not folder.exists():
        return f"Folder {name} tidak ditemukan."
    try:
        os.startfile(str(folder))
        return f"Berhasil membuka folder {name}."
    except OSError as exc:
        return f"Gagal membuka folder {name}: {exc}"


def pc_status() -> str:
    free_gb = shutil.disk_usage(Path.home()).free / (1024 ** 3)
    return (
        f"OS: {platform.system()} {platform.release()}; "
        f"komputer: {platform.node()}; "
        f"Python: {platform.python_version()}; "
        f"ruang kosong: {free_gb:.1f} GB."
    )


def type_text(text: str) -> str:
    text = str(text)
    if not text:
        return "Teks kosong."
    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return "Teks berhasil diketik ke jendela aktif."
    except Exception as exc:
        return f"Gagal mengetik teks: {exc}"


def press_key(key: str) -> str:
    aliases = {
        "enter": "enter",
        "esc": "esc",
        "escape": "esc",
        "tab": "tab",
        "backspace": "backspace",
        "delete": "delete",
        "home": "home",
        "end": "end",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "ctrl+s": "ctrl+s",
        "ctrl+n": "ctrl+n",
        "ctrl+a": "ctrl+a",
        "ctrl+c": "ctrl+c",
        "ctrl+v": "ctrl+v",
        "ctrl+x": "ctrl+x",
        "ctrl+z": "ctrl+z",
        "ctrl+h": "ctrl+h", "ctrl+f": "ctrl+f", "ctrl+p": "ctrl+p",
        "ctrl+shift+s": "ctrl+shift+s", "ctrl+shift+n": "ctrl+shift+n",
        "ctrl+shift+z": "ctrl+shift+z", "ctrl+enter": "ctrl+enter",
        "shift+enter": "shift+enter", "f2": "f2", "f4": "f4",
        "f5": "f5", "f11": "f11", "alt+f4": "alt+f4",
    }
    normalized = str(key).lower().replace(" ", "")
    mapped = aliases.get(normalized)
    if not mapped:
        return f"Tombol atau shortcut '{key}' tidak diizinkan."
    try:
        if "+" in mapped:
            pyautogui.hotkey(*mapped.split("+"))
        else:
            pyautogui.press(mapped)
        return f"Berhasil menekan {key}."
    except Exception as exc:
        return f"Gagal menekan {key}: {exc}"


def type_text(text: str) -> str:
    text = str(text)
    if not text:
        return "Teks kosong."
    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return "Teks berhasil diketik ke jendela aktif."
    except Exception as exc:
        return f"Gagal mengetik teks: {exc}"


def press_key(key: str) -> str:
    aliases = {
        "enter": "enter", "esc": "esc", "escape": "esc", "tab": "tab",
        "backspace": "backspace", "delete": "delete", "home": "home", "end": "end",
        "up": "up", "down": "down", "left": "left", "right": "right",
        "ctrl+s": "ctrl+s", "ctrl+n": "ctrl+n", "ctrl+a": "ctrl+a",
        "ctrl+c": "ctrl+c", "ctrl+v": "ctrl+v", "ctrl+x": "ctrl+x",
        "ctrl+z": "ctrl+z", "alt+f4": "alt+f4",
    }
    normalized = str(key).lower().replace(" ", "")
    mapped = aliases.get(normalized)
    if not mapped:
        return f"Tombol atau shortcut '{key}' tidak diizinkan."
    try:
        if "+" in mapped:
            pyautogui.hotkey(*mapped.split("+"))
        else:
            pyautogui.press(mapped)
        return f"Berhasil menekan {key}."
    except Exception as exc:
        return f"Gagal menekan {key}: {exc}"




def _ui_windows(window_title: str | None = None):
    desktop=Desktop(backend="uia"); windows=[]
    for win in desktop.windows(visible_only=True):
        try:
            title=(win.window_text() or "").strip()
            if title and (not window_title or window_title.lower() in title.lower()): windows.append(win)
        except Exception: pass
    return windows

def ui_inspect(window_title: str = "") -> str:
    try:
        windows=_ui_windows(window_title.strip() or None)
        if not windows: return "Tidak menemukan jendela UI yang cocok."
        lines=[]
        for win in windows[:6]:
            lines.append(f"WINDOW: {(win.window_text() or '').strip()}")
            count=0
            for ctrl in win.descendants():
                if count>=60: break
                try:
                    if getattr(ctrl.element_info,"is_password",False): continue
                    text=(ctrl.window_text() or "").strip().replace("\n"," ")
                    ctype=str(ctrl.element_info.control_type or "")
                    if text and ctype.lower() in {"text","button","edit","tabitem","menuitem","listitem","combobox","checkbox","radiobutton","hyperlink"}:
                        lines.append(f"  {ctype}: {text[:180]}"); count+=1
                except Exception: pass
        return "\n".join(lines)[:12000] if lines else "Tidak ada kontrol UI yang terbaca."
    except Exception as exc: return f"Gagal membaca UI Windows: {exc}"

def ui_click(text: str, window_title: str = "") -> str:
    target=str(text).strip()
    if not target: return "Teks target UI kosong."
    try:
        exact=[]; partial=[]
        for win in _ui_windows(window_title.strip() or None):
            for ctrl in win.descendants():
                try:
                    if getattr(ctrl.element_info,"is_password",False): continue
                    label=(ctrl.window_text() or "").strip()
                    if not label: continue
                    if label.casefold()==target.casefold(): exact.append(ctrl)
                    elif target.casefold() in label.casefold(): partial.append(ctrl)
                except Exception: pass
        candidates=exact or partial
        if not candidates: return f"Elemen UI '{target}' tidak ditemukan."
        ctrl=candidates[0]
        try: ctrl.scroll_into_view()
        except Exception: pass
        ctrl.click_input()
        return f"Berhasil klik '{(ctrl.window_text() or target).strip()}'."
    except Exception as exc: return f"Gagal klik UI '{target}': {exc}"

def scroll_mouse(clicks:int) -> str:
    try:
        value=max(-20,min(20,int(clicks))); pyautogui.scroll(value)
        return f"Berhasil scroll {value} klik."
    except Exception as exc: return f"Gagal scroll: {exc}"

def wait_seconds(seconds:float) -> str:
    try:
        value=max(0.1,min(5.0,float(seconds))); time.sleep(value)
        return f"Menunggu {value:.1f} detik selesai."
    except Exception as exc: return f"Gagal menunggu: {exc}"

def run_tool(name: str, arguments: dict[str, Any]) -> str:
    # Some model/tool adapters can occasionally append an internal channel marker.
    # Strip it before dispatching, but never execute arbitrary tool names.
    if isinstance(name, str) and "<|channel|>" in name:
        name = name.split("<|channel|>", 1)[0]
    if name == "open_app":
        return open_app(str(arguments["name"]))
    if name == "search_web":
        return search_web(str(arguments["query"]))
    if name == "open_site":
        return open_site(str(arguments["name"]))
    if name == "open_folder":
        return open_folder(str(arguments["name"]))
    if name == "type_text":
        return type_text(str(arguments["text"]))
    if name == "ui_click": return ui_click(str(arguments["text"]), str(arguments.get("window_title","")))
    if name == "ui_inspect": return ui_inspect(str(arguments.get("window_title","")))
    if name == "scroll_mouse": return scroll_mouse(int(arguments["clicks"]))
    if name == "wait_seconds": return wait_seconds(float(arguments["seconds"]))
    if name == "press_key":
        return press_key(str(arguments["key"]))
    if name == "pc_status":
        return pc_status()
    if name == "close_active_window":
        return close_active_window()
    if name == "close_app":
        return close_app(str(arguments["name"]))
    if name == "stop_jarvis":
        return stop_jarvis()
    return f"Tool {name} tidak dikenal."


def _windows_process_name_from_hwnd(hwnd: int) -> str:
    """Return the executable name owning a top-level window, if available."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return ""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def close_app(name: str) -> str:
    """Close one specific approved app without relying on the currently focused window."""
    exe_map = {
        "notepad": "notepad.exe",
        "calculator": "calculatorapp.exe",
        "chrome": "chrome.exe",
        "vscode": "code.exe",
        "word": "winword.exe",
        "explorer": "explorer.exe",
        "task manager": "taskmgr.exe",
    }
    target = exe_map.get(name)
    if not target:
        return f"Aplikasi {name} tidak bisa ditutup secara spesifik."
    if os.name != "nt":
        return "Penutupan aplikasi spesifik hanya didukung di Windows."

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    WM_SYSCOMMAND = 0x0112
    SC_CLOSE = 0xF060
    matches = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @EnumWindowsProc
    def enum_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        process_name = _windows_process_name_from_hwnd(hwnd)
        title_buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buffer, 512)
        title = title_buffer.value.strip().lower()

        matched = process_name == target
        if name == "calculator" and process_name == "applicationframehost.exe":
            matched = title in {"calculator", "kalkulator"} or "calculator" in title

        if matched:
            matches.append(hwnd)
        return True

    user32.EnumWindows(enum_window, 0)

    # First request a normal window close.
    for hwnd in matches:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0)

    time.sleep(0.7)

    def calculator_window_exists() -> bool:
        found = []
        @EnumWindowsProc
        def check_window(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            process_name = _windows_process_name_from_hwnd(hwnd)
            title_buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title_buffer, 512)
            title = title_buffer.value.strip().lower()
            if process_name == "calculatorapp.exe":
                found.append(hwnd)
            elif (
                name == "calculator"
                and process_name == "applicationframehost.exe"
                and (title in {"calculator", "kalkulator"} or "calculator" in title)
            ):
                found.append(hwnd)
            return True
        user32.EnumWindows(check_window, 0)
        return bool(found)

    if name == "calculator" and calculator_window_exists():
        # Calculator may be hosted by ApplicationFrameHost and ignore WM_CLOSE.
        # Use an exact Calculator window-title filter so we never close the
        # currently focused unrelated application.
        try:
            subprocess.run(
                ["taskkill", "/F", "/FI", "WINDOWTITLE eq Calculator"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            print(f"[CLOSE] Calculator fallback failed: {exc}")

        time.sleep(0.8)

    if name == "calculator" and calculator_window_exists():
        return "Saya menemukan Kalkulator, tetapi Windows tidak menutup jendelanya."

    if not matches and name != "calculator":
        return f"{name} tidak sedang terbuka."

    return f"Berhasil menutup {name}."




def close_active_window() -> str:
    try:
        pyautogui.hotkey("alt", "f4")
        return "Jendela aktif ditutup dengan Alt+F4."
    except Exception as exc:
        return f"Gagal menutup jendela aktif: {exc}"


def stop_jarvis() -> str:
    return "__JARVIS_STOP__"


def ask_agent(client: Groq, user_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    # Safety/precision guard: if the user explicitly names an approved app
    # together with "tutup", never let the LLM fall back to Alt+F4. Alt+F4 on
    # the desktop can open Windows' Shut Down dialog, which is not wanted when
    # closing a named application.
    normalized_request = " ".join(user_text.lower().strip().split())
    close_words = ("tutup ", "tutupkan ", "close ")
    close_app_aliases = {
        "kalkulator": "calculator",
        "calculator": "calculator",
        "notepad": "notepad",
        "chrome": "chrome",
        "google chrome": "chrome",
        "word": "word",
        "microsoft word": "word",
        "vscode": "vscode",
        "vs code": "vscode",
        "explorer": "explorer",
        "task manager": "task manager",
    }
    for prefix in close_words:
        if normalized_request.startswith(prefix):
            target_text = normalized_request[len(prefix):].strip()
            if target_text.endswith("nya"):
                target_text = target_text[:-3].strip()
            target_app = close_app_aliases.get(target_text)
            if target_app:
                result = close_app(target_app)
                print(f"[DIRECT] close_app({target_app}) -> {result}")
                return result

    for _ in range(6):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.4,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return (message.content or "").strip()

        messages.append(message)

        for tool_call in message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                result = run_tool(tool_call.function.name, arguments)
            except Exception as exc:
                result = f"Tool error: {exc}"

            print(f"[TOOL] {tool_call.function.name} -> {result}")
            if result == "__JARVIS_STOP__":
                return "__JARVIS_STOP__"
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    return "Saya berhenti setelah beberapa langkah tool agar tidak masuk loop."


def record_audio(path: Path) -> None:
    print("[MIC] Mendengarkan...")
    block_size = 1600
    max_blocks = int(RECORD_SECONDS * SAMPLE_RATE / block_size)
    min_blocks = max(1, int(MIN_RECORD_SECONDS * SAMPLE_RATE / block_size))
    silence_blocks = max(1, int(SILENCE_SECONDS * SAMPLE_RATE / block_size))
    start_timeout_blocks = max(1, int(START_TIMEOUT_SECONDS * SAMPLE_RATE / block_size))

    chunks = []
    started = False
    quiet_count = 0

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=block_size,
    ) as stream:
        # Wait indefinitely for the first real speech signal.
        # START_TIMEOUT_SECONDS=0 means "no timeout", not "wait one block".
        while not started:
            data, _ = stream.read(block_size)
            chunk = data.copy()
            energy = float(abs(chunk).mean())

            if energy >= ENERGY_THRESHOLD:
                started = True
                chunks.append(chunk)
                quiet_count = 0
                print("[MIC] Suara terdeteksi.")

        # Once speech starts, record until silence or the 6-second cap.
        remaining_blocks = max_blocks - 1
        for _ in range(max(0, remaining_blocks)):
            data, _ = stream.read(block_size)
            chunk = data.copy()
            energy = float(abs(chunk).mean())
            chunks.append(chunk)

            if energy < ENERGY_THRESHOLD:
                quiet_count += 1
                if len(chunks) >= min_blocks and quiet_count >= silence_blocks:
                    break
            else:
                quiet_count = 0

    if not chunks:
        raise RuntimeError("Tidak ada suara terdeteksi.")

    recording = __import__("numpy").concatenate(chunks, axis=0)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(recording.tobytes())


def try_direct_command(text: str) -> str | None:
    normalized = " ".join(text.lower().strip().split())
    # Whisper/Groq can mishear "Jarvis" as "Jervis", "Yervis", or "Surface".
    # Treat these common wake-name variants as the same command prefix.
    prefixes = ("tolong ", "jarvis ", "jervis ", "yervis ", "surface ", "service ", "sir ", "bisa ")

    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()

    app_aliases = {
        "chrome": "chrome",
        "google chrome": "chrome",
        "notepad": "notepad",
        "kalkulator": "calculator",
        "calculator": "calculator",
        "explorer": "explorer",
        "task manager": "task manager",
        "vscode": "vscode",
        "vs code": "vscode",
        "word": "word",
        "microsoft word": "word",
        "note": "notepad",
        "catatan": "notepad",
        "pengaturan": "settings",
        "settings": "settings",
    }

    if normalized.startswith("buka ") and normalized[5:].strip() in app_aliases:
        app = app_aliases[normalized[5:].strip()]
        result = open_app(app)
        print(f"[DIRECT] open_app -> {result}")
        return result

    # Handle common "buka X lalu/terus/dan cari Y" phrasing without Gemini.
    combined_search_markers = (" lalu cari ", " terus cari ", " dan cari ")
    if normalized.startswith("buka ") and any(marker in normalized for marker in combined_search_markers):
        for marker in combined_search_markers:
            if marker in normalized:
                app_part, query = normalized[5:].split(marker, 1)
                app = app_aliases.get(app_part.strip())
                query = query.strip()
                if app == "chrome" and query:
                    open_result = open_app("chrome")
                    search_result = search_web(query)
                    result = f"{open_result} {search_result}"
                    print(f"[DIRECT] chrome search -> {result}")
                    return result

    intro_phrases = {
        "ceritain tentang diri lu", "ceritakan tentang diri lu",
        "ceritain tentang diri lo", "ceritakan tentang diri lo",
        "kenalin diri lu", "kenalkan diri lu", "kenalin diri lo", "kenalkan diri lo",
        "ceritain tentang diri kamu", "ceritakan tentang diri kamu",
        "siapa kamu", "lu siapa", "lo siapa", "kamu siapa",
    }
    if normalized in intro_phrases:
        result = "Gue JARVIS, asisten desktop lokal lu. Gue bisa buka aplikasi, cari di web, mengetik, menekan shortcut yang diizinkan, menutup aplikasi tertentu, membaca status PC dasar, dan menjalankan perintah desktop yang aman."
        print("[DIRECT] self_intro -> JARVIS")
        return result

    stop_phrases = {
        "matikan jarvis", "matikan diri", "matikan diri sendiri", "matikan dirimu", "matikan diri anda", "matikan dirimu sendiri",
        "matikan diri lu", "matikan diri lo", "matikan diri sendiri lu",
        "matikan diri sendiri lo", "stop jarvis", "shutdown jarvis",
        "matikan diri sendiri", "matikan jervis", "matikan yervis", "matikan surface",
    }
    if normalized in stop_phrases:
        print("[DIRECT] stop_jarvis -> JARVIS dihentikan.")
        return "__JARVIS_STOP__"

    for click_prefix in ("klik ", "click "):
        if normalized.startswith(click_prefix):
            target=normalized[len(click_prefix):].strip()
            if target:
                result=ui_click(target); print(f"[DIRECT] ui_click -> {result}"); return result
    if normalized in {"lihat layar","baca layar","cek layar","lihat jendela"}:
        result=ui_inspect(); print("[DIRECT] ui_inspect -> layar dibaca"); return result
    if normalized.startswith("scroll "):
        direction=normalized[7:].strip()
        amount=5 if direction in {"bawah","down"} else -5 if direction in {"atas","up"} else 0
        if amount:
            result=scroll_mouse(amount); print(f"[DIRECT] scroll_mouse -> {result}"); return result
    typing_prefixes = ("ketik ", "tulis ", "ketikkan ")
    original_clean = " ".join(text.strip().split())
    original_lower = original_clean.lower()
    for prefix in typing_prefixes:
        if original_lower.startswith(prefix):
            typed = original_clean[len(prefix):].strip()
            if typed.startswith(","):
                typed = typed[1:].strip()
            if typed:
                result = type_text(typed)
                print(f"[DIRECT] type_text -> {result}")
                return result

    action_markers = (" dan ketik ", " lalu ketik ", " terus ketik ", " dan tulis ", " lalu tulis ")
    if normalized.startswith("buka "):
        for marker in action_markers:
            if marker in normalized:
                app_part, text = normalized[5:].split(marker, 1)
                app = app_aliases.get(app_part.strip())
                text = text.strip()
                if app and text:
                    open_result = open_app(app)
                    time.sleep(UI_DELAY_SECONDS)
                    type_result = type_text(text)
                    result = f"{open_result} {type_result}"
                    print(f"[DIRECT] open + type -> {result}")
                    return result

    close_aliases = {
        "notepad": "notepad",
        "calculator": "calculator",
        "kalkulator": "calculator",
        "chrome": "chrome",
        "google chrome": "chrome",
        "word": "word",
        "microsoft word": "word",
        "vscode": "vscode",
        "vs code": "vscode",
        "explorer": "explorer",
        "task manager": "task manager",
    }
    close_target = normalized[6:].strip() if normalized.startswith("tutup ") else ""
    if close_target.endswith("nya"):
        close_target = close_target[:-3].strip()
    if close_target in close_aliases:
        app = close_aliases[close_target]
        result = close_app(app)
        print(f"[DIRECT] close_app({app}) -> {result}")
        return result
    if normalized in {"tutup", "tutup jendela", "tutup aplikasi"}:
        result = close_active_window()
        print(f"[DIRECT] close_active_window -> {result}")
        return result

    key_aliases = {
        "enter": "enter", "tekan enter": "enter", "escape": "esc", "esc": "esc",
        "tab": "tab", "hapus": "backspace", "backspace": "backspace",
        "ctrl s": "ctrl+s", "ctrl n": "ctrl+n", "ctrl a": "ctrl+a",
        "ctrl c": "ctrl+c", "ctrl v": "ctrl+v", "ctrl z": "ctrl+z",
        "alt f4": "alt+f4",
    }
    if normalized in key_aliases:
        result = press_key(key_aliases[normalized])
        print(f"[DIRECT] press_key -> {result}")
        return result

    search_prefixes = (
        "cari tentang ",
        "cari ",
        "search tentang ",
        "search ",
        "google tentang ",
        "google ",
        "buka chrome dan cari tentang ",
        "buka chrome lalu cari tentang ",
        "buka chrome terus cari tentang ",
        "buka chrome dan cari ",
        "buka chrome lalu cari ",
        "buka chrome terus cari ",
    )

    for prefix in search_prefixes:
        if normalized.startswith(prefix):
            query = normalized[len(prefix):].strip()
            for suffix in (" di website", " di web", " lewat website", " lewat web", " di google"):
                if query.endswith(suffix):
                    query = query[:-len(suffix)].strip()
                    break
            if query:
                result = search_web(query)
                print(f"[DIRECT] search_web -> {result}")
                return result

    return None


def transcribe(client: Groq, path: Path) -> str:
    try:
        with open(path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(path.name, audio_file.read()),
                model=STT_MODEL,
                language="id",
                response_format="text",
                temperature=0,
            )
        text = str(transcription).strip()
        if text:
            print(f"[STT] Groq: {text}")
        return text
    except Exception as exc:
        print(f"[STT] Groq gagal: {exc}")
        return ""


def main() -> None:
    if not GROQ_API_KEY:
        print("GROQ_API_KEY belum diatur.")
        return

    client = Groq(api_key=GROQ_API_KEY)
    speak("Sistem aktif, Sir. Saya siap mendengarkan.")
    audio_path = Path(__file__).resolve().with_name(".jarvis_input.wav")

    try:
        while True:
            try:
                record_audio(audio_path)
                heard = transcribe(client, audio_path)
            except (OSError, sd.PortAudioError) as exc:
                print(f"[MIC] {exc}")
                speak("Mikrofon tidak bisa diakses. Periksa perangkat audio Windows.")
                time.sleep(2)
                continue
            except Exception as exc:
                print(f"[STT] {exc}")
                speak("Pengenalan suara gagal. Periksa koneksi dan GROQ API key.")
                time.sleep(2)
                continue

            if not heard:
                continue

            print(f"Sir: {heard}")
            normalized = " ".join(heard.lower().strip().split())
            normalized = normalized.strip(".,!?;:")

            shutdown_phrases = {
                "shutdown jarvis",
                "matikan jarvis",
                "matikan diri",
                "matikan diri sendiri",
                "matikan dirimu",
                "matikan diri lu",
                "matikan diri lo",
                "berhenti mendengarkan",
                "matikan mode suara",
                "stop jarvis",
                "stop jervis",
                "stop yervis",
                "stop diri sendiri",
                "jervis matikan diri lo",
                "yervis matikan diri lo",
                "surface matikan diri lo",
            }

            shutdown_words = set(normalized.split())
            has_app_target = bool(shutdown_words & {
                "kalkulator", "calculator", "notepad", "chrome", "word",
                "vscode", "explorer", "aplikasi", "jendela"
            })
            shutdown_intent = (
                normalized in shutdown_phrases
                or (
                    not has_app_target
                    and ("matikan" in shutdown_words)
                    and bool(shutdown_words & {"jarvis", "jervis", "yervis", "surface"})
                )
                or normalized.startswith("matikan diri")
                or normalized.startswith("stop jarvis")
                or normalized.startswith("stop jervis")
                or normalized.startswith("stop yervis")
            )

            if shutdown_intent:
                print("[JARVIS] Perintah shutdown diterima. Menghentikan proses agent...")
                speak("Baik, Sir. Saya mematikan sistem JARVIS.")
                return

            direct_reply = try_direct_command(heard)
            if direct_reply is not None:
                if direct_reply == "__JARVIS_STOP__":
                    print("[JARVIS] Perintah shutdown diterima. Menghentikan proses agent...")
                    speak("Baik, Sir. Saya mematikan sistem JARVIS.")
                    return
                speak(direct_reply)
                continue

            try:
                reply = ask_agent(client, heard)
                if reply == "__JARVIS_STOP__":
                    speak("Baik, Sir. Saya mematikan sistem JARVIS.")
                    break
                if reply:
                    speak(reply)
            except KeyboardInterrupt:
                print("\n[JARVIS] Dihentikan dari keyboard.")
                break
            except Exception as exc:
                print(f"[AGENT] {exc}")
                speak("Saya gagal memproses permintaan itu, Sir. Periksa log terminal.")

    except KeyboardInterrupt:
        print("\n[JARVIS] Dihentikan dari keyboard.")
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
