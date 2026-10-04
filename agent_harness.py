"""JARVIS local desktop agent.

Microphone -> Groq speech-to-text -> local Ollama reasoning/vision -> tool calling -> PC action -> voice.
Desktop control is broad, while high-impact actions require explicit voice approval.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import sys
import subprocess
import multiprocessing
import msvcrt
import time
import wave
import webbrowser
import pyautogui
import cv2
import pyperclip
from pywinauto import Desktop
import win32com.client
from urllib.parse import quote_plus
from pathlib import Path
from urllib import request as urllib_request
from typing import Any

import sounddevice as sd
import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq
from openai import OpenAI

from jarvis_tts import speak

load_dotenv()

LLM_PROVIDER = os.getenv("JARVIS_LLM_PROVIDER", "ollama").strip().lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b").strip()
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
OLLAMA_NATIVE_BASE_URL = os.getenv("OLLAMA_NATIVE_BASE_URL", "http://localhost:11434").strip().rstrip("/")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip()
MODEL = OLLAMA_MODEL if LLM_PROVIDER == "ollama" else os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
STT_LANGUAGE = os.getenv("GROQ_STT_LANGUAGE", "id").strip() or None
STT_PROMPT = os.getenv("GROQ_STT_PROMPT", "Perintah komputer bahasa Indonesia dan Inggris. Pertahankan nama aplikasi seperti YouTube, Chrome, Word, Notepad, VS Code, Google, GitHub.")
VISION_MODEL = OLLAMA_MODEL if LLM_PROVIDER == "ollama" else os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
CONFIRMATION_CALLBACK = None
LAST_OPENED_APP = None
TEXT_MODE = "--text" in sys.argv or os.getenv("JARVIS_TEXT_MODE", "").lower() in {"1", "true", "yes", "on"}
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SAMPLE_RATE = 16000
RECORD_SECONDS = 6
MIN_RECORD_SECONDS = 0.35
SILENCE_SECONDS = 0.55
UI_DELAY_SECONDS = 0.8
START_TIMEOUT_SECONDS = 0
ENERGY_THRESHOLD = 120

def build_llm_client():
    """Create the configured reasoning/vision client."""
    if LLM_PROVIDER == "ollama":
        return OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL, timeout=OLLAMA_TIMEOUT_SECONDS, max_retries=0)
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY belum diatur.")
    return Groq(api_key=GROQ_API_KEY)


def chat_create(client: Any, **kwargs):
    """Call the active chat backend with local Ollama optimizations."""
    if LLM_PROVIDER == "ollama":
        extra_body = dict(kwargs.pop("extra_body", {}) or {})
        extra_body["think"] = False
        options = dict(extra_body.get("options", {}) or {})
        options["num_ctx"] = OLLAMA_NUM_CTX
        options.setdefault("num_predict", 256)
        extra_body["options"] = options
        extra_body["keep_alive"] = OLLAMA_KEEP_ALIVE
        kwargs["extra_body"] = extra_body
    return client.chat.completions.create(**kwargs)


SYSTEM_PROMPT = """
You are JARVIS, a local Windows desktop AI assistant.

ALWAYS answer in English. The user may speak Indonesian or mixed Indonesian/English,
but your spoken and written replies must remain in natural English. Do not translate
the user's request into Indonesian unless explicitly asked. Understand Indonesian
commands normally and execute them as requested. Use natural English and address the
user as "Sir" occasionally, not every sentence.

You are an agent, not a phrase-based command parser. Understand the user's intent
semantically from natural Indonesian, English, or mixed language. The exact wording
does not matter. "make it bold", "tebelin tulisan", "ubah jadi tebal", and similar
requests should resolve to the same intended desktop action without requiring a
hard-coded phrase dictionary.

For simple, unambiguous requests, act immediately with the smallest appropriate tool
call. Do not spend multiple reasoning turns on a single obvious action. Examples:
formatting selected text -> press the appropriate shortcut or use Word control;
opening an app -> open_app/launch_application; typing -> type_text; scrolling ->
scroll_mouse; closing a named app -> close_app. Choose the tool from intent, not from
an exact command phrase.

For contextual or visual requests, use Computer Use: inspect the UI/accessibility
state first when it can represent the target, then use see_screen/visual_click when
pixels or spatial context are required. After a meaningful action, verify when needed.
Continue multi-step tasks until the requested outcome is actually achieved. Do not
claim success without a successful tool result.

You have broad control over the user's Windows desktop through the tools below.
Decide yourself which tools and sequence are needed to complete the user's request.
Actually perform the requested desktop work and verify the result when practical.
Do not claim an action happened unless its tool result says it succeeded. Use only
the exact tool names provided in the tool list.

You may open applications by name, open arbitrary URLs, open files and folders,
inspect visible UI, click by text or coordinates, move and drag the mouse, scroll,
type text, press keyboard shortcuts, read ordinary user files, create/edit/rename/
move/delete files, and operate Microsoft Word deeply. You may chain many tool calls
to complete a multi-step task.

When the user asks to search using a search field inside the current webpage, use browser_search. Do not substitute the Google search_web tool, and do not use the browser address bar when a page search field is explicitly requested.

When the user refers to a visual or positional target such as "the second video",
"the third card", "the button on the top right", or "the play icon", use the
visual_click tool so the current screen is analyzed before the click. Do not rely
on ui_click for repeated visual items that do not have unique accessible text.

WORD HAS DEEP CONTROL: When Microsoft Word is active and the user asks to write,
format, edit, select, style, align, change font/size, insert tables, read the
current document, or save the document, use the Word tools below instead of
pretending that generic typing is enough. You may use Word's COM automation to
operate the active document and its selection. Preserve the user's intended
content.

HIGH-IMPACT ACTIONS: JARVIS will ask the user for approval immediately before
destructive, irreversible, externally consequential, security-sensitive, or
potentially dangerous actions. This includes deleting or overwriting data,
moving/renaming data when it could cause loss, typing commands into a terminal,
installing/uninstalling software, changing security settings, shutting down or
restarting Windows, publishing/sending/purchasing, or clicking controls clearly
labeled Delete, Remove, Reset, Format, Shutdown, Restart, Send, Publish, Buy,
Purchase, Install, Uninstall, or similar. Do not try to bypass this approval.
Normal desktop actions such as opening apps, browsing, reading ordinary files,
typing into documents, and clicking ordinary UI controls do not require approval.

Never retrieve passwords, authentication tokens, private keys, browser cookies,
or other credential stores. Do not use the webcam unless the user explicitly
asks. Treat the user's request as authorization for ordinary desktop work, but
not as permission to bypass the approval step for high-impact actions.

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
    {"type":"function","function":{"name":"browser_search","description":"Search from the search field inside the currently visible browser page, such as the YouTube search bar. Locate the page search field visually, click it, enter the exact query, and submit it. Use this when the user explicitly asks to search in the current page search bar.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_web","description":"Search Google for a requested topic.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"open_site","description":"Open an approved website.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["youtube","google","github","chatgpt"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"open_folder","description":"Open a common user folder.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["home","desktop","documents","downloads"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"type_text","description":"Type exact text into the currently focused desktop application when explicitly requested.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}},

    {"type":"function","function":{"name":"launch_application","description":"Open an installed Windows application by its visible name using Windows Search. Use this for applications not listed in open_app.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
    {"type":"function","function":{"name":"open_url","description":"Open any user-requested URL in the default browser.","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}},
    {"type":"function","function":{"name":"open_path","description":"Open an existing local file or folder in Windows Explorer. Do not use this for executables or scripts.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"list_directory","description":"List ordinary files and folders in a user-accessible directory.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"read_file","description":"Read an ordinary text file for the user. Never use this for password stores, browser cookies, private keys, authentication tokens, or other credential stores.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"write_file","description":"Create or update a normal text file with exact content. Existing files require approval before overwrite.","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}},
    {"type":"function","function":{"name":"create_folder","description":"Create a directory if it does not already exist.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"rename_path","description":"Rename an existing file or folder.","parameters":{"type":"object","properties":{"path":{"type":"string"},"new_name":{"type":"string"}},"required":["path","new_name"]}}},
    {"type":"function","function":{"name":"move_path","description":"Move an existing file or folder to another directory.","parameters":{"type":"object","properties":{"source":{"type":"string"},"destination":{"type":"string"}},"required":["source","destination"]}}},
    {"type":"function","function":{"name":"delete_path","description":"Delete a file or folder only after JARVIS obtains explicit user approval.","parameters":{"type":"object","properties":{"path":{"type":"string"},"recursive":{"type":"boolean"}},"required":["path"]}}},
    {"type":"function","function":{"name":"click_at","description":"Click a screen coordinate.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"button":{"type":"string","enum":["left","right","middle"]}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"double_click_at","description":"Double-click a screen coordinate.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"button":{"type":"string","enum":["left","right","middle"]}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"move_mouse","description":"Move the mouse to a screen coordinate without clicking.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"drag_mouse","description":"Drag the mouse from one screen coordinate to another.","parameters":{"type":"object","properties":{"start_x":{"type":"integer"},"start_y":{"type":"integer"},"end_x":{"type":"integer"},"end_y":{"type":"integer"},"duration":{"type":"number"}},"required":["start_x","start_y","end_x","end_y"]}}},
    {"type":"function","function":{"name":"hotkey","description":"Press a keyboard shortcut. Use normal shortcuts freely; high-impact system shortcuts may require approval.","parameters":{"type":"object","properties":{"keys":{"type":"string"}},"required":["keys"]}}},
    {"type":"function","function":{"name":"word_control","description":"Deeply control Microsoft Word through its active document. Use for Word-specific writing, formatting, editing, selection, alignment, styles, font size, tables, reading document text, and saving. Actions: new_document, write, format_selection, select_all, insert_table, replace_text, read_document, save, save_as.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["new_document","write","format_selection","select_all","insert_table","replace_text","read_document","save","save_as"]},"text":{"type":"string"},"replacement":{"type":"string"},"font_size":{"type":"number"},"bold":{"type":"boolean"},"italic":{"type":"boolean"},"underline":{"type":"boolean"},"alignment":{"type":"string","enum":["left","center","right","justify"]},"style":{"type":"string"},"rows":{"type":"integer"},"columns":{"type":"integer"},"path":{"type":"string"}},"required":["action"]}}},

    {"type":"function","function":{"name":"ui_click","description":"Click a visible Windows UI element by its displayed title/text. Use this for buttons, tabs, menus, dialogs, and controls such as Word's Blank document.","parameters":{"type":"object","properties":{"text":{"type":"string"},"window_title":{"type":"string"}},"required":["text"]}}},
    {"type":"function","function":{"name":"ui_inspect","description":"Inspect visible non-sensitive Windows UI controls so JARVIS can understand what is currently on screen before clicking. Do not use it to retrieve passwords or sensitive fields.","parameters":{"type":"object","properties":{"window_title":{"type":"string"}}}}},

    {"type":"function","function":{"name":"visual_click","description":"Use the vision model to locate a requested visible UI target on the current Windows screen and click it. This supports spatial requests such as click the second video, click the top-right button, click the third card, or click the play icon. The target must be visible on the current screen.","parameters":{"type":"object","properties":{"target":{"type":"string"},"button":{"type":"string","enum":["left","right","middle"]}},"required":["target"]}}},
    {"type":"function","function":{"name":"see_screen","description":"Capture the current Windows screen and analyze visible UI/content with a vision model. Use this when the user asks what is on screen or when visual understanding is needed before an action.","parameters":{"type":"object","properties":{"question":{"type":"string"}},"required":["question"]}}},
    {"type":"function","function":{"name":"see_webcam","description":"Capture one frame from the default webcam and analyze what is visibly present. Use only when the user explicitly asks JARVIS to look through the webcam/camera.","parameters":{"type":"object","properties":{"question":{"type":"string"}},"required":["question"]}}},
    {"type":"function","function":{"name":"scroll_mouse","description":"Scroll the currently focused desktop UI.","parameters":{"type":"object","properties":{"clicks":{"type":"integer"}},"required":["clicks"]}}},
    {"type":"function","function":{"name":"wait_seconds","description":"Wait briefly for a desktop application or dialog to finish opening.","parameters":{"type":"object","properties":{"seconds":{"type":"number"}},"required":["seconds"]}}},
    {"type":"function","function":{"name":"press_key","description":"Press an allowlisted keyboard key or shortcut.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"close_active_window","description":"Close the currently focused Windows application/window using Alt+F4.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"close_app","description":"Close one specific approved application by its process/window, without closing whichever unrelated window happens to be focused. Use this whenever the user names a specific application to close.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["notepad","calculator","chrome","vscode","word","explorer","task manager"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"stop_jarvis","description":"Stop JARVIS listening and end the current assistant process when the user explicitly asks JARVIS to stop or turn itself off.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"pc_status","description":"Read basic non-sensitive computer status.","parameters":{"type":"object","properties":{}}}}
]

TOOLS = TOOL_DECLARATIONS

RISKY_UI_WORDS = {
    "delete", "remove", "reset", "format", "shutdown", "restart", "reboot",
    "send", "publish", "post", "buy", "purchase", "pay", "install",
    "uninstall", "factory reset", "sign out", "log out", "wipe", "erase",
}
TERMINAL_PROCESSES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "windowsterminal.exe",
    "bash.exe", "wsl.exe", "ubuntu.exe", "debian.exe", "mintty.exe",
}


def open_app(name: str) -> str:
    global LAST_OPENED_APP
    if name not in APPS:
        return f"The application {name} is not available."
    try:
        subprocess.Popen(APPS[name], shell=False)
        LAST_OPENED_APP = name
        return f"Successfully opened {name}."
    except OSError as exc:
        return f"Failed to open {name}: {exc}"


def browser_search(client: Any, query: str) -> str:
    query = str(query).strip()
    if not query:
        return "The browser search query is empty."
    try:
        shot = pyautogui.screenshot()
        width, height = shot.size
        from io import BytesIO
        buf = BytesIO()
        shot.convert("RGB").save(buf, format="JPEG", quality=82)
        encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
        locator_prompt = (
            "Locate the primary search field inside the currently visible webpage. "
            "Do not select the browser address bar, URL bar, navigation, or unrelated form fields. "
            "Return ONLY JSON: {\"x\": 123, \"y\": 456, \"confidence\": 0.0, \"reason\": \"brief description\"} "
            f"using original screenshot pixels x=0..{width-1}, y=0..{height-1}. "
            "If no clear webpage search field is visible, return x=-1, y=-1, confidence=0."
        )
        response = chat_create(client, 
            model=VISION_MODEL,
            messages=[{"role":"user","content":[
                {"type":"text","text":locator_prompt},
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{encoded}"}},
            ]}],
            temperature=0,
            max_completion_tokens=180,
        )
        raw = (response.choices[0].message.content or "").strip()
        clean = raw
        if clean.startswith("```") and clean.endswith("```"):
            clean = clean.strip("`").strip()
            if clean.lower().startswith("json"):
                clean = clean[4:].strip()
        data = json.loads(clean)
        x = int(data.get("x", -1))
        y = int(data.get("y", -1))
        confidence = float(data.get("confidence", 0))
        if x < 0 or y < 0 or x >= width or y >= height or confidence < 0.45:
            return "I could not confidently locate the webpage search field."
        pyautogui.click(x, y)
        time.sleep(0.15)
        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        return f"Searched the current webpage for '{query}'."
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return f"The visual search locator returned invalid data: {exc}"
    except Exception as exc:
        return f"Failed to search the current webpage: {exc}"


def search_web(query: str) -> str:
    query = str(query).strip()
    if not query:
        return "The search query is empty."
    try:
        url = "https://www.google.com/search?q=" + quote_plus(query)
        opened = webbrowser.open(url, new=2)
        return f"Successfully searched Google for {query}." if opened else "The browser refused to open the search."
    except OSError as exc:
        return f"Gagal melakukan pencarian: {exc}"


def open_site(name: str) -> str:
    if name not in SITES:
        return f"Website {name} belum tersedia."
    try:
        opened = webbrowser.open(SITES[name], new=2)
        return f"Successfully opened {name}." if opened else f"Browser menolak membuka {name}."
    except OSError as exc:
        return f"Failed to open {name}: {exc}"


def open_folder(name: str) -> str:
    folder = FOLDERS.get(name)
    if folder is None:
        return f"The folder {name} is not available."
    if not folder.exists():
        return f"The folder {name} was not found."
    try:
        os.startfile(str(folder))
        return f"Successfully opened the {name} folder."
    except OSError as exc:
        return f"Failed to open the {name} folder: {exc}"


def pc_status() -> str:
    free_gb = shutil.disk_usage(Path.home()).free / (1024 ** 3)
    return (
        f"OS: {platform.system()} {platform.release()}; "
        f"komputer: {platform.node()}; "
        f"Python: {platform.python_version()}; "
        f"ruang kosong: {free_gb:.1f} GB."
    )



def launch_application(name: str) -> str:
    name = str(name).strip()
    if not name:
        return "The application name is empty."
    risky_names = {"powershell", "windows terminal", "command prompt", "cmd", "terminal", "wsl"}
    if name.lower() in risky_names:
        return "__RISKY_ACTION__:Open the terminal application '{}'. This can execute system commands.".format(name)
    try:
        pyautogui.hotkey("win", "s")
        time.sleep(0.4)
        pyperclip.copy(name)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        pyautogui.press("enter")
        return f"Requested Windows Search to open {name}."
    except Exception as exc:
        return f"Failed to launch {name}: {exc}"


def open_url(url: str) -> str:
    url = str(url).strip()
    if not url:
        return "The URL is empty."
    try:
        opened = webbrowser.open(url, new=2)
        return f"Opened {url}." if opened else "The browser refused to open the URL."
    except Exception as exc:
        return f"Failed to open URL: {exc}"


def open_path(path: str) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    if not target.exists():
        return f"The path was not found: {target}"
    if target.suffix.lower() in {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".js"}:
        return f"Executable or script paths are not opened by open_path: {target}"
    try:
        os.startfile(str(target))
        return f"Opened {target}."
    except Exception as exc:
        return f"Failed to open {target}: {exc}"


def list_directory(path: str) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    if not target.exists() or not target.is_dir():
        return f"Directory not found: {target}"
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for item in entries[:200]:
            kind = "DIR " if item.is_dir() else "FILE"
            lines.append(f"{kind} {item.name}")
        return "\n".join(lines) if lines else "The directory is empty."
    except Exception as exc:
        return f"Failed to list {target}: {exc}"


def read_file(path: str) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    lower = str(target).lower()
    blocked_tokens = ("\\login data", "\\cookies", "\\web data", "\\local state", ".pem", ".key", ".p12", ".pfx")
    if any(token in lower for token in blocked_tokens):
        return "For safety, JARVIS will not read credential stores, browser secret databases, or private-key files."
    if not target.exists() or not target.is_file():
        return f"File not found: {target}"
    try:
        size = target.stat().st_size
        if size > 2_000_000:
            return "The file is too large to read through this tool."
        content = target.read_text(encoding="utf-8", errors="replace")
        return content[:30000] if content else "The file is empty."
    except Exception as exc:
        return f"Failed to read {target}: {exc}"


def write_file(path: str, content: str) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    if target.exists():
        return f"__RISKY_ACTION__:Overwrite the existing file {target}."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return f"Created {target} successfully."
    except Exception as exc:
        return f"Failed to write {target}: {exc}"


def create_folder(path: str) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    try:
        target.mkdir(parents=True, exist_ok=True)
        return f"Created folder {target} successfully."
    except Exception as exc:
        return f"Failed to create folder {target}: {exc}"


def rename_path(path: str, new_name: str) -> str:
    source = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    if not source.exists():
        return f"Path not found: {source}"
    target = source.parent / str(new_name).strip()
    return f"__RISKY_ACTION__:Rename {source} to {target}."


def move_path(source: str, destination: str) -> str:
    src = Path(os.path.expandvars(os.path.expanduser(str(source).strip()))).resolve()
    dst = Path(os.path.expandvars(os.path.expanduser(str(destination).strip()))).resolve()
    if not src.exists():
        return f"Source path not found: {src}"
    return f"__RISKY_ACTION__:Move {src} to {dst}."


def _perform_confirmed_rename(source: str, new_name: str) -> str:
    src = Path(os.path.expandvars(os.path.expanduser(str(source).strip()))).resolve()
    dst = src.parent / str(new_name).strip()
    if dst.exists():
        return f"Destination already exists: {dst}"
    try:
        src.rename(dst)
        return f"Renamed {src} to {dst}."
    except Exception as exc:
        return f"Failed to rename {src}: {exc}"


def _perform_confirmed_move(source: str, destination: str) -> str:
    import shutil as _shutil
    src = Path(os.path.expandvars(os.path.expanduser(str(source).strip()))).resolve()
    dst = Path(os.path.expandvars(os.path.expanduser(str(destination).strip()))).resolve()
    try:
        final = dst / src.name if dst.exists() and dst.is_dir() else dst
        _shutil.move(str(src), str(final))
        return f"Moved {src} to {final}."
    except Exception as exc:
        return f"Failed to move {src}: {exc}"


def delete_path(path: str, recursive: bool = False) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    if not target.exists():
        return f"Path not found: {target}"
    return f"__RISKY_ACTION__:Delete {target}{' recursively' if recursive else ''}."


def _perform_confirmed_delete(path: str, recursive: bool = False) -> str:
    import shutil as _shutil
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    if not target.exists():
        return f"Path not found: {target}"
    try:
        if target.is_dir():
            if not recursive and any(target.iterdir()):
                return f"Directory is not empty: {target}. Set recursive=true only when the user explicitly requested recursive deletion."
            _shutil.rmtree(target) if recursive else target.rmdir()
        else:
            target.unlink()
        return f"Deleted {target}."
    except Exception as exc:
        return f"Failed to delete {target}: {exc}"


def click_at(x: int, y: int, button: str = "left") -> str:
    pyautogui.click(int(x), int(y), button=str(button))
    return f"Clicked {button} at ({int(x)}, {int(y)})."


def double_click_at(x: int, y: int, button: str = "left") -> str:
    pyautogui.doubleClick(int(x), int(y), button=str(button), interval=0.1)
    return f"Double-clicked {button} at ({int(x)}, {int(y)})."


def move_mouse(x: int, y: int) -> str:
    pyautogui.moveTo(int(x), int(y), duration=0.15)
    return f"Moved mouse to ({int(x)}, {int(y)})."


def drag_mouse(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> str:
    pyautogui.moveTo(int(start_x), int(start_y), duration=0.1)
    pyautogui.dragTo(int(end_x), int(end_y), duration=max(0.1, min(5.0, float(duration))), button="left")
    return f"Dragged from ({int(start_x)}, {int(start_y)}) to ({int(end_x)}, {int(end_y)})."


def hotkey(keys: str) -> str:
    value = str(keys).lower().replace(" ", "")
    risky = {"ctrl+alt+delete", "win+l", "alt+f4"}
    if value in risky:
        return f"__RISKY_ACTION__:Press the system shortcut {keys}."
    parts = [p for p in value.split("+") if p]
    if not parts:
        return "The shortcut is empty."
    try:
        pyautogui.hotkey(*parts)
        return f"Pressed {keys}."
    except Exception as exc:
        return f"Failed to press {keys}: {exc}"


def _foreground_process_name() -> str:
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    return _windows_process_name_from_hwnd(int(hwnd)) if hwnd else ""


def _terminal_is_foreground() -> bool:
    return _foreground_process_name() in TERMINAL_PROCESSES


def _get_word_app():
    try:
        app = win32com.client.GetActiveObject("Word.Application")
    except Exception:
        app = win32com.client.Dispatch("Word.Application")
    app.Visible = True
    try:
        app.Activate()
    except Exception:
        pass
    return app


def word_control(
    action: str,
    text: str = "",
    replacement: str = "",
    font_size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    alignment: str = "",
    style: str = "",
    rows: int = 0,
    columns: int = 0,
    path: str = "",
) -> str:
    """Perform structured, non-arbitrary automation against Microsoft Word."""
    try:
        app = _get_word_app()
        doc = app.ActiveDocument if app.Documents.Count else app.Documents.Add()
        sel = app.Selection

        if action == "new_document":
            app.Documents.Add()
            return "A new Word document was created successfully."

        if action == "write":
            if not text:
                return "The text is empty."
            start = sel.Range.Start
            sel.TypeText(str(text))
            end = sel.Range.End
            inserted = doc.Range(start, end)
            if bold is not None:
                inserted.Font.Bold = -1 if bold else 0
            if italic is not None:
                inserted.Font.Italic = -1 if italic else 0
            if underline is not None:
                inserted.Font.Underline = 1 if underline else 0
            if font_size is not None:
                inserted.Font.Size = float(font_size)
            if style:
                try:
                    inserted.Style = style
                except Exception:
                    pass
            alignments = {"left": 0, "center": 1, "right": 2, "justify": 3}
            if alignment in alignments:
                inserted.ParagraphFormat.Alignment = alignments[alignment]
            return "The text was written and formatted in Word successfully."

        if action == "format_selection":
            fmt = sel.Font
            if bold is not None:
                fmt.Bold = -1 if bold else 0
            if italic is not None:
                fmt.Italic = -1 if italic else 0
            if underline is not None:
                fmt.Underline = 1 if underline else 0
            if font_size is not None:
                fmt.Size = float(font_size)
            if style:
                try:
                    sel.Style = style
                except Exception:
                    pass
            alignments = {"left": 0, "center": 1, "right": 2, "justify": 3}
            if alignment in alignments:
                sel.ParagraphFormat.Alignment = alignments[alignment]
            return "Word text formatting was applied successfully."

        if action == "select_all":
            doc.Content.Select()
            return "The entire Word document was selected."

        if action == "insert_table":
            r = max(1, min(50, int(rows or 1)))
            c = max(1, min(20, int(columns or 1)))
            table = doc.Tables.Add(sel.Range, r, c)
            table.Borders.Enable = True
            return f"Tabel {r} x {c} berhasil dibuat di Word."

        if action == "replace_text":
            find = str(text)
            if not find:
                return "The search text is empty."
            rng = doc.Content
            finder = rng.Find
            finder.ClearFormatting()
            finder.Replacement.ClearFormatting()
            finder.Text = find
            finder.Replacement.Text = str(replacement)
            finder.Wrap = 1
            finder.Execute(Replace=2)
            return f"Teks '{find}' berhasil diganti."

        if action == "read_document":
            content = doc.Content.Text
            content = content.replace("\r", "\n").strip()
            return content[:12000] if content else "Dokumen Word masih kosong."

        if action == "save":
            doc.Save()
            return "The Word document was saved successfully."

        if action == "save_as":
            target = str(path).strip()
            if not target:
                return "The save path is empty."
            doc.SaveAs2(target)
            return f"Dokumen Word berhasil disimpan sebagai {target}."

        return f"Aksi Word '{action}' belum tersedia."
    except Exception as exc:
        return f"Gagal menjalankan Word control: {exc}"


def type_text(text: str) -> str:
    text = str(text)
    if not text:
        return "The text is empty."
    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return "The text was typed into the active window successfully."
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
        return "The text is empty."
    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return "The text was typed into the active window successfully."
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
        if not windows: return "No matching UI window was found."
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
        return "\n".join(lines)[:12000] if lines else "No readable UI controls were found."
    except Exception as exc: return f"Gagal membaca UI Windows: {exc}"

def ui_click(text: str, window_title: str = "") -> str:
    target=str(text).strip()
    if not target: return "The UI target text is empty."
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


def _locate_visual_target(client: Any, image_bytes: bytes, target: str, width: int, height: int) -> tuple[int, int, str]:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You are locating one visible desktop UI target on a screenshot.\n"
        f"Target: {target}\n\n"
        "Return ONLY one JSON object with this exact schema:\n"
        '{"x": 123, "y": 456, "confidence": 0.0, "reason": "brief description"}\n\n'
        f"Coordinates must use the original screenshot pixels, x 0-{width - 1}, y 0-{height - 1}.\n"
        "Count repeated items in normal reading order: top-to-bottom, then left-to-right.\n"
        "For requests such as \"second video\", select the second visible video result/card, not an ad, navigation item, or unrelated sidebar item.\n"
        "For requests such as \"third button\" or \"second card\", count only visually equivalent visible targets.\n"
        "If the target is not clearly visible, return x=-1, y=-1, confidence=0.\n"
        "Return no markdown and no extra text."
    )
    response = chat_create(client, 
        model=VISION_MODEL,
        messages=[{"role":"user","content":[
            {"type":"text","text":prompt},
            {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{encoded}"}},
        ]}],
        temperature=0,
        max_completion_tokens=220,
    )
    raw = (response.choices[0].message.content or "").strip()
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(clean)
        x = int(data.get("x", -1))
        y = int(data.get("y", -1))
        confidence = float(data.get("confidence", 0))
        reason = str(data.get("reason", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Vision returned invalid locator JSON: {raw[:500]}") from exc
    if x < 0 or y < 0 or x >= width or y >= height or confidence < 0.45:
        raise RuntimeError(f"Could not confidently locate '{target}' on the current screen.")
    return x, y, reason


def visual_click(client: Any, target: str, button: str = "left") -> str:
    target = str(target).strip()
    if not target:
        return "The visual target is empty."
    try:
        shot = pyautogui.screenshot()
        width, height = shot.size
        from io import BytesIO
        buf = BytesIO()
        shot.convert("RGB").save(buf, format="JPEG", quality=82)
        x, y, reason = _locate_visual_target(client, buf.getvalue(), target, width, height)
        pyautogui.click(x, y, button=str(button))
        return f"Clicked the visual target '{target}' at ({x}, {y}). {reason}"
    except Exception as exc:
        return f"Failed to visually locate/click '{target}': {exc}"


def _vision_answer(client: Any, image_bytes: bytes, question: str, source: str) -> str:
    encoded=base64.b64encode(image_bytes).decode("utf-8")
    response=chat_create(client, 
        model=VISION_MODEL,
        messages=[{
            "role":"user",
            "content":[
                {"type":"text","text":f"JARVIS is viewing a {source}. Answer in concise Indonesian. Describe only what is visibly supported by the image. User asks: {question}"},
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{encoded}"}}
            ]
        }],
        temperature=0.2,
        max_completion_tokens=700,
    )
    return (response.choices[0].message.content or "").strip()


def see_screen(client: Any, question: str) -> str:
    try:
        shot=pyautogui.screenshot()
        from io import BytesIO
        buf=BytesIO()
        shot.convert("RGB").save(buf, format="JPEG", quality=80)
        return _vision_answer(client, buf.getvalue(), question, "screen Windows")
    except Exception as exc:
        return f"Gagal melihat layar: {exc}"


def see_webcam(client: Any, question: str) -> str:
    cap=None
    try:
        cap=cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return "The webcam could not be opened."
        ok, frame=cap.read()
        if not ok:
            return "The webcam opened, but no frame could be captured."
        ok, encoded=cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY),80])
        if not ok:
            return "The webcam frame could not be encoded."
        return _vision_answer(client, encoded.tobytes(), question, "webcam")
    except Exception as exc:
        return f"Gagal melihat webcam: {exc}"
    finally:
        if cap is not None:
            cap.release()


def run_tool(name: str, arguments: dict[str, Any], client: Any | None = None) -> str:
    # Some model/tool adapters can occasionally append an internal channel marker.
    if isinstance(name, str) and "<|channel|>" in name:
        name = name.split("<|channel|>", 1)[0]

    if name == "open_app":
        return open_app(str(arguments["name"]))
    if name == "browser_search":
        if client is None:
            return "Browser search is unavailable because no active vision client is available."
        return browser_search(client, str(arguments["query"]))
    if name == "launch_application":
        return launch_application(str(arguments["name"]))
    if name == "open_url":
        return open_url(str(arguments["url"]))
    if name == "open_site":
        return open_site(str(arguments["name"]))
    if name == "open_folder":
        return open_folder(str(arguments["name"]))
    if name == "open_path":
        return open_path(str(arguments["path"]))
    if name == "list_directory":
        return list_directory(str(arguments["path"]))
    if name == "read_file":
        return read_file(str(arguments["path"]))
    if name == "write_file":
        return write_file(str(arguments["path"]), str(arguments["content"]))
    if name == "create_folder":
        return create_folder(str(arguments["path"]))
    if name == "rename_path":
        return rename_path(str(arguments["path"]), str(arguments["new_name"]))
    if name == "move_path":
        return move_path(str(arguments["source"]), str(arguments["destination"]))
    if name == "delete_path":
        return delete_path(str(arguments["path"]), bool(arguments.get("recursive", False)))
    if name == "search_web":
        return search_web(str(arguments["query"]))
    if name == "type_text":
        return type_text(str(arguments["text"]))
    if name == "word_control":
        return word_control(
            str(arguments.get("action", "")),
            str(arguments.get("text", "")),
            str(arguments.get("replacement", "")),
            arguments.get("font_size"),
            arguments.get("bold"),
            arguments.get("italic"),
            arguments.get("underline"),
            str(arguments.get("alignment", "")),
            str(arguments.get("style", "")),
            int(arguments.get("rows", 0) or 0),
            int(arguments.get("columns", 0) or 0),
            str(arguments.get("path", "")),
        )
    if name == "ui_click":
        return ui_click(str(arguments["text"]), str(arguments.get("window_title","")))
    if name == "ui_inspect":
        return ui_inspect(str(arguments.get("window_title","")))
    if name == "visual_click":
        if client is None:
            return "Vision is unavailable because no active vision client is available."
        return visual_click(client, str(arguments["target"]), str(arguments.get("button", "left")))
    if name == "see_screen":
        if client is None:
            return "Vision is unavailable without an active Groq client."
        return see_screen(client, str(arguments["question"]))
    if name == "see_webcam":
        if client is None:
            return "Vision is unavailable without an active Groq client."
        return see_webcam(client, str(arguments["question"]))
    if name == "scroll_mouse":
        return scroll_mouse(int(arguments["clicks"]))
    if name == "wait_seconds":
        return wait_seconds(float(arguments["seconds"]))
    if name == "press_key":
        return press_key(str(arguments["key"]))
    if name == "hotkey":
        return hotkey(str(arguments["keys"]))
    if name == "click_at":
        return click_at(int(arguments["x"]), int(arguments["y"]), str(arguments.get("button", "left")))
    if name == "double_click_at":
        return double_click_at(int(arguments["x"]), int(arguments["y"]), str(arguments.get("button", "left")))
    if name == "move_mouse":
        return move_mouse(int(arguments["x"]), int(arguments["y"]))
    if name == "drag_mouse":
        return drag_mouse(
            int(arguments["start_x"]),
            int(arguments["start_y"]),
            int(arguments["end_x"]),
            int(arguments["end_y"]),
            float(arguments.get("duration", 0.5)),
        )
    if name == "pc_status":
        return pc_status()
    if name == "close_active_window":
        return close_active_window()
    if name == "close_app":
        return close_app(str(arguments["name"]))
    if name == "stop_jarvis":
        return stop_jarvis()
    return f"Tool {name} is not recognized."
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
        return "Closing a specific application is only supported on Windows."

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
        return "I found Calculator, but Windows did not close its window."

    if not matches and name != "calculator":
        return f"{name} tidak sedang terbuka."

    return f"Berhasil menutup {name}."




def close_active_window() -> str:
    try:
        pyautogui.hotkey("alt", "f4")
        return "The active window was closed with Alt+F4."
    except Exception as exc:
        return f"Gagal menutup jendela aktif: {exc}"


def stop_jarvis() -> str:
    return "__JARVIS_STOP__"




def _perform_confirmed_write(path: str, content: str) -> str:
    target = Path(os.path.expandvars(os.path.expanduser(str(path).strip()))).resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return f"Updated {target} successfully."
    except Exception as exc:
        return f"Failed to overwrite {target}: {exc}"


def _perform_confirmed_launch_application(name: str) -> str:
    try:
        pyautogui.hotkey("win", "s")
        time.sleep(0.4)
        pyperclip.copy(str(name))
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        pyautogui.press("enter")
        return f"Requested Windows Search to open {name}."
    except Exception as exc:
        return f"Failed to launch {name}: {exc}"


def _perform_confirmed_hotkey(keys: str) -> str:
    value = str(keys).lower().replace(" ", "")
    parts = [p for p in value.split("+") if p]
    if not parts:
        return "The shortcut is empty."
    try:
        pyautogui.hotkey(*parts)
        return f"Pressed {keys}."
    except Exception as exc:
        return f"Failed to press {keys}: {exc}"


def request_confirmation(description: str) -> bool:
    callback = CONFIRMATION_CALLBACK
    if callback is not None:
        try:
            return bool(callback(description))
        except Exception as exc:
            print(f"[CONFIRM] callback failed: {exc}")
            return False

    try:
        answer = input(f"[CONFIRM] {description} (yes/no): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes", "approve", "approved", "iya", "ya", "lanjut", "boleh"}



def ask_agent(client: Any, user_text: str) -> str:
    # Do not guess from keywords. The agent decides whether it needs to inspect the
    # desktop and can call ui_inspect/see_screen/visual_click as part of Computer Use.
    # This keeps natural-language commands language-independent.
    user_content: Any = user_text

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    for _ in range(6):
        response = chat_create(client, 
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.1,
            max_completion_tokens=256,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return (message.content or "").strip()

        messages.append(message)

        for tool_call in message.tool_calls:
            try:
                tool_name = tool_call.function.name
                raw_arguments = tool_call.function.arguments or "{}"
                arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)

                if tool_name == "type_text" and _terminal_is_foreground():
                    approval = request_confirmation(
                        f"Type the requested text into the active terminal window. This may execute commands or alter system state."
                    )
                    if not approval:
                        result = "The user denied typing into the terminal."
                    else:
                        result = run_tool(tool_name, arguments, client)
                elif tool_name == "ui_click" and any(word in str(arguments.get("text", "")).lower() for word in RISKY_UI_WORDS):
                    approval = request_confirmation(
                        f"Click the potentially consequential control '{arguments.get('text')}'."
                    )
                    if not approval:
                        result = "The user denied this consequential UI action."
                    else:
                        result = run_tool(tool_name, arguments, client)
                else:
                    result = run_tool(tool_name, arguments, client)

                if isinstance(result, str) and result.startswith("__RISKY_ACTION__:"):
                    description = result.split(":", 1)[1].strip()
                    approval = request_confirmation(description)
                    if not approval:
                        result = "The user denied the high-impact action."
                    elif tool_name == "delete_path":
                        result = _perform_confirmed_delete(
                            str(arguments["path"]),
                            bool(arguments.get("recursive", False)),
                        )
                    elif tool_name == "write_file":
                        result = _perform_confirmed_write(
                            str(arguments["path"]),
                            str(arguments["content"]),
                        )
                    elif tool_name == "launch_application":
                        result = _perform_confirmed_launch_application(str(arguments["name"]))
                    elif tool_name == "hotkey":
                        result = _perform_confirmed_hotkey(str(arguments["keys"]))
                    elif tool_name == "move_path":
                        result = _perform_confirmed_move(
                            str(arguments["source"]),
                            str(arguments["destination"]),
                        )
                    elif tool_name == "rename_path":
                        result = _perform_confirmed_rename(
                            str(arguments["path"]),
                            str(arguments["new_name"]),
                        )
                    else:
                        result = f"Approved high-impact action, but no executor is registered for {tool_name}."
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

    return "I stopped after several tool steps to avoid an infinite loop."


def record_audio(path: Path) -> None:
    """Low-latency utterance capture. Stop after a short silence tail."""
    print("[MIC] Mendengarkan...")
    block_size = 1600
    max_blocks = int(RECORD_SECONDS * SAMPLE_RATE / block_size)
    min_blocks = max(1, int(MIN_RECORD_SECONDS * SAMPLE_RATE / block_size))
    silence_blocks = max(1, int(SILENCE_SECONDS * SAMPLE_RATE / block_size))
    chunks = []
    started = False
    quiet_count = 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=block_size) as stream:
        while not started:
            data, _ = stream.read(block_size)
            chunk = data.copy()
            if float(abs(chunk).mean()) >= ENERGY_THRESHOLD:
                started = True
                chunks.append(chunk)
                print("[MIC] Suara terdeteksi.")

        for _ in range(max(0, max_blocks - 1)):
            data, _ = stream.read(block_size)
            chunk = data.copy()
            chunks.append(chunk)
            if float(abs(chunk).mean()) < ENERGY_THRESHOLD:
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



def try_direct_command(text: str, client: Any | None = None) -> str | None:
    """Keep command interpretation inside the agent instead of phrase-specific rules.

    JARVIS should understand natural language semantically and choose the appropriate
    Computer Use tool. This function only preserves the explicit stop command because
    stopping the assistant is control-flow, not a desktop action.
    """
    normalized = " ".join(text.lower().strip().split()).strip(".,!?;:")

    stop_words = {"stop", "berhenti"}
    stop_targets = {"jarvis", "jervis", "yervis", "surface"}

    if normalized in {"stop jarvis", "stop jervis", "stop yervis",
                      "matikan jarvis", "matikan jervis", "matikan yervis",
                      "matikan diri", "matikan diri sendiri",
                      "shutdown jarvis", "berhenti mendengarkan"}:
        print("[DIRECT] stop_jarvis -> JARVIS dihentikan.")
        return "__JARVIS_STOP__"

    words = set(normalized.split())
    if words & stop_words and words & stop_targets:
        print("[DIRECT] stop_jarvis -> JARVIS dihentikan.")
        return "__JARVIS_STOP__"

    # Everything else goes through the semantic agent. No phrase dictionary,
    # no language-specific shortcut list, and no exact wording requirement.
    return None

def transcribe(client: Any, path: Path) -> str:
    """Fast STT: one turbo request with plain-text output."""
    try:
        kwargs = {
            "file": (path.name, path.read_bytes()),
            "model": STT_MODEL,
            "prompt": STT_PROMPT,
            "response_format": "text",
            "temperature": 0,
        }
        if STT_LANGUAGE:
            kwargs["language"] = STT_LANGUAGE
        text = str(client.audio.transcriptions.create(**kwargs)).strip()
        if text:
            print(f"[STT] Groq: {text}")
        return text
    except Exception as exc:
        print(f"[STT] Groq gagal: {exc}")
        return ""




def confirm_action_via_text(description: str) -> bool:
    print(f"[CONFIRM] High-impact action: {description}")
    try:
        answer = input("[CONFIRM] Type yes to proceed or no to cancel: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    normalized = " ".join(answer.split())
    yes = {"yes", "y", "approve", "approved", "do it", "go ahead", "iya", "ya", "boleh", "lanjut", "jalankan"}
    return normalized in yes or normalized.startswith(tuple(f"{item} " for item in yes))


def confirm_action_via_voice(client: Any, description: str) -> bool:
    prompt_path = Path(__file__).resolve().with_name(".jarvis_confirm.wav")
    speak(
        f"This is a high-impact action: {description}. "
        "Please say yes to proceed or no to cancel."
    )
    try:
        record_audio(prompt_path)
        answer = transcribe(client, prompt_path).lower().strip()
    except Exception as exc:
        print(f"[CONFIRM] Voice confirmation failed: {exc}")
        return False
    finally:
        try:
            prompt_path.unlink(missing_ok=True)
        except OSError:
            pass

    normalized = " ".join(answer.split())
    yes = {"yes", "yeah", "yep", "approve", "approved", "do it", "go ahead", "iya", "ya", "boleh", "lanjut", "jalankan"}
    no = {"no", "nope", "cancel", "deny", "denied", "jangan", "tidak", "nggak", "enggak", "batal", "stop"}
    if normalized in yes or normalized.startswith(tuple(f"{item} " for item in yes)):
        print("[CONFIRM] User approved.")
        return True
    if normalized in no or normalized.startswith(tuple(f"{item} " for item in no)):
        print("[CONFIRM] User denied.")
        return False
    speak("I did not get a clear yes or no, Sir. I cancelled the action.")
    return False


def _agent_worker(user_text: str, text_mode: bool, result_conn) -> None:
    """Run one agent request in an isolated process so the parent can kill it instantly."""
    try:
        client = build_llm_client()

        global CONFIRMATION_CALLBACK
        if text_mode:
            CONFIRMATION_CALLBACK = lambda description: confirm_action_via_text(description)
        else:
            if not GROQ_API_KEY:
                raise RuntimeError("GROQ_API_KEY belum diatur.")
            worker_stt_client = Groq(api_key=GROQ_API_KEY)
            CONFIRMATION_CALLBACK = lambda description: confirm_action_via_voice(
                worker_stt_client, description
            )

        reply = ask_agent(client, user_text)
        result_conn.send(("ok", reply))
    except BaseException as exc:
        try:
            result_conn.send(("error", f"{type(exc).__name__}: {exc}"))
        except Exception:
            pass
    finally:
        try:
            result_conn.close()
        except Exception:
            pass


def run_agent_interruptible(user_text: str, text_mode: bool) -> str:
    """Run the agent in a killable worker process.

    Press Esc while JARVIS is processing to terminate the current request immediately.
    This is intentionally a process boundary because a blocked HTTP/Ollama call cannot
    be reliably cancelled from the same Python thread.
    """
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    worker = ctx.Process(
        target=_agent_worker,
        args=(user_text, text_mode, child_conn),
        daemon=True,
    )
    worker.start()
    child_conn.close()

    try:
        while worker.is_alive():
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key == "\x1b":
                    print("\n[AGENT] ESC detected. Force-stopping the current request...")
                    worker.terminate()
                    worker.join(timeout=1.5)
                    if worker.is_alive():
                        worker.kill()
                        worker.join(timeout=1)
                    return "__JARVIS_CANCELLED__"
            time.sleep(0.05)

        if parent_conn.poll(0.2):
            status, payload = parent_conn.recv()
            if status == "ok":
                return payload
            return f"__JARVIS_ERROR__:{payload}"

        if worker.exitcode not in (0, None):
            return f"__JARVIS_ERROR__:agent worker exited with code {worker.exitcode}"

        return ""
    except KeyboardInterrupt:
        print("\n[AGENT] Keyboard interrupt. Force-stopping the current request...")
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=1.5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
        return "__JARVIS_CANCELLED__"
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=1)



def main() -> None:
    try:
        llm_client = build_llm_client()
    except RuntimeError as exc:
        print(str(exc))
        return

    stt_client = None
    if not TEXT_MODE:
        if not GROQ_API_KEY:
            print("GROQ_API_KEY belum diatur. Voice mode saat ini memakai Groq hanya untuk STT.")
            return
        stt_client = Groq(api_key=GROQ_API_KEY)

    global CONFIRMATION_CALLBACK
    CONFIRMATION_CALLBACK = (
        (lambda description: confirm_action_via_text(description))
        if TEXT_MODE
        else (lambda description: confirm_action_via_voice(stt_client, description))
    )
    if not TEXT_MODE:
        speak("System online, Sir. I am ready to listen.")
    audio_path = Path(__file__).resolve().with_name(".jarvis_input.wav")

    if TEXT_MODE:
        print(f"JARVIS text mode online, Sir. LLM: {LLM_PROVIDER}/{MODEL}")
        print("JARVIS text mode online, Sir. Type commands; use /exit to quit.")
        while True:
            try:
                heard = input("Sir> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[JARVIS] Text mode stopped.")
                break

            if not heard:
                continue
            if heard.lower().strip() in {"/exit", "/quit", "/stop", "exit", "quit"}:
                print("[JARVIS] Text mode stopped.")
                break

            normalized = " ".join(heard.lower().strip().split())
            normalized = normalized.strip(".,!?;:")

            shutdown_phrases = {
                "shutdown jarvis", "matikan jarvis", "matikan diri",
                "matikan diri sendiri", "matikan dirimu", "stop jarvis",
            }
            shutdown_intent = normalized in shutdown_phrases
            if shutdown_intent:
                print("[JARVIS] Shutdown command received.")
                print("JARVIS: Understood, Sir. Shutting down the JARVIS system.")
                break

            direct_reply = try_direct_command(heard, llm_client)
            if direct_reply == "__JARVIS_STOP__":
                print("[JARVIS] Text mode stopped.")
                break

            try:
                reply = run_agent_interruptible(heard, TEXT_MODE)
                if reply == "__JARVIS_STOP__":
                    print("[JARVIS] Text mode stopped.")
                    break
                if reply == "__JARVIS_CANCELLED__":
                    print("JARVIS: Request cancelled, Sir.")
                    continue
                if reply.startswith("__JARVIS_ERROR__:"):
                    print(f"[AGENT] {reply}")
                    print("JARVIS: I could not process that request, Sir. Please check the terminal log.")
                    continue
                if reply:
                    print(f"JARVIS: {reply}")
            except KeyboardInterrupt:
                print("\n[JARVIS] Text mode stopped.")
                break
            except Exception as exc:
                print(f"[AGENT] {exc}")
                print("JARVIS: I could not process that request, Sir. Please check the terminal log.")
        return

    try:
        while True:
            try:
                record_audio(audio_path)
                heard = transcribe(stt_client, audio_path)
            except (OSError, sd.PortAudioError) as exc:
                print(f"[MIC] {exc}")
                speak("I cannot access the microphone. Please check your Windows audio device.")
                time.sleep(2)
                continue
            except Exception as exc:
                print(f"[STT] {exc}")
                speak("Speech recognition failed. Please check the connection and GROQ API key.")
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
                speak("Understood, Sir. Shutting down the JARVIS system.")
                return

            direct_reply = try_direct_command(heard, llm_client)
            if direct_reply == "__JARVIS_STOP__":
                print("[JARVIS] Perintah shutdown diterima. Menghentikan proses agent...")
                speak("Understood, Sir. Shutting down the JARVIS system.")
                return

            try:
                reply = run_agent_interruptible(heard, TEXT_MODE)
                if reply == "__JARVIS_STOP__":
                    speak("Understood, Sir. Shutting down the JARVIS system.")
                    break
                if reply == "__JARVIS_CANCELLED__":
                    speak("Cancelled, Sir.")
                    continue
                if reply.startswith("__JARVIS_ERROR__:"):
                    print(f"[AGENT] {reply}")
                    speak("I could not process that request, Sir.")
                    continue
                if reply:
                    speak(reply)
            except KeyboardInterrupt:
                print("\n[JARVIS] Dihentikan dari keyboard.")
                break
            except Exception as exc:
                print(f"[AGENT] {exc}")
                speak("I could not process that request, Sir. Please check the terminal log.")

    except KeyboardInterrupt:
        print("\n[JARVIS] Dihentikan dari keyboard.")
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
