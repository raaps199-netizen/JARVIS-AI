"""WinMind extension pack: raw keyboard & mouse input injection.

The foundation of desktop automation. Where UI Automation (ui_click/ui_type_text)
needs a named control, this pack drives the OS input stream directly via
SendInput - global hotkeys (Win+R, Alt+Tab), literal Unicode typing into
whatever has focus, single named-key presses, and absolute/relative mouse
move/click/drag/scroll. Plus clipboard set + paste.

Every tool here is a user action (it changes machine state / moves the cursor).
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any, Callable


def _ok(data: Any, *, warnings: list[str] | None = None, source_api: str | None = None, risk_level: str = "user_action") -> dict[str, Any]:
    return {"ok": True, "data": data, "warnings": warnings or [], "source_api": source_api, "risk_level": risk_level}


def _fail(error: str, *, source_api: str | None = None, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": error, "source_api": source_api}
    if details is not None:
        payload["details"] = details
    return payload


# ------------------------------------------------------------------
# SendInput plumbing
# ------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

SM_CXSCREEN = 0
SM_CYSCREEN = 1

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


def _send(*inputs: INPUT) -> int:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return int(user32.SendInput(n, arr, ctypes.sizeof(INPUT)))


def _key_input(vk: int, up: bool = False) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=(KEYEVENTF_KEYUP if up else 0), time=0, dwExtraInfo=0)))


def _unicode_input(ch: str, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTunion(ki=KEYBDINPUT(wVk=0, wScan=ord(ch), dwFlags=flags, time=0, dwExtraInfo=0)))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, u=_INPUTunion(mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)))


# Virtual-key codes for named keys and modifiers.
VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "menu": 0x12,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "super": 0x5B, "meta": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "bksp": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "prtsc": 0x2C, "capslock": 0x14, "numlock": 0x90,
    "apps": 0x5D, "menukey": 0x5D, "pause": 0x13,
}
for _i in range(1, 25):  # F1..F24
    VK[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)
_PUNCT_VK = {
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
    "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
VK.update(_PUNCT_VK)

MODIFIERS = {"ctrl", "control", "shift", "alt", "menu", "win", "lwin", "rwin", "super", "meta"}


def _resolve_vk(token: str) -> int | None:
    return VK.get(token.strip().lower())


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

def send_hotkey(combo: str) -> dict[str, Any]:
    """Send a key combination like 'win+r', 'alt+f4', 'ctrl+shift+esc', 'ctrl+c'."""
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    if not parts:
        return _fail("combo is empty.", source_api="SendInput keyboard")
    vks: list[int] = []
    for part in parts:
        vk = _resolve_vk(part)
        if vk is None:
            return _fail(f"Unknown key in combo: '{part}'", source_api="SendInput keyboard")
        vks.append(vk)
    # Press all down in order, then release in reverse.
    for vk in vks:
        _send(_key_input(vk, up=False))
    time.sleep(0.02)
    for vk in reversed(vks):
        _send(_key_input(vk, up=True))
    return _ok({"combo": combo, "keys": parts}, source_api="SendInput keyboard (WM combos)")


def press_key(key: str, presses: int = 1, interval_ms: int = 30) -> dict[str, Any]:
    """Press a single named key one or more times (enter, tab, esc, up, f5, a, etc.)."""
    vk = _resolve_vk(key)
    if vk is None:
        return _fail(f"Unknown key: '{key}'", source_api="SendInput keyboard")
    n = max(1, min(int(presses), 200))
    gap = max(0, min(int(interval_ms), 2000)) / 1000.0
    for _ in range(n):
        _send(_key_input(vk, up=False))
        _send(_key_input(vk, up=True))
        if gap:
            time.sleep(gap)
    return _ok({"key": key, "presses": n}, source_api="SendInput keyboard")


def type_text(text: str, per_char_ms: int = 0) -> dict[str, Any]:
    """Type a literal Unicode string into whatever currently has keyboard focus."""
    if text is None:
        return _fail("text is required.", source_api="SendInput Unicode")
    gap = max(0, min(int(per_char_ms), 200)) / 1000.0
    count = 0
    for ch in str(text):
        if ch == "\n":
            _send(_key_input(VK["enter"], up=False))
            _send(_key_input(VK["enter"], up=True))
        elif ch == "\t":
            _send(_key_input(VK["tab"], up=False))
            _send(_key_input(VK["tab"], up=True))
        else:
            _send(_unicode_input(ch, up=False))
            _send(_unicode_input(ch, up=True))
        count += 1
        if gap:
            time.sleep(gap)
    return _ok({"typed_chars": count}, source_api="SendInput Unicode (KEYEVENTF_UNICODE)")


def _screen_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(SM_CXSCREEN)), int(user32.GetSystemMetrics(SM_CYSCREEN))


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    w, h = _screen_size()
    w = max(1, w); h = max(1, h)
    ax = int(x * 65535 / (w - 1)) if w > 1 else 0
    ay = int(y * 65535 / (h - 1)) if h > 1 else 0
    return ax, ay


def mouse_move(x: int, y: int) -> dict[str, Any]:
    """Move the mouse cursor to absolute screen coordinates (pixels)."""
    ax, ay = _to_absolute(int(x), int(y))
    _send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=ax, dy=ay))
    return _ok({"x": int(x), "y": int(y)}, source_api="SendInput mouse")


def mouse_click(button: str = "left", x: int | None = None, y: int | None = None, double: bool = False) -> dict[str, Any]:
    """Click a mouse button, optionally moving to (x, y) first. button: left|right|middle."""
    if x is not None and y is not None:
        ax, ay = _to_absolute(int(x), int(y))
        _send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=ax, dy=ay))
        time.sleep(0.01)
    down_up = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(button.lower())
    if not down_up:
        return _fail("button must be left, right, or middle.", source_api="SendInput mouse")
    clicks = 2 if double else 1
    for _ in range(clicks):
        _send(_mouse_input(down_up[0]))
        _send(_mouse_input(down_up[1]))
        if double:
            time.sleep(0.05)
    return _ok({"button": button, "double": bool(double), "x": x, "y": y}, source_api="SendInput mouse")


def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 20) -> dict[str, Any]:
    """Press at (x1,y1), drag to (x2,y2), and release. button: left|right|middle."""
    down, up = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(button.lower(), (None, None))
    if down is None:
        return _fail("button must be left, right, or middle.", source_api="SendInput mouse")
    ax, ay = _to_absolute(int(x1), int(y1))
    _send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=ax, dy=ay))
    _send(_mouse_input(down))
    n = max(2, min(int(steps), 200))
    for i in range(1, n + 1):
        ix = int(x1 + (x2 - x1) * i / n)
        iy = int(y1 + (y2 - y1) * i / n)
        bx, by = _to_absolute(ix, iy)
        _send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=bx, dy=by))
        time.sleep(0.005)
    _send(_mouse_input(up))
    return _ok({"from": [x1, y1], "to": [x2, y2], "button": button}, source_api="SendInput mouse drag")


def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """Scroll the mouse wheel. Positive = up, negative = down (in notches)."""
    if x is not None and y is not None:
        ax, ay = _to_absolute(int(x), int(y))
        _send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=ax, dy=ay))
        time.sleep(0.01)
    notches = max(-100, min(int(amount), 100))
    _send(_mouse_input(MOUSEEVENTF_WHEEL, data=notches * WHEEL_DELTA))
    return _ok({"amount": notches}, source_api="SendInput mouse wheel")


def get_cursor_pos() -> dict[str, Any]:
    """Return the current mouse cursor position (read-only)."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return {"ok": True, "data": {"x": int(pt.x), "y": int(pt.y)}, "warnings": [], "source_api": "GetCursorPos", "risk_level": "read_only"}


def clipboard_set_text(text: str) -> dict[str, Any]:
    """Set the clipboard to the given text."""
    try:
        import win32clipboard
        import win32con
    except ImportError:
        return _fail("pywin32 is required for clipboard.", source_api="win32clipboard")
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:
        return _fail(f"Could not set clipboard: {exc}", source_api="win32clipboard")
    return _ok({"length": len(str(text))}, source_api="win32clipboard.SetClipboardData")


def paste(text: str | None = None) -> dict[str, Any]:
    """Paste: optionally set clipboard text first, then send Ctrl+V."""
    if text is not None:
        res = clipboard_set_text(text)
        if not res.get("ok"):
            return res
        time.sleep(0.03)
    send_hotkey("ctrl+v")
    return _ok({"pasted": True, "set_clipboard": text is not None}, source_api="clipboard + Ctrl+V")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_USER_ACTIONS = {
    "send_hotkey", "press_key", "type_text", "mouse_move", "mouse_click",
    "mouse_drag", "mouse_scroll", "clipboard_set_text", "paste",
}


def build(tool_factory: Callable[..., dict[str, Any]], user_action_names: set[str]) -> dict[str, Any]:
    user_action_names.update(_USER_ACTIONS)
    tools = [
        tool_factory("send_hotkey", "Send a global key combination via SendInput, e.g. 'win+r', 'alt+f4', 'alt+tab', 'ctrl+shift+esc', 'ctrl+c'. Works regardless of UI Automation support.",
                     {"combo": {"type": "string", "description": "Keys joined by '+', e.g. 'ctrl+alt+del'."}}, ["combo"]),
        tool_factory("press_key", "Press a single named key one or more times (enter, tab, esc, up/down/left/right, f1-f24, a-z, 0-9).",
                     {"key": {"type": "string", "description": "Key name."}, "presses": {"type": "integer", "description": "How many times. Default 1."}, "interval_ms": {"type": "integer", "description": "Gap between presses in ms. Default 30."}}, ["key"]),
        tool_factory("type_text", "Type a literal Unicode string into whatever currently has keyboard focus (handles any character, plus newlines and tabs).",
                     {"text": {"type": "string", "description": "Text to type."}, "per_char_ms": {"type": "integer", "description": "Optional delay per character in ms."}}, ["text"]),
        tool_factory("mouse_move", "Move the mouse cursor to absolute screen pixel coordinates.",
                     {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
        tool_factory("mouse_click", "Click a mouse button (left/right/middle), optionally moving to (x,y) first; supports double-click. Use for canvas/custom controls where UI Automation cannot find an element.",
                     {"button": {"type": "string", "enum": ["left", "right", "middle"]}, "x": {"type": "integer"}, "y": {"type": "integer"}, "double": {"type": "boolean"}}),
        tool_factory("mouse_drag", "Press at (x1,y1), drag to (x2,y2), and release.",
                     {"x1": {"type": "integer"}, "y1": {"type": "integer"}, "x2": {"type": "integer"}, "y2": {"type": "integer"}, "button": {"type": "string", "enum": ["left", "right", "middle"]}, "steps": {"type": "integer"}}, ["x1", "y1", "x2", "y2"]),
        tool_factory("mouse_scroll", "Scroll the mouse wheel; positive scrolls up, negative scrolls down (in notches).",
                     {"amount": {"type": "integer", "description": "Notches; negative = down."}, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["amount"]),
        tool_factory("get_cursor_pos", "Return the current mouse cursor screen coordinates."),
        tool_factory("clipboard_set_text", "Set the system clipboard to the given text.",
                     {"text": {"type": "string"}}, ["text"]),
        tool_factory("paste", "Optionally set clipboard text, then send Ctrl+V to paste into the focused control.",
                     {"text": {"type": "string", "description": "If given, set clipboard to this first."}}),
    ]
    handlers = {
        "send_hotkey": send_hotkey, "press_key": press_key, "type_text": type_text,
        "mouse_move": mouse_move, "mouse_click": mouse_click, "mouse_drag": mouse_drag,
        "mouse_scroll": mouse_scroll, "get_cursor_pos": get_cursor_pos,
        "clipboard_set_text": clipboard_set_text, "paste": paste,
    }
    return {"tools": tools, "handlers": handlers}
