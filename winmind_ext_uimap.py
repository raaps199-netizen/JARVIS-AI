"""WinMind extension pack: UI tree - see and drive ANY Windows app through its accessibility tree.

No screen capture, no mouse, no global keystrokes. Everything goes through Microsoft UI Automation (UIA) - the
accessibility layer Windows apps publish for screen readers, which also bridges the older MSAA/IAccessible2
APIs - plus messages sent to the app's own window. That covers Win32, WinForms, WPF, UWP/WinUI (Settings,
Calculator, Store apps), Office, File Explorer, Qt (Telegram), Flutter, and every Chromium-family app:
Chrome/Edge, Electron (VS Code, Discord, Slack, Notion), CEF (Spotify, Steam) and WebView2 (Teams, WhatsApp).

What this pack does that most automation tools don't:
- The app as a TREE (ui_tree): CacheRequest + TreeScope_Subtree brings the whole parent/child hierarchy with
  every property in ONE cross-process call (Spotify: 277 on-screen nodes in ~80 ms). Landmarks ("navigation",
  "main", "search") and heading levels name the sections; empty wrapper layers are collapsed and labels that
  only repeat their parent's are folded in.
- Hosts that break a whole-subtree fetch (WinUI 3 windows embedding WebView2, e.g. WhatsApp, navigate back into
  the same web content) are split and de-duplicated by RuntimeId: 14k elements / 7 s -> ~130 / 0.25 s.
- Finds an app by its PROCESS as well as its title (Spotify's title is the song playing; a Chrome tab titled
  "Spotify" is not the Spotify app), including windows hidden in the tray - shown again without focus.
- Wakes Chromium-family apps: they build their accessibility tree only after an assistive tool asks. Every
  Chromium window of the app (top level, CEF/WebView2 child hosts, render widgets) gets both the MSAA
  (OBJID_CLIENT) and the UIA (UiaRootObjectId) request - Steam's library went from 4 elements to its full tree.
- Clicks without a mouse: accessibility actions (Invoke, Toggle, SelectionItem, ExpandCollapse, IAccessible
  default action). Web apps put click handlers on inner elements, so a press on a list item's container fires
  nowhere (Discord "Nitro" did nothing). Instead the deepest element under the item's centre gets its default
  action - Chromium's "click ancestor" then clicks whatever really handles it, as a real click would.
- Typing without a keyboard: ValuePattern for native apps. Web editors (React, Slate, ProseMirror) ignore values
  set from outside, so they get UIA focus + TextPattern select-all + WM_CHAR messages to the app's own window -
  only after focus is confirmed on the box, and the text is read back.
- Scrolling (ScrollPattern), context menus (IUIAutomationElement3.ShowContextMenu), and every action checked:
  it reports whether the screen changed and which texts appeared/disappeared.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable


def _ok(data: Any, *, warnings: list[str] | None = None, source_api: str | None = None, risk_level: str = "read_only") -> dict[str, Any]:
    return {"ok": True, "data": data, "warnings": warnings or [], "source_api": source_api, "risk_level": risk_level}


def _fail(error: str, *, source_api: str | None = None, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": error, "source_api": source_api}
    if details is not None:
        payload["details"] = details
    return payload


# ------------------------------------------------------------------
# Win32 plumbing
# ------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
user32.GetGUIThreadInfo.restype = wintypes.BOOL

WM_GETOBJECT = 0x003D
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
VK_RETURN = 0x0D
OBJID_CLIENT = -4
UIA_ROOT_OBJECT_ID = -25    # UiaRootObjectId: what a UIA client (Narrator) asks for; modern Chromium switches on for it
SMTO_ABORTIFHUNG = 0x0002
GA_ROOT = 2
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_SHOWNA = 8
SW_RESTORE = 9
ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe", "iexplore.exe"}
HELPER_CLASSES = ("IME", "MSCTFIME UI", "OleDdeWndClass", "GDI+ Hook Window Class", ".NET-BroadcastEventWindow",
                  "H.NotifyIcon", "Chrome_SystemMessageWindow", "Base_PowerMessageWindow", "DiscordDesktopOverlay")
# Chromium's own windows: top level / CEF + WebView2 child hosts, CEF's wrapper, and the render widget
CHROMIUM_CLASSES = ("Chrome_WidgetWin", "Chrome_RenderWidgetHostHWND", "CefBrowserWindow")


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def _text(hwnd: int, getter: Callable, size: int = 512) -> str:
    buf = ctypes.create_unicode_buffer(size)
    getter(hwnd, buf, size)
    return buf.value


def _title(hwnd: int) -> str:
    return _text(hwnd, user32.GetWindowTextW)


def _class(hwnd: int) -> str:
    return _text(hwnd, user32.GetClassNameW, 256)


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_name(hwnd: int) -> str:
    try:
        import psutil

        return psutil.Process(_pid(hwnd)).name()
    except Exception:
        return ""


def _is_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except Exception:
        return False
    return bool(cloaked.value)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _child_windows(hwnd: int) -> list[int]:
    found: list[int] = []

    @ENUM_PROC
    def visit(child, _lparam):
        found.append(int(child))
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    return found


def _candidate_windows() -> list[int]:
    """Top-level windows a person could mean - including app windows hidden in the tray."""
    found: list[int] = []

    @ENUM_PROC
    def visit(hwnd, _lparam):
        if not _title(hwnd).strip() or _is_cloaked(hwnd):
            return True
        if not user32.IsWindowVisible(hwnd):
            cls = _class(hwnd)
            width, height = _rect(hwnd)[2:]
            if (user32.GetWindow(hwnd, GW_OWNER) or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW
                    or width < 300 or height < 200 or cls.startswith(HELPER_CLASSES) or "TrayIcon" in cls):
                return True   # IME, DDE, tray-icon message windows... nothing a person opens
        found.append(int(hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _norm(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _match_score(hwnd: int, title_q: str, proc_q: str) -> int:
    """How well a window matches. The PROCESS beats the title: Spotify's title is the song playing, and a
    Chrome tab titled 'Spotify - Web Player' is not the Spotify app."""
    title = _title(hwnd).lower()
    exe = _process_name(hwnd).lower()
    stem = _norm(exe.removesuffix(".exe"))
    if proc_q and _norm(proc_q.removesuffix(".exe")) not in stem:
        return 0
    score = 50 if proc_q else 0
    if title_q:
        want = _norm(title_q.removesuffix(".exe"))
        by_process = bool(want) and (want == stem or (len(want) >= 3 and want in stem))
        by_title = title_q in title
        if not (by_process or by_title):
            return 0
        if by_process:
            score += 100 if want == stem else 80
        if by_title:
            score += 25 if exe in BROWSERS and not by_process else 60
    if user32.IsWindowVisible(hwnd):
        score += 10
    if not user32.IsIconic(hwnd):
        score += 5
    return score


def _target_hwnd(window_id: int | None, title_contains: str | None, process_contains: str | None) -> int:
    if window_id:
        return int(window_id)
    title_q, proc_q = (title_contains or "").lower().strip(), (process_contains or "").lower().strip()
    if not (title_q or proc_q):
        return int(user32.GetForegroundWindow() or 0)
    best, best_key = 0, (0, 0)
    for hwnd in _candidate_windows():
        score = _match_score(hwnd, title_q, proc_q)
        if score:
            width, height = _rect(hwnd)[2:]
            key = (score, width * height)
            if key > best_key:
                best, best_key = hwnd, key
    return best


def _window_payload(hwnd: int) -> dict[str, Any]:
    return {"id": hwnd, "title": _title(hwnd), "process": _process_name(hwnd), "rect": list(_rect(hwnd)),
            "minimized": bool(user32.IsIconic(hwnd)), "hidden": not user32.IsWindowVisible(hwnd)}


def _make_readable(hwnd: int) -> str | None:
    """Show a tray-hidden window / restore a minimized one WITHOUT taking focus (apps publish nothing while
    hidden or minimized). Returns a note for the caller, or None."""
    if not hwnd:
        return None
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNA)
        time.sleep(1.0)              # Electron/WebView2 apps repaint and rebuild their tree once shown
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            time.sleep(0.5)
        return "The app was hidden in the system tray - showed its window (without taking focus)."
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(0.8)              # UWP apps need a moment to resume
        return "The window was minimized - restored it (without taking focus) so its controls are readable."
    return None


def _activate(hwnd: int) -> bool:
    """Bring the app to the front with plain window APIs (AttachThreadInput + SetForegroundWindow) - no synthetic
    key or mouse input. Only needed when typing requires keyboard focus."""
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if int(user32.GetForegroundWindow() or 0) == hwnd:
        return True
    ours = int(kernel32.GetCurrentThreadId())
    threads = {int(user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None) or 0),
               int(user32.GetWindowThreadProcessId(hwnd, None) or 0)} - {0, ours}
    attached = [t for t in threads if user32.AttachThreadInput(ours, t, True)]
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in attached:
            user32.AttachThreadInput(ours, thread, False)
    time.sleep(0.2)
    return int(user32.GetForegroundWindow() or 0) == hwnd


def _screen() -> tuple[int, int, int, int]:
    """The virtual screen (all monitors)."""
    metric = user32.GetSystemMetrics
    return int(metric(76)), int(metric(77)), int(metric(78)), int(metric(79))


def _clip(rect: tuple, area: tuple) -> tuple[int, int, int, int]:
    left, top = max(rect[0], area[0]), max(rect[1], area[1])
    right, bottom = min(rect[0] + rect[2], area[0] + area[2]), min(rect[1] + rect[3], area[1] + area[3])
    return left, top, max(0, right - left), max(0, bottom - top)


def _intersects(rect: tuple, area: tuple) -> bool:
    left, top, width, height = rect
    a_left, a_top, a_width, a_height = area
    return left < a_left + a_width and a_left < left + width and top < a_top + a_height and a_top < top + height


def _inside(inner: tuple, outer: tuple, slack: int = 2) -> bool:
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
            and inner[0] + inner[2] <= outer[0] + outer[2] + slack and inner[1] + inner[3] <= outer[1] + outer[3] + slack)


def _visible_area(hwnd: int) -> tuple[int, int, int, int]:
    rect = _rect(hwnd)
    return rect if user32.IsIconic(hwnd) else _clip(rect, _screen())


# ------------------------------------------------------------------
# UI Automation setup
# ------------------------------------------------------------------

_UIA: dict[str, Any] = {}


def _uia():
    if not _UIA:
        import comtypes.client

        home = Path(os.environ.get("WINMIND_HOME") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WinMind")
        cache = home / "comtypes_cache"   # generated COM wrappers: user data, not source
        cache.mkdir(parents=True, exist_ok=True)
        comtypes.client.gen_dir = str(cache)
        if str(cache.parent) not in sys.path:
            sys.path.insert(0, str(cache.parent))
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        try:
            client = comtypes.client.CreateObject(U.CUIAutomation8, interface=U.IUIAutomation6)
        except Exception:
            client = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        types = {getattr(U, n): n[4:-len("ControlTypeId")] for n in dir(U)
                 if n.startswith("UIA_") and n.endswith("ControlTypeId")}
        props = {
            "name": U.UIA_NamePropertyId, "type": U.UIA_ControlTypePropertyId,
            "offscreen": U.UIA_IsOffscreenPropertyId, "enabled": U.UIA_IsEnabledPropertyId,
            "aid": U.UIA_AutomationIdPropertyId, "cls": U.UIA_ClassNamePropertyId,
            "fw": U.UIA_FrameworkIdPropertyId, "accel": U.UIA_AcceleratorKeyPropertyId,
            "access": U.UIA_AccessKeyPropertyId, "focus": U.UIA_HasKeyboardFocusPropertyId,
            "password": U.UIA_IsPasswordPropertyId, "help": U.UIA_HelpTextPropertyId,
            # Windows 10 1703+: the section names web/WinUI apps publish ("navigation", "main", "search") and h1-h9
            "landmark": getattr(U, "UIA_LocalizedLandmarkTypePropertyId", 30158),
            "heading": getattr(U, "UIA_HeadingLevelPropertyId", 30173),
            "rid": U.UIA_RuntimeIdPropertyId,
            "p_invoke": U.UIA_IsInvokePatternAvailablePropertyId, "p_toggle": U.UIA_IsTogglePatternAvailablePropertyId,
            "p_value": U.UIA_IsValuePatternAvailablePropertyId, "p_expand": U.UIA_IsExpandCollapsePatternAvailablePropertyId,
            "p_select": U.UIA_IsSelectionItemPatternAvailablePropertyId, "p_range": U.UIA_IsRangeValuePatternAvailablePropertyId,
            "p_scroll": U.UIA_IsScrollItemPatternAvailablePropertyId, "p_text": U.UIA_IsTextPatternAvailablePropertyId,
            "p_scrollp": U.UIA_IsScrollPatternAvailablePropertyId,
            "p_legacy": U.UIA_IsLegacyIAccessiblePatternAvailablePropertyId,
            "v_toggle": U.UIA_ToggleToggleStatePropertyId, "v_value": U.UIA_ValueValuePropertyId,
            "v_ro": U.UIA_ValueIsReadOnlyPropertyId, "v_expand": U.UIA_ExpandCollapseExpandCollapseStatePropertyId,
            "v_selected": U.UIA_SelectionItemIsSelectedPropertyId, "v_range": U.UIA_RangeValueValuePropertyId,
            "v_min": U.UIA_RangeValueMinimumPropertyId, "v_max": U.UIA_RangeValueMaximumPropertyId,
            "legacy_action": U.UIA_LegacyIAccessibleDefaultActionPropertyId,
        }
        patterns = {
            "invoke": (U.UIA_InvokePatternId, U.IUIAutomationInvokePattern),
            "toggle": (U.UIA_TogglePatternId, U.IUIAutomationTogglePattern),
            "value": (U.UIA_ValuePatternId, U.IUIAutomationValuePattern),
            "expand": (U.UIA_ExpandCollapsePatternId, U.IUIAutomationExpandCollapsePattern),
            "select": (U.UIA_SelectionItemPatternId, U.IUIAutomationSelectionItemPattern),
            "range": (U.UIA_RangeValuePatternId, U.IUIAutomationRangeValuePattern),
            "scroll": (U.UIA_ScrollItemPatternId, U.IUIAutomationScrollItemPattern),
            "scrollp": (U.UIA_ScrollPatternId, U.IUIAutomationScrollPattern),
            "text": (U.UIA_TextPatternId, U.IUIAutomationTextPattern),
            "legacy": (U.UIA_LegacyIAccessiblePatternId, U.IUIAutomationLegacyIAccessiblePattern),
        }
        _UIA.update(client=client, U=U, types=types, props=props, patterns=patterns)
    return _UIA


INTERACTIVE_TYPES = {"Button", "Edit", "Hyperlink", "CheckBox", "RadioButton", "ComboBox", "ListItem", "MenuItem",
                     "TabItem", "TreeItem", "Slider", "Spinner", "SplitButton", "DataItem", "HeaderItem"}
# A press on these reaches the app's own handler. In WEB content the containers (list/tree items, groups, images,
# text) often don't handle the click themselves - an element inside them does.
SPECIFIC_TYPES = {"Button", "Hyperlink", "MenuItem", "TabItem", "CheckBox", "RadioButton", "ComboBox", "Edit",
                  "SplitButton", "Slider", "Spinner"}
CONTAINER_TYPES = {"ListItem", "TreeItem", "DataItem", "Group", "Image", "Text", "Pane", "Custom", "HeaderItem"}
WEB_FRAMEWORKS = {"Chrome", "Gecko"}
NOISE_NAMES = {"to get missing image descriptions, open the context menu."}   # Chromium's image-description prompt
GATED = {"v_toggle": "p_toggle", "v_value": "p_value", "v_ro": "p_value", "v_expand": "p_expand",
         "v_selected": "p_select", "v_range": "p_range", "v_min": "p_range", "v_max": "p_range",
         "legacy_action": "p_legacy"}
TOGGLE_STATES = {0: "unchecked", 1: "checked", 2: "mixed"}
EXPAND_STATES = {0: "collapsed", 1: "expanded", 2: "partly expanded"}


def _read(element: Any, cached: bool) -> dict[str, Any]:
    """All properties we care about, from the bulk-fetch cache or live from the app."""
    u = _uia()
    get = element.GetCachedPropertyValue if cached else element.GetCurrentPropertyValue
    raw: dict[str, Any] = {}
    for key, pid in u["props"].items():
        if key in GATED and not raw.get(GATED[key]):
            continue   # the pattern isn't there; its state property would be a "not supported" marker
        try:
            raw[key] = get(pid)
        except Exception:
            raw[key] = None
    rect = element.CachedBoundingRectangle if cached else element.CurrentBoundingRectangle
    raw["rect"] = (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
    raw["type"] = u["types"].get(raw.get("type"), str(raw.get("type")))
    return raw


def _actions(raw: dict[str, Any]) -> list[str]:
    acts = []
    if raw.get("p_value") and not raw.get("v_ro"):
        acts.append("set_value")
    if raw.get("p_range"):
        acts.append("set_value")
    if raw.get("p_toggle"):
        acts.append("toggle")
    if raw.get("p_expand") and raw.get("v_expand") != 3:
        acts.append("collapse" if raw.get("v_expand") == 1 else "expand")
    if raw.get("p_select"):
        acts.append("select")
    if raw.get("p_scrollp"):
        acts.append("scroll")
    if raw.get("p_invoke") or not acts:
        acts.append("click")
    return list(dict.fromkeys(acts))


def _row(ref: int, raw: dict[str, Any]) -> dict[str, Any]:
    left, top, width, height = raw["rect"]
    name = _clean(raw.get("name") or "")[:90]
    row: dict[str, Any] = {"ref": ref, "type": raw["type"], "name": name,
                           "center": [left + width // 2, top + height // 2], "rect": [left, top, width, height],
                           "actions": _actions(raw)}
    if not name:
        url = str(raw.get("v_value") or "") if raw.get("p_value") else ""
        hint = raw.get("help") or (url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] if url.startswith("http") else "") \
            or raw.get("aid") or raw.get("cls") or ""
        row["name"] = str(hint)[:60]
        row["unnamed"] = True
    if raw.get("p_value") and not raw.get("password") and raw.get("v_value"):
        value = " ".join(str(raw["v_value"]).split())[:80]
        if value != name[:80]:   # tree/list items often repeat their name as a value
            row["value"] = value
    if raw.get("p_range") and raw.get("v_range") is not None:
        row["value"] = f"{raw['v_range']:g} (range {raw.get('v_min') or 0:g}-{raw.get('v_max') or 100:g})"
    state = []
    if raw.get("enabled") is False:
        state.append("disabled")
    if raw.get("focus"):
        state.append("focused")
    if raw.get("p_toggle") and raw.get("v_toggle") in TOGGLE_STATES:
        state.append(TOGGLE_STATES[raw["v_toggle"]])
    if raw.get("p_expand") and raw.get("v_expand") in EXPAND_STATES:
        state.append(EXPAND_STATES[raw["v_expand"]])
    if raw.get("p_select") and raw.get("v_selected"):
        state.append("selected")
    if raw.get("password"):
        state.append("password")
    if raw.get("offscreen"):
        state.append("offscreen")
    if state:
        row["state"] = state
    keys = ", ".join(k for k in (raw.get("accel"), raw.get("access")) if k)
    if keys:
        row["keys"] = keys
    if raw.get("aid") and not row.get("unnamed"):
        row["automation_id"] = str(raw["aid"])[:60]
    return row


def _line(row: dict[str, Any]) -> str:
    text = f'{row["ref"]} {row["type"]} "{row["name"]}"'
    if "value" in row:
        text += f' value="{row["value"]}"'
    if row.get("state"):
        text += " " + ",".join(row["state"])
    text += f' @({row["center"][0]},{row["center"][1]}) {row["rect"][2]}x{row["rect"][3]} [{"|".join(row["actions"])}]'
    if row.get("keys"):
        text += f' keys={row["keys"]}'
    return text


# ------------------------------------------------------------------
# Fetching the tree (bulk, split where a host breaks it) + waking Chromium-family apps
# ------------------------------------------------------------------

_WOKEN: set[int] = set()
_OPAQUE: set[int] = set()   # windows that publish (almost) no tree even after waiting - don't wait again next time
MAP: dict[str, Any] = {"hwnd": 0, "made": 0.0, "elements": {}, "rows": {}, "next": 1, "texts_ms": 0}
LIGHT_PROPS = ("name", "type", "p_value", "v_value", "cls", "rid")


def _condition(onscreen: bool) -> Any:
    u = _uia()
    condition = u["client"].ControlViewCondition
    if onscreen:
        condition = u["client"].CreateAndCondition(
            condition, u["client"].CreatePropertyCondition(u["U"].UIA_IsOffscreenPropertyId, False))
    return condition


def _cache_request(scope: int | None = None, onscreen: bool = False, light: bool = False) -> Any:
    u = _uia()
    request = u["client"].CreateCacheRequest()
    for key, pid in u["props"].items():
        if not light or key in LIGHT_PROPS:
            request.AddProperty(pid)
    request.AddProperty(u["U"].UIA_BoundingRectanglePropertyId)
    if scope is not None:
        request.TreeScope = scope
        request.TreeFilter = _condition(onscreen)
    return request


def _find_all(root: Any, onscreen: bool = False) -> list[Any]:
    found = root.FindAllBuildCache(_uia()["U"].TreeScope_Descendants, _condition(onscreen), _cache_request())
    return [found.GetElement(i) for i in range(found.Length)]


def _cached_children(element: Any) -> list[Any]:
    try:
        found = element.GetCachedChildren()
    except Exception:
        return []
    if not found:
        return []
    return [found.GetElement(i) for i in range(found.Length)]


# WinUI 3 hosts that embed WebView2 (WhatsApp) navigate back into the same web content, so a whole-subtree fetch
# fails on them (and FindAll returns the same elements over and over: 14k instead of ~230).
SPLIT_CLASSES = {"WinUIDesktopWin32WindowClass", "Microsoft.UI.Content.DesktopChildSiteBridge", "InputSiteWindowClass"}
_SPLIT_LEARNED: set[tuple[str, str]] = set()   # (process, class) whose subtree fetch failed once - split right away next time


def _class_of(element: Any) -> str:
    try:
        return str(element.GetCachedPropertyValue(_uia()["props"]["cls"]) or "")
    except Exception:
        try:
            return str(element.CurrentClassName or "")
        except Exception:
            return ""


def _from_cache(cached: Any) -> dict[str, Any]:
    return {"el": cached, "kids": [_from_cache(child) for child in _cached_children(cached)]}


def _rid(element: Any) -> tuple | None:
    try:
        value = element.GetCachedPropertyValue(_uia()["props"]["rid"])
        return tuple(value) if value else None
    except Exception:
        return None


def _dedupe(tree: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop elements reached a second time (same RuntimeId) - WinUI hosts loop back into their WebView2 content."""
    seen: set[tuple] = set()

    def walk(node: dict[str, Any]) -> None:
        kept = []
        for kid in node["kids"]:
            rid = _rid(kid["el"])
            if rid is not None:
                if rid in seen:
                    continue
                seen.add(rid)
            walk(kid)
            kept.append(kid)
        node["kids"] = kept

    if tree:
        walk(tree)
    return tree


def _fetch_tree(element: Any, onscreen: bool, process: str = "", light: bool = False, depth: int = 0) -> dict[str, Any] | None:
    """Element + descendants as {"el": cached element, "kids": [...]}, in ONE cross-process call where the app
    allows it (TreeScope_Subtree). Where it doesn't, split: this node + its children in one call, then one
    subtree call per child."""
    tree = _fetch_subtree(element, onscreen, process, light, depth)
    return _dedupe(tree) if depth == 0 else tree


def _fetch_subtree(element: Any, onscreen: bool, process: str, light: bool, depth: int) -> dict[str, Any] | None:
    U = _uia()["U"]
    key = (process, _class_of(element))
    if key[1] not in SPLIT_CLASSES and key not in _SPLIT_LEARNED:
        try:
            return _from_cache(element.BuildUpdatedCache(_cache_request(U.TreeScope_Subtree, onscreen, light)))
        except Exception:
            _SPLIT_LEARNED.add(key)
    if depth > 15:
        return None
    try:
        cached = element.BuildUpdatedCache(_cache_request(U.TreeScope_Element | U.TreeScope_Children, onscreen, light))
    except Exception:
        return None
    kids = (_fetch_tree(child, onscreen, process, light, depth + 1) for child in _cached_children(cached))
    return {"el": cached, "kids": [k for k in kids if k]}


def _flatten(tree: dict[str, Any] | None) -> list[Any]:
    """Descendants in document order (the root itself excluded, like FindAll)."""
    out: list[Any] = []
    stack = list(reversed(tree["kids"])) if tree else []
    while stack:
        node = stack.pop()
        out.append(node["el"])
        stack.extend(reversed(node["kids"]))
    return out


def _wake_chromium(hwnd: int) -> bool:
    """Chromium/Electron/CEF/WebView2 build their accessibility tree only after an assistive tool asks for it.

    Ask every Chromium window of the app - the top level (Chrome_WidgetWin_*), child hosts (CEF inside Steam's
    SDL window, WebView2 inside Teams/WhatsApp), CEF's wrapper and the page's render widget
    (Chrome_RenderWidgetHostHWND, which can be missing while minimized) - both ways: MSAA (OBJID_CLIENT, what NVDA
    sends) and UIA (UiaRootObjectId, what Narrator sends).
    """
    targets = [c for c in _child_windows(hwnd) if _class(c).startswith(CHROMIUM_CLASSES)]
    if not targets and not _class(hwnd).startswith("Chrome_WidgetWin"):
        return False
    result = ctypes.c_size_t()
    for target in [hwnd, *targets]:
        for object_id in (OBJID_CLIENT, UIA_ROOT_OBJECT_ID):
            user32.SendMessageTimeoutW(target, WM_GETOBJECT, 0, object_id, SMTO_ABORTIFHUNG, 500, ctypes.byref(result))
    return True


_NOTES: list[str] = []   # things the last scan did that the caller should mention (e.g. a visibility wake)
_HOST: dict[str, Any] = {}


def _is_host(hwnd: int) -> bool:
    """Is this window the app WinMind itself runs inside (e.g. VS Code hosting Claude Code -> WinMind)? Heavy
    accessibility work there blocks the host's UI thread and can take the whole session down, so it is only
    read gently: no text extraction, no bringing windows forward, no waiting loops."""
    if "pids" not in _HOST:
        try:
            import psutil

            _HOST["pids"] = {p.pid for p in psutil.Process().parents()}
        except Exception:
            _HOST["pids"] = set()
    return _pid(hwnd) in _HOST["pids"]


def _take_notes() -> list[str]:
    notes = list(dict.fromkeys(_NOTES))
    _NOTES.clear()
    return notes


def _user_busy() -> bool:
    """A full-screen app/game or a presentation is in front - never pull another window over it."""
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value in (2, 3, 4)   # QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE
    except Exception:
        pass
    return False


def _settle(hwnd: int, fetch: Callable[[Any], tuple[Any, int]]) -> tuple[Any, Any, int, bool]:
    """Fetch, waiting for the tree to fill: Store apps resuming, pages loading, and Chromium-family apps building
    their tree after being woken (the first time, or after they switched accessibility off again)."""
    # A Chromium app is ready when its web Document exists - its frame alone (window buttons, views) can be 15+.
    root = _uia()["client"].ElementFromHandle(hwnd)
    chromium = _wake_chromium(hwnd)
    payload, count, has_document = fetch(root)

    def ready() -> bool:
        return has_document if chromium else count >= 15

    if _is_host(hwnd):
        _NOTES.append("This is the app WinMind runs inside, so it was read gently (one fetch, no text extraction, "
                      "never brought forward) - heavy accessibility work there can freeze it.")
        return root, payload, count, chromium
    if hwnd in _OPAQUE and not ready() and not chromium:
        return root, payload, count, chromium   # already waited once for this window: answer right away
    deadline = time.monotonic() + (1.2 if chromium else 3.0)
    while not ready() and time.monotonic() < deadline:
        time.sleep(0.4)
        if chromium:
            _wake_chromium(hwnd)   # render widgets appear late (new page, just restored): ask them too
        payload, count, has_document = fetch(root)
    previous = int(user32.GetForegroundWindow() or 0)
    if chromium and not ready() and previous != hwnd:
        # Chromium tracks window occlusion: the web content of a window covered by others counts as hidden and
        # gets no accessibility tree (Discord behind VS Code: 6 elements; visible: 191 within a second). Once
        # built, the tree stays readable in the background - so show the app for a moment, then give the
        # user's window back. Never over a full-screen game or presentation.
        if _user_busy():
            _NOTES.append("A full-screen app/game is in front, so this covered app wasn't brought forward to build "
                          "its tree - ask again when you're out of the game.")
        elif _activate(hwnd):
            deadline = time.monotonic() + 3.0
            while not ready() and time.monotonic() < deadline:
                time.sleep(0.35)
                _wake_chromium(hwnd)
                payload, count, has_document = fetch(root)
            if previous:
                _activate(previous)
            _NOTES.append(f"'{_title(hwnd)}' was covered by other windows, and Chromium-based apps build no "
                          "accessibility tree while hidden - brought it to the front for a moment (window APIs, no "
                          "mouse), then gave your window back. It stays readable in the background now.")
    if chromium and has_document:
        first_time = hwnd not in _WOKEN or count < 60
        _WOKEN.add(hwnd)
        if first_time:   # the document just appeared: wait while it keeps filling
            deadline = time.monotonic() + 4.0
            last, flat = count, 0
            while time.monotonic() < deadline:
                time.sleep(0.35)
                payload, count, has_document = fetch(root)
                flat = flat + 1 if count <= last * 1.05 + 5 else 0
                if flat and (count > 60 or flat >= 3):
                    break
                last = count
    if ready():
        _OPAQUE.discard(hwnd)
    else:
        _OPAQUE.add(hwnd)
    return root, payload, count, chromium


def _scan_tree(hwnd: int, onscreen: bool = False) -> tuple[Any, dict[str, Any] | None, bool]:
    """The window's element tree, woken and settled. Falls back to including offscreen elements when the
    on-screen filter leaves almost nothing (some providers mark everything offscreen)."""
    process = _process_name(hwnd).lower()
    document_type, type_prop = _uia()["U"].UIA_DocumentControlTypeId, _uia()["props"]["type"]

    def fetch(root: Any) -> tuple[dict[str, Any] | None, int, bool]:
        tree = _fetch_tree(root, onscreen, process)
        elements = _flatten(tree)
        has_document = False
        for element in elements:
            try:
                if element.GetCachedPropertyValue(type_prop) == document_type:
                    has_document = True
                    break
            except Exception:
                continue
        return tree, len(elements), has_document

    root, tree, count, chromium = _settle(hwnd, fetch)
    if onscreen and count < 15:
        tree = _fetch_tree(root, False, process)
    return root, tree, chromium


def _scan(hwnd: int, onscreen: bool = False) -> tuple[Any, list[Any], bool]:
    root, tree, chromium = _scan_tree(hwnd, onscreen)
    elements = _flatten(tree) if tree else _find_all(root, onscreen)   # tree navigation refused: plain search
    return root, elements, chromium


def _register(element: Any, row_data: dict[str, Any], hwnd: int) -> dict[str, Any]:
    if MAP["hwnd"] != hwnd:
        MAP.update(hwnd=hwnd, made=time.time(), elements={}, rows={}, next=1)
    ref = MAP["next"]
    MAP["next"] += 1
    row = _row(ref, row_data)
    MAP["elements"][ref] = element
    MAP["rows"][ref] = row
    return row


def _reset_map(hwnd: int) -> None:
    MAP.update(hwnd=hwnd, made=time.time(), elements={}, rows={}, next=1)


EMPTY_NOTE = ("This window publishes no accessibility tree right now: still loading, or it is a game/video/canvas "
              "(those draw pixels only - there is nothing to read without screen capture, which WinMind doesn't use). "
              "Try again in a moment.")


# ------------------------------------------------------------------
# ui_map: a numbered flat list
# ------------------------------------------------------------------

def ui_map(window_id: int | None = None, title_contains: str | None = None, process_contains: str | None = None,
           query: str | None = None, scope: str = "interactive", include_offscreen: bool = False,
           max_elements: int = 120, format: str = "lines", restore_minimized: bool = True) -> dict[str, Any]:
    """Number every usable element of a window with its exact position, state, shortcut and actions."""
    hwnd = _target_hwnd(window_id, title_contains, process_contains)
    if not hwnd:
        return _fail(f"No window for '{title_contains or process_contains or window_id}'. The app may not be running - "
                     "open it first (launch_and_wait_until_ready / launch_uri), then call ui_map again.",
                     source_api="EnumWindows")
    started = time.perf_counter()
    note = _make_readable(hwnd) if restore_minimized else None
    window = _window_payload(hwnd)
    if window["minimized"]:
        include_offscreen = True   # nothing is "on screen", but the accessibility actions still work
    try:
        _root, elements, chromium = _scan(hwnd, onscreen=not include_offscreen)
    except Exception as exc:
        return _fail(f"UI Automation scan failed: {exc}", source_api="IUIAutomationElement::BuildUpdatedCache")
    area = _visible_area(hwnd)
    needle = (query or "").lower().strip()
    limit = max(1, min(int(max_elements), 800))
    _reset_map(hwnd)
    rows, seen, counts, matched = [], set(), {}, 0
    for element in elements:
        try:
            raw = _read(element, cached=True)
        except Exception:
            continue
        rect = raw["rect"]
        visible = rect[2] > 0 and rect[3] > 0 and not raw.get("offscreen") and _intersects(rect, area)
        if not visible and not include_offscreen:
            continue
        name = str(raw.get("name") or "").strip()
        if name.lower() in NOISE_NAMES:
            continue
        has_action = any(raw.get(p) for p in ("p_invoke", "p_toggle", "p_expand", "p_select", "p_range")) \
            or (raw.get("p_value") and not raw.get("v_ro"))
        # Real controls always count; other things (web divs, panes) only if they have a name AND an action.
        interactive = raw["type"] in INTERACTIVE_TYPES or (bool(name) and has_action)
        if scope == "interactive" and not interactive:
            continue
        if scope != "interactive" and not (interactive or name):
            continue
        if needle:
            hay = " ".join(str(raw.get(k) or "") for k in ("name", "type", "aid", "v_value", "help")).lower()
            if needle not in hay:
                continue
        key = (name, rect)
        if key in seen:
            continue   # Chromium often exposes a link and its text as twins
        seen.add(key)
        matched += 1
        counts[raw["type"]] = counts.get(raw["type"], 0) + 1
        if len(rows) < limit:
            rows.append(_register(element, raw, hwnd))
    data: dict[str, Any] = {
        "window": window,
        "coordinate_space": "screen pixels",
        "shown": len(rows), "matched": matched, "scanned": len(elements),
        "truncated": matched > len(rows), "counts": counts,
        "scan_ms": int((time.perf_counter() - started) * 1000),
        "how_to_act": "ui_act(ref=N, action=...) - actions in [..]. set_value needs value=. Refs reset on every ui_map/ui_tree.",
    }
    if format == "json":
        data["elements"] = rows
    else:
        data["lines"] = [_line(r) for r in rows]
        data["legend"] = 'ref Type "name" [value] [state] @(center x,y) WxH [actions] [keys=shortcut]'
    warnings = ([note] if note else []) + _take_notes()
    if window["minimized"]:
        warnings.append("Window is minimized: positions aren't real and Store apps may list nothing. Actions still work.")
    if not rows:
        warnings.append(EMPTY_NOTE)
    elif chromium and len(elements) < 60:
        warnings.append("Chromium-based app exposed only a little so far - the page may still be loading. Call again in a second.")
    if data["truncated"]:
        warnings.append(f"Showing {len(rows)} of {matched}. Narrow it with query='...' or raise max_elements.")
    return _ok(data, warnings=warnings, source_api="UI Automation BuildUpdatedCache (bulk fetch)")


# ------------------------------------------------------------------
# ui_tree: the app as sections -> groups -> controls
# ------------------------------------------------------------------

TYPE_LABELS = {"ListItem": "item", "TreeItem": "treeitem", "DataItem": "row", "TabItem": "tab", "Tab": "tabs",
               "Edit": "textbox", "Hyperlink": "link", "MenuItem": "menuitem", "MenuBar": "menubar", "Menu": "menu",
               "CheckBox": "checkbox", "RadioButton": "radio", "ComboBox": "combobox", "Button": "button",
               "SplitButton": "button", "Slider": "slider", "Spinner": "spinner", "Text": "text", "Image": "image",
               "Group": "group", "List": "list", "Tree": "tree", "DataGrid": "grid", "Table": "table",
               "ToolBar": "toolbar", "Document": "document", "Pane": "pane", "StatusBar": "statusbar",
               "Header": "header", "HeaderItem": "column", "Window": "window", "Custom": "section",
               "ProgressBar": "progress", "Calendar": "calendar", "AppBar": "appbar"}
STRUCTURE_TYPES = {"List", "Tree", "Tab", "DataGrid", "Table", "ToolBar", "Menu", "MenuBar", "StatusBar", "Header",
                   "AppBar", "Window"}
TREE_DROP = {"ScrollBar", "Thumb", "Separator", "TitleBar", "ToolTip"}
ITEM_TYPES = {"ListItem", "DataItem", "TreeItem"}
WRAPPER_TYPES = {"Pane", "Document", "Group", "Custom"}
TEXT_BUDGET = {"controls": 12, "lines": 30}   # per ui_tree call: how many text controls get their text shown, lines each


def _text_of(element: Any, full: bool = False, limit: int = 4000) -> str:
    """The text INSIDE a control (editor, document, terminal, text box) via TextPattern: what is visible on screen
    (GetVisibleRanges), or the whole document (DocumentRange) when full=True. Names/values don't carry this."""
    try:
        if _is_web(element):
            # Chromium answers TextPattern range queries on its UI thread and they get very slow on big content
            # (they froze VS Code for a minute) - the value it already holds is the same text, for free.
            value = _pattern(element, "value")
            return str(value.CurrentValue or "")[:limit] if value is not None else ""
        pattern = _pattern(element, "text")
        if pattern is None:
            return ""
        if full:
            return str(pattern.DocumentRange.GetText(limit) or "")
        ranges = pattern.GetVisibleRanges()
        parts: list[str] = []
        for i in range(ranges.Length if ranges else 0):
            parts.append(str(ranges.GetElement(i).GetText(limit) or ""))
            if sum(len(p) for p in parts) >= limit:
                break
        text = "\n".join(parts)
        return text if text.strip() else str(pattern.DocumentRange.GetText(limit) or "")
    except Exception:
        return ""


def _holds_text(raw: dict[str, Any]) -> bool:
    """Controls whose content is text the tree should show: editors, text boxes, terminals, native documents.
    Web pages' Documents are left out: their text is already in the tree as text nodes, and Chromium's
    TextPattern on a big page is very slow (4 calls on VS Code's panels took 64 s)."""
    if not raw.get("p_text") or raw["type"] not in ("Edit", "Document"):
        return False
    return raw["type"] == "Edit" or str(raw.get("fw") or "") not in WEB_FRAMEWORKS


def _clean(text: Any) -> str:
    """Drop icon-font glyphs (Unicode private-use characters, e.g. VS Code's codicons) and squeeze whitespace."""
    return " ".join("".join(ch for ch in str(text) if not ("" <= ch <= "" or ord(ch) >= 0xF0000)).split())


def _heading_level(raw: dict[str, Any]) -> int:
    try:
        level = int(raw.get("heading") or 0) - 80050   # HeadingLevel_None = 80050, HeadingLevel1..9 = 80051..80059
    except (TypeError, ValueError):
        return 0
    return level if 1 <= level <= 9 else 0


def _merge_badges(kids: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A short number right after a button/tab is its badge (WhatsApp 'Chats' + '42' unread) - show it on the button."""
    merged: list[dict[str, Any]] = []
    for kid in kids:
        name = kid["name"]
        if (merged and kid["type"] == "Text" and not kid["kids"] and 0 < len(name) <= 4 and name.strip("+").isdigit()
                and merged[-1]["interactive"] and not merged[-1].get("badge")):
            merged[-1]["badge"] = name
            continue
        merged.append(kid)
    return merged


def _tree_walk(source: dict[str, Any], area: tuple, include_offscreen: bool, depth: int, max_depth: int) -> list[dict[str, Any]]:
    """One UIA node -> zero or more display nodes. Unnamed wrappers vanish (their children move up), text that only
    repeats the parent's label is folded into it, scrollbars/separators/decorative images are dropped."""
    element = source["el"]
    try:
        raw = _read(element, cached=True)
    except Exception:
        return []
    kind = raw["type"]
    if kind in TREE_DROP:
        return []
    kids: list[dict[str, Any]] = []
    if depth < max_depth:
        for child in source["kids"]:
            kids.extend(_tree_walk(child, area, include_offscreen, depth + 1, max_depth))
    rect = raw["rect"]
    visible = rect[2] > 0 and rect[3] > 0 and not raw.get("offscreen") and _intersects(rect, area)
    if not visible and not include_offscreen and not kids:
        return []
    name = _clean(raw.get("name") or "")
    low = name.lower()
    if low in NOISE_NAMES:
        return kids
    if name:
        # text/images/empty groups that only repeat this node's label add nothing
        kids = [k for k in kids if not (k["type"] in ("Text", "Image", "Group", "Custom") and not k["kids"]
                                        and not k.get("textline") and k["name"].lower() in low)]
    if kind in ITEM_TYPES:
        # grid rows / list items often contain the same entry again (row -> cell -> cell, row -> group -> button
        # with the row's own label): fold those in - clicking the row already reaches that inner button
        folded: list[dict[str, Any]] = []
        for kid in kids:
            same = not kid["name"] or kid["name"].lower() in low
            if same and kid["type"] in ITEM_TYPES | {"Group", "Custom"}:
                folded.extend(kid["kids"])
            else:
                folded.append(kid)
        kids = [k for k in folded if not (k["type"] in ("Button", "Hyperlink") and not k["kids"]
                                          and k["name"] and k["name"].lower() == low)]
    kids = _merge_badges(kids)
    if visible and _holds_text(raw) and TEXT_BUDGET["controls"] > 0:
        # the text inside an editor/document/terminal becomes lines under it, next to its buttons
        TEXT_BUDGET["controls"] -= 1
        started = time.perf_counter()
        content = _text_of(element)
        if time.perf_counter() - started > 1.5:
            TEXT_BUDGET["controls"] = 0   # a slow text provider: don't pay that again in this call
        lines = [_clean(line)[:160] for line in content.splitlines()]
        lines = [line for line in lines if line and line.lower() != low]
        shown = lines[:TEXT_BUDGET["lines"]]
        for line in shown:
            kids.append({"element": element, "raw": {"name": line, "type": "Text", "rect": raw["rect"]},
                         "type": "Text", "name": line, "kids": [], "interactive": False, "landmark": "",
                         "heading": 0, "textline": True})
        if len(lines) > len(shown):
            more = f"… {len(lines) - len(shown)} more lines (ui_read(full=True) returns all of it)"
            kids.append({"element": element, "raw": {"name": more, "type": "Text", "rect": raw["rect"]},
                         "type": "Text", "name": more, "kids": [], "interactive": False, "landmark": "",
                         "heading": 0, "textline": True})
    has_action = any(raw.get(p) for p in ("p_invoke", "p_toggle", "p_expand", "p_select", "p_range")) \
        or (raw.get("p_value") and not raw.get("v_ro"))
    node = {"element": element, "raw": raw, "type": kind, "name": name, "kids": kids,
            "interactive": kind in INTERACTIVE_TYPES or (bool(name) and has_action),
            "landmark": str(raw.get("landmark") or ""), "heading": _heading_level(raw)}
    if kind in ITEM_TYPES and not name and not kids:
        return []   # an empty cell
    if node["interactive"] or node["landmark"] or node["heading"]:
        return [node]
    if kind in ("Text", "Image"):
        return [node] if name and visible else kids
    if kind in STRUCTURE_TYPES or kind == "Document" or (name and kind in ("Group", "Pane", "Custom")):
        if not kids:
            return [node] if name and kind != "Pane" else []
        only = kids[0] if len(kids) == 1 else None
        if only is not None and (not name or only["name"] == name
                                 or (kind in WRAPPER_TYPES and not only["interactive"] and only["type"] not in ("Text", "Image"))):
            return kids   # a wrapper that adds nothing (pane -> pane -> document -> group chains)
        return [node]
    return kids   # unnamed wrapper: its children take its place


def _tree_label(node: dict[str, Any], hwnd: int) -> str:
    raw, kind = node["raw"], node["type"]
    wants_ref = node["interactive"] or bool(node["kids"])
    row = _register(node["element"], raw, hwnd) if wants_ref else _row(0, raw)
    label = TYPE_LABELS.get(kind, kind.lower())
    if node["heading"]:
        label = f"heading{node['heading']}"
    elif node["landmark"] and not node["interactive"]:
        label = node["landmark"]
    text = (f"[{row['ref']}] " if wants_ref else "") + label
    if node["name"]:
        text += f' "{node["name"][:90]}"'
    elif row.get("unnamed") and row["name"]:
        text += f" ({row['name']})"
    if node.get("badge"):
        text += f" ({node['badge']})"
    if "value" in row:
        text += f' = "{row["value"]}"'
    states = [s for s in row.get("state", []) if s != "offscreen"]
    if states:
        text += " " + ",".join(states)
    if node["interactive"] or raw.get("p_scrollp"):
        text += f' [{"|".join(row["actions"])}]'
    if kind in ("List", "Tree", "DataGrid", "Table", "Tab", "Menu") and node["kids"]:
        text += f" ({len(node['kids'])})"
    return text


def _tree_render(nodes: list[dict[str, Any]], prefix: str, lines: list[str], budget: int, max_items: int, hwnd: int) -> None:
    for index, node in enumerate(nodes):
        if len(lines) >= budget:
            lines.append(prefix + f"└─ … {len(nodes) - index} more here (raise max_nodes, or ui_tree(ref=N) on a section)")
            return
        last = index == len(nodes) - 1
        lines.append(prefix + ("└─ " if last else "├─ ") + _tree_label(node, hwnd))
        kids = node["kids"]
        if not kids:
            continue
        own_ref = MAP["next"] - 1
        child_prefix = prefix + ("   " if last else "│  ")
        shown = kids if len(kids) <= max_items + 3 else kids[:max_items]
        _tree_render(shown, child_prefix, lines, budget, max_items, hwnd)
        if len(shown) < len(kids) and len(lines) < budget:
            lines.append(child_prefix + f"└─ … {len(kids) - len(shown)} more (ui_tree(ref={own_ref}) lists them all)")


def _tree_count(nodes: list[dict[str, Any]]) -> tuple[int, int]:
    total = interactive = 0
    for node in nodes:
        total += 1
        interactive += bool(node["interactive"])
        sub_total, sub_interactive = _tree_count(node["kids"])
        total += sub_total
        interactive += sub_interactive
    return total, interactive


def ui_tree(window_id: int | None = None, title_contains: str | None = None, process_contains: str | None = None,
            ref: int | None = None, max_nodes: int = 200, max_items: int = 25, max_depth: int = 40,
            include_offscreen: bool = False) -> dict[str, Any]:
    """The app as a tree - sections, lists, tabs and every clickable thing, numbered for ui_act."""
    started = time.perf_counter()
    note = None
    if ref is not None:
        start = MAP["elements"].get(int(ref))
        hwnd = MAP["hwnd"]
        if start is None:
            return _fail(f"No section {ref} in the current map. Run ui_tree (or ui_map) first - refs reset on every call.",
                         source_api="WinMind ui tree")
        tree = _fetch_tree(start, not include_offscreen, _process_name(hwnd).lower())
        if tree is None:
            return _fail("That section is gone (the app changed screen) - run ui_tree again.",
                         source_api="IUIAutomationElement::BuildUpdatedCache")
        header_name = str(MAP["rows"].get(int(ref), {}).get("name") or "")
    else:
        hwnd = _target_hwnd(window_id, title_contains, process_contains)
        if not hwnd:
            return _fail(f"No window for '{title_contains or process_contains or window_id}'. Open the app first "
                         "(launch_and_wait_until_ready / launch_uri), then call ui_tree again.", source_api="EnumWindows")
        note = _make_readable(hwnd)
        if user32.IsIconic(hwnd):
            include_offscreen = True
        try:
            _root, tree, _chromium = _scan_tree(hwnd, onscreen=not include_offscreen)
        except Exception as exc:
            return _fail(f"UI Automation scan failed: {exc}", source_api="IUIAutomationElement::BuildUpdatedCache")
        header_name = _title(hwnd)
    area = _visible_area(hwnd)
    _reset_map(hwnd)
    TEXT_BUDGET.update(controls=0 if _is_host(hwnd) else 12, lines=30)
    nodes: list[dict[str, Any]] = []
    for child in (tree or {"kids": []})["kids"]:
        nodes.extend(_tree_walk(child, area, include_offscreen, 1, max(2, min(int(max_depth), 80))))
    total, interactive = _tree_count(nodes)
    budget = max(20, min(int(max_nodes), 1500))
    lines = [f'{header_name}  ({_process_name(hwnd)}, {"section " + str(ref) if ref is not None else "window"})']
    _tree_render(nodes, "", lines, budget, max(3, int(max_items)), hwnd)
    warnings = ([note] if note else []) + _take_notes()
    if not nodes:
        warnings.append(EMPTY_NOTE)
    return _ok({"tree": "\n".join(lines), "nodes": total, "clickable": interactive,
                "scan_ms": int((time.perf_counter() - started) * 1000),
                "how_to_act": "ui_act(ref=N) on any [N]; ui_tree(ref=N) zooms into a section and lists all of it; "
                              "ui_act(ref=N, action='scroll', value='down') scrolls a list. After an action changes the "
                              "screen, call ui_tree again (refs reset every call)."},
               warnings=warnings, source_api="UI Automation BuildUpdatedCache(TreeScope_Subtree) + landmarks/headings")


# ------------------------------------------------------------------
# Acting - accessibility actions and messages to the app's own window: no mouse, no global keystrokes
# ------------------------------------------------------------------

CLICKS = {"click", "invoke", "press", "double_click", "open"}
SCROLLS = {"down": (2, 4), "up": (2, 1), "page_down": (2, 3), "page_up": (2, 0), "right": (4, 2), "left": (1, 2)}


def _pattern(element: Any, name: str) -> Any:
    u = _uia()
    pattern_id, interface = u["patterns"][name]
    available_prop = {"invoke": "p_invoke", "toggle": "p_toggle", "value": "p_value", "expand": "p_expand",
                      "select": "p_select", "range": "p_range", "scroll": "p_scroll", "scrollp": "p_scrollp",
                      "text": "p_text", "legacy": "p_legacy"}[name]
    if not element.GetCurrentPropertyValue(u["props"][available_prop]):
        return None
    return element.GetCurrentPattern(pattern_id).QueryInterface(interface)


def _is_web(element: Any) -> bool:
    try:
        return str(element.CurrentFrameworkId or "") in WEB_FRAMEWORKS
    except Exception:
        return False


def _type_of(element: Any) -> str:
    try:
        return _uia()["types"].get(element.CurrentControlType, "")
    except Exception:
        return ""


def _deep_default_action(element: Any, skip_self: bool = False) -> str | None:
    """What a real click in the middle of the element would hit, done through accessibility instead of the mouse:
    the deepest element under its centre that has a default action. Chromium gives text inside clickable things
    the action 'click ancestor', which clicks the nearest ancestor that handles clicks - so this reaches the
    element that really owns the click handler. Falls back to a link/button anywhere inside, then the element."""
    try:
        box = element.CurrentBoundingRectangle
    except Exception:
        return None
    cx, cy = (box.left + box.right) // 2, (box.top + box.bottom) // 2
    tree = _fetch_tree(element, False)
    if tree is None:
        return None
    best = None
    stack = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        stack.extend((kid, depth + 1) for kid in node["kids"])
        if skip_self and depth == 0:
            continue
        try:
            raw = _read(node["el"], cached=True)
        except Exception:
            continue
        action = str(raw.get("legacy_action") or "") if raw.get("p_legacy") else ""
        if not action and not raw.get("p_invoke"):
            continue
        r = raw["rect"]
        centred = r[0] <= cx < r[0] + r[2] and r[1] <= cy < r[1] + r[3]
        rank = 0 if centred and depth else (1 if raw["type"] in SPECIFIC_TYPES and depth else (2 if not depth else 3))
        key = (rank, r[2] * r[3])
        if best is None or key < best[0]:
            best = (key, node["el"], raw, action)
    if best is None:
        return None
    _key, target, raw, action = best
    label = " ".join(str(raw.get("name") or "").split())[:40]
    if action:
        _pattern(target, "legacy").DoDefaultAction()
        return f'default action "{action}" of the {raw["type"]} "{label}"'
    _pattern(target, "invoke").Invoke()
    return f'InvokePattern on the {raw["type"]} "{label}"'


def _has_focus(element: Any) -> bool:
    """Does keyboard focus sit on this element, or inside it (e.g. the editable node of a rich editor)?"""
    client = _uia()["client"]
    try:
        node = client.GetFocusedElement()
        walker = client.ControlViewWalker
        for _ in range(8):
            if not node:
                return False
            if client.CompareElements(node, element):
                return True
            node = walker.GetParentElement(node)
    except Exception:
        return False
    return False


def _read_back(element: Any) -> str:
    for name, get in (("value", lambda p: p.CurrentValue), ("text", lambda p: p.DocumentRange.GetText(4000))):
        try:
            pattern = _pattern(element, name)
            if pattern is not None:
                text = str(get(pattern) or "")
                if text:
                    return text
        except Exception:
            continue
    try:
        return str(element.CurrentName or "")
    except Exception:
        return ""


def _host_window(element: Any, hwnd: int) -> int:
    """The window that takes keyboard messages for an element: its own handle, else its nearest ancestor's (web
    content has none; Chromium's accessibility-only render widget hands keys to its parent), else the app window."""
    walker = _uia()["client"].ControlViewWalker
    current = element
    for _ in range(60):
        try:
            handle = int(current.CurrentNativeWindowHandle or 0)
        except Exception:
            handle = 0
        if handle:
            if _class(handle).startswith("Chrome_RenderWidgetHostHWND"):
                return int(user32.GetParent(handle) or handle)
            return handle
        try:
            current = walker.GetParentElement(current)
        except Exception:
            break
        if not current:
            break
    return hwnd


def _focus_window(hwnd: int) -> int:
    """The window that holds keyboard focus inside the app (a WebView2/CEF child may even belong to another
    process) - known only while the app is in front."""
    info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
    if user32.GetGUIThreadInfo(0, ctypes.byref(info)) and info.hwndFocus:
        focus = int(info.hwndFocus)
        if int(user32.GetAncestor(focus, GA_ROOT) or 0) == hwnd:
            return focus
    return 0


def _post_text(target: int, text: str) -> None:
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:   # emoji etc.: a UTF-16 surrogate pair, one WM_CHAR per half
            code -= 0x10000
            user32.PostMessageW(target, WM_CHAR, 0xD800 + (code >> 10), 1)
            user32.PostMessageW(target, WM_CHAR, 0xDC00 + (code & 0x3FF), 1)
        else:
            user32.PostMessageW(target, WM_CHAR, code, 1)


def _focus_on(hwnd: int, element: Any) -> bool:
    """Give the element keyboard focus through UIA; bring the app to the front (window APIs) if it needs that.
    Returns whether the window had to be brought to the front."""
    try:
        element.SetFocus()
    except Exception:
        pass
    time.sleep(0.1)
    if _has_focus(element):
        return False
    _activate(hwnd)
    try:
        element.SetFocus()
    except Exception:
        pass
    time.sleep(0.15)
    if not _has_focus(element):
        raise RuntimeError("Couldn't put the keyboard focus on that element, so nothing was typed (stray characters "
                           "could land somewhere else). Pick the text box itself (textbox/combobox in ui_tree).")
    return True


def _type_into(hwnd: int, element: Any, text: str, replace: bool = True) -> str:
    """Type the way a screen reader would: UIA focus, select the old text through TextPattern (replace=True),
    then WM_CHAR messages to the app's own window. Refuses unless focus is confirmed on the box."""
    activated = _focus_on(hwnd, element)
    selected = not replace
    if replace:
        try:
            text_pattern = _pattern(element, "text")
            if text_pattern is not None:
                text_pattern.DocumentRange.Select()   # typed characters replace the selection
                selected = True
        except Exception:
            pass
    if not selected and _read_back(element).strip():
        try:
            _pattern(element, "value").SetValue("")
        except Exception:
            pass
    clean = " ".join(text.splitlines())   # a typed line break is Enter = "send" in chat apps
    _post_text(_focus_window(hwnd) or _host_window(element, hwnd), clean)
    time.sleep(0.25 + 0.003 * len(clean))
    how = "UIA focus + WM_CHAR messages to the app window" + (" (brought the app to the front first)" if activated else "")
    if clean != text:
        how += " (line breaks became spaces)"
    want = _norm(clean)[:60]
    if want:
        how += " - checked: the box shows the text" if want in _norm(_read_back(element)) else \
            " - couldn't read the text back; look at changes"
    return how


def _press_enter(hwnd: int, element: Any) -> str:
    _focus_on(hwnd, element)
    target = _focus_window(hwnd) or _host_window(element, hwnd)
    user32.PostMessageW(target, WM_KEYDOWN, VK_RETURN, 0x001C0001)
    user32.PostMessageW(target, WM_CHAR, 0x0D, 0x001C0001)
    user32.PostMessageW(target, WM_KEYUP, VK_RETURN, 0xC01C0001)
    return " + Enter (key messages to the app window)"


def _scroll(element: Any, value: str | None) -> str:
    walker = _uia()["client"].ControlViewWalker
    node, pattern = element, None
    for _ in range(15):
        try:
            pattern = _pattern(node, "scrollp")
        except Exception:
            pattern = None
        if pattern is not None:
            break
        try:
            node = walker.GetParentElement(node)
        except Exception:
            node = None
        if not node:
            break
    if pattern is None:
        raise RuntimeError("Nothing scrollable here or around it.")
    how = str(value or "down").lower().strip().replace(" ", "_")
    if how in ("top", "bottom"):
        pattern.SetScrollPercent(-1, 0.0 if how == "top" else 100.0)
    elif how.rstrip("%").replace(".", "", 1).isdigit():
        pattern.SetScrollPercent(-1, max(0.0, min(100.0, float(how.rstrip("%")))))
    else:
        horizontal, vertical = SCROLLS.get(how, SCROLLS["down"])
        pattern.Scroll(horizontal, vertical)
    try:
        where = f" - now at {pattern.CurrentVerticalScrollPercent:.0f}%"
    except Exception:
        where = ""
    return f"ScrollPattern {how}{where}"


def _perform(element: Any, hwnd: int, action: str, value: str | None, use_keyboard: bool) -> tuple[str, bool]:
    """Do the action. Returns (how it was done, whether it was a press on web content that may silently do nothing)."""
    web = _is_web(element)
    if action in CLICKS:
        if web and _type_of(element) in CONTAINER_TYPES:
            how = _deep_default_action(element)
            if how:
                return how + " (web item: its click handler sits on an element inside)", False
        for name, run in (("invoke", lambda p: p.Invoke()), ("toggle", lambda p: p.Toggle()),
                          ("select", lambda p: p.Select())):
            pattern = _pattern(element, name)
            if pattern is not None:
                run(pattern)
                return f"{name.capitalize()}Pattern", web
        pattern = _pattern(element, "expand")
        if pattern is not None:
            if pattern.CurrentExpandCollapseState == 1:
                pattern.Collapse()
            else:
                pattern.Expand()
            return "ExpandCollapsePattern", web
        legacy = _pattern(element, "legacy")
        if legacy is not None and legacy.CurrentDefaultAction:
            legacy.DoDefaultAction()
            return f"IAccessible default action ({legacy.CurrentDefaultAction})", web
        how = _deep_default_action(element, skip_self=True)
        if how:
            return how, False
        raise RuntimeError("This element has no accessibility action. Try its parent or child in ui_tree.")
    if action == "right_click":
        element.QueryInterface(_uia()["U"].IUIAutomationElement3).ShowContextMenu()
        return "ShowContextMenu (the app's own context menu)", False
    if action == "toggle":
        pattern = _pattern(element, "toggle")
        if pattern is None:
            raise RuntimeError("This element can't be toggled.")
        pattern.Toggle()
        return "TogglePattern", False
    if action in ("set_value", "type"):
        if value is None:
            raise RuntimeError("set_value needs value=...")
        rng = _pattern(element, "range")
        if rng is not None and not use_keyboard:
            rng.SetValue(float(value))
            return "RangeValuePattern", False
        if not web and not use_keyboard:
            pattern = _pattern(element, "value")
            if pattern is not None and not pattern.CurrentIsReadOnly:
                pattern.SetValue(str(value))
                if not _norm(value) or _norm(value)[:60] in _norm(_read_back(element)):
                    return "ValuePattern.SetValue (checked)", False
                # the app ignored the direct value - type it instead
        return _type_into(hwnd, element, str(value)), False
    if action in ("expand", "collapse"):
        pattern = _pattern(element, "expand")
        if pattern is None:
            raise RuntimeError("This element doesn't expand/collapse.")
        pattern.Expand() if action == "expand" else pattern.Collapse()
        return "ExpandCollapsePattern", False
    if action == "select":
        pattern = _pattern(element, "select")
        if pattern is None:
            raise RuntimeError("This element can't be selected.")
        pattern.Select()
        return "SelectionItemPattern", web
    if action == "focus":
        element.SetFocus()
        return "SetFocus", False
    if action == "scroll":
        return _scroll(element, value), False
    if action == "scroll_into_view":
        pattern = _pattern(element, "scroll")
        if pattern is None:
            raise RuntimeError("This element doesn't support scrolling into view.")
        pattern.ScrollIntoView()
        return "ScrollItemPattern", False
    raise RuntimeError(f"Unknown action '{action}'.")


TEXT_TYPES = {"Text", "Edit", "Document", "StatusBar", "Header", "ListItem", "DataItem", "Hyperlink", "TitleBar",
              "Button", "TreeItem", "TabItem"}


def _visible_texts(hwnd: int) -> list[str]:
    """Every on-screen text/value in the window (light bulk fetch) - used to show what an action changed."""
    u = _uia()
    U = u["U"]
    request = u["client"].CreateCacheRequest()
    for pid in (U.UIA_NamePropertyId, U.UIA_ControlTypePropertyId,
                U.UIA_IsValuePatternAvailablePropertyId, U.UIA_ValueValuePropertyId):
        request.AddProperty(pid)
    started = time.perf_counter()
    try:
        root = u["client"].ElementFromHandle(hwnd)
        tree = _fetch_tree(root, True, _process_name(hwnd).lower(), light=True)
        elements = _flatten(tree) if tree else [
            found.GetElement(i) for found in [root.FindAllBuildCache(U.TreeScope_Descendants, _condition(True), request)]
            for i in range(found.Length)]
    except Exception:
        return []
    texts: list[str] = []
    for element in elements:
        try:
            if u["types"].get(element.GetCachedPropertyValue(U.UIA_ControlTypePropertyId)) not in TEXT_TYPES:
                continue
            name = " ".join(str(element.GetCachedPropertyValue(U.UIA_NamePropertyId) or "").split())
            if name:
                texts.append(name[:120])
            if element.GetCachedPropertyValue(U.UIA_IsValuePatternAvailablePropertyId):
                value = " ".join(str(element.GetCachedPropertyValue(U.UIA_ValueValuePropertyId) or "").split())
                if value and value != name:
                    texts.append(f"{name[:40]}: {value[:120]}" if name else value[:120])
        except Exception:
            continue
    MAP["texts_ms"] = int((time.perf_counter() - started) * 1000)
    return list(dict.fromkeys(texts))


def _changes(before: list[str], hwnd: int) -> dict[str, Any]:
    after = _visible_texts(hwnd)
    old = set(before)
    appeared = [t for t in after if t not in old]
    gone = [t for t in before if t not in set(after)]
    summary: dict[str, Any] = {"now_showing": appeared[:8]}
    if gone:
        summary["no_longer_showing"] = gone[:5]
    if len(appeared) > 8:
        summary["note"] = f"{len(appeared)} new texts - the screen changed a lot (new page/dialog?). ui_tree to see it."
    if not appeared and not gone:
        summary["note"] = "No visible text changed."
    return summary


def _state(hwnd: int, element: Any) -> tuple:
    """A cheap fingerprint of what the user sees: titles, the focused element, the element itself, visible texts."""
    client = _uia()["client"]
    try:
        focus = client.GetFocusedElement()
        box = focus.CurrentBoundingRectangle
        focused = (focus.CurrentName, focus.CurrentControlType, box.left, box.top, box.right, box.bottom)
    except Exception:
        focused = None
    try:
        own = _line(_row(0, _read(element, cached=False)))
    except Exception:
        own = "gone"
    texts = tuple(_visible_texts(hwnd)) if MAP.get("texts_ms", 0) < 700 else ()
    return _title(hwnd), _title(int(user32.GetForegroundWindow() or 0)), focused, own, texts


def _wait_for_effect(hwnd: int, element: Any, before: tuple, timeout: float = 0.9) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(0.15)
        if _state(hwnd, element) != before:
            return True
        if time.monotonic() >= deadline:
            return False


def _act_one(ref: int, action: str, value: str | None, press_enter: bool, use_keyboard: bool) -> dict[str, Any]:
    element = MAP["elements"].get(ref)
    if element is None:
        return {"ref": ref, "ok": False, "error": f"No element {ref} in the current map. Run ui_tree/ui_map first (refs reset on every call)."}
    hwnd = MAP["hwnd"]
    before = MAP["rows"][ref]
    try:
        element.CurrentName   # still alive?
    except Exception:
        return {"ref": ref, "ok": False, "error": "That element is gone (the window changed). Run ui_tree/ui_map again."}
    effect = None
    try:
        state_before = _state(hwnd, element)
        method, unsure = _perform(element, hwnd, action, value, use_keyboard)
        if action in CLICKS | {"select"}:
            effect = _wait_for_effect(hwnd, element, state_before)
            if unsure and not effect:
                # the press "worked" but nothing changed: the web app handles clicks on an element inside
                deeper = _deep_default_action(element, skip_self=True)
                if deeper:
                    method += f" -> nothing changed on screen, so did the {deeper}"
                    effect = _wait_for_effect(hwnd, element, state_before)
        if press_enter:
            method += _press_enter(hwnd, element)
    except Exception as exc:
        return {"ref": ref, "ok": False, "error": f"{action} failed on {ref} {before['type']} '{before['name']}': {exc}"}
    time.sleep(0.15)
    try:
        after = _line(_row(ref, _read(element, cached=False)))
    except Exception:
        after = "element gone (the app changed screen - run ui_tree again)"
    result = {"ref": ref, "ok": True, "method": method, "before": _line(before), "after": after}
    if effect is not None:
        result["effect"] = "screen changed" if effect else "no visible change (the app may not have reacted - check with ui_tree)"
    return result


SYNONYMS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
    "8": "eight", "9": "nine", "=": "equals", "+": "plus", "-": "minus", "*": "multiply by", "x": "multiply by",
    "×": "multiply by", "multiply": "multiply by", "times": "multiply by", "/": "divide by",
    "÷": "divide by", "divide": "divide by", ".": "decimal separator", "%": "percent", "c": "clear",
    "ac": "clear", "ok": "ok", "back": "back",
}


def _preference(row: dict[str, Any]) -> tuple:
    """Among equally good name matches: enabled first, then real controls over containers, then the innermost."""
    return "disabled" in row.get("state", []), row["type"] not in SPECIFIC_TYPES, row["rect"][2] * row["rect"][3]


def _resolve_names(names: list[str]) -> tuple[list[int], str | None]:
    """Element names -> refs in the current map (exact, then starts-with, then contains)."""
    refs = []
    rows = list(MAP["rows"].values())
    for wanted in names:
        want = str(wanted).strip().lower()
        if want in SYNONYMS and not any(r["name"].lower() == want for r in rows):
            want = SYNONYMS[want]   # screen-reader naming: "9" -> "Nine", "=" -> "Equals"
        pick = None
        for test in (lambda n: n == want, lambda n: n.startswith(want), lambda n: want in n):
            hits = [r for r in rows if test(r["name"].lower())]
            if hits:
                hits.sort(key=_preference)
                pick = hits[0]
                break
        if pick is None:
            available = ", ".join(sorted({f'"{r["name"]}"' for r in rows if r["name"]})[:40])
            if not available:
                return [], f'No element named "{wanted}": {EMPTY_NOTE}'
            return [], f'No element named "{wanted}". Names in this window: {available}'
        refs.append(pick["ref"])
    return refs, None


def ui_act(ref: int | None = None, action: str = "click", value: str | None = None, press_enter: bool = False,
           use_keyboard: bool = False, refs: list[int] | None = None, name: str | None = None,
           names: list[str] | None = None, window_id: int | None = None, title_contains: str | None = None,
           process_contains: str | None = None) -> dict[str, Any]:
    """Act on element(s) by ref (from ui_tree/ui_map) or by NAME. refs=[...] / names=[...] do a whole sequence."""
    wanted_names = [str(n) for n in (names or [])] or ([str(name)] if name else [])
    window_given = bool(window_id or title_contains or process_contains)
    target = _target_hwnd(window_id, title_contains, process_contains) if window_given else 0
    if window_given and not target:
        return _fail(f"No open window matches '{title_contains or process_contains or window_id}'. Open the app first "
                     "(launch_and_wait_until_ready / launch_uri), or check windows_summary.", source_api="EnumWindows")
    if window_given and not wanted_names and ref is None and not refs:
        if action in ("focus", "click", "restore", "maximize"):
            _activate(target)   # acting on the window itself
            return _ok({"focused_window": _title(target)}, source_api="AttachThreadInput + SetForegroundWindow",
                       risk_level="user_action")
        return _fail("Say which element: name= / names=[...] (or ref from ui_tree/ui_map).", source_api="WinMind ui map")
    if wanted_names:
        target = target or MAP["hwnd"] or int(user32.GetForegroundWindow() or 0)
        _make_readable(target)
        if target != MAP["hwnd"] or not MAP["rows"]:
            ui_map(window_id=target, max_elements=800, format="json")
        sequence, error = _resolve_names(wanted_names)
        if error:   # the map may be stale or filtered - refresh once
            ui_map(window_id=target, max_elements=800, format="json")
            sequence, error = _resolve_names(wanted_names)
        if error:
            return _fail(error, source_api="WinMind ui map")
    else:
        sequence = [int(r) for r in (refs or [])] or ([int(ref)] if ref is not None else [])
    if not sequence:
        return _fail("Give name= / names=[...] (easiest), or ref= / refs=[...] from ui_tree/ui_map.", source_api="WinMind ui map")
    hwnd = MAP["hwnd"]
    texts_before = _visible_texts(hwnd)
    steps = []
    for index, number in enumerate(sequence):
        last = index == len(sequence) - 1
        step = _act_one(number, action, value, press_enter and last, bool(use_keyboard))
        steps.append(step)
        if not step["ok"]:
            break
    time.sleep(0.2)
    changes = _changes(texts_before, hwnd)
    foreground = _title(int(user32.GetForegroundWindow() or 0))
    if len(sequence) == 1:
        step = steps[0]
        if not step["ok"]:
            return _fail(step["error"], source_api="UI Automation")
        return _ok({**step, "action": action, "changes": changes, "foreground_window": foreground},
                   source_api="UI Automation patterns / IAccessible / window messages", risk_level="user_action")
    done = sum(1 for s in steps if s["ok"])
    data = {"action": action, "done": done, "of": len(sequence),
            "steps": [(f'{s["before"].split(" @(")[0]} -> {s["method"]}' if s["ok"] else f'{s["ref"]}: {s["error"]}') for s in steps],
            "changes": changes, "foreground_window": foreground}
    if done < len(sequence):
        return {"ok": False, "error": f"Stopped at step {done + 1}: {steps[-1]['error']}", "data": data,
                "source_api": "UI Automation patterns / IAccessible / window messages"}
    return _ok(data, source_api="UI Automation patterns / IAccessible / window messages", risk_level="user_action")


# ------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------

READ_SKIP = {"Window", "TitleBar", "ScrollBar", "Thumb", "MenuBar", "Pane", "Separator"}


def ui_read(window_id: int | None = None, title_contains: str | None = None, process_contains: str | None = None,
            max_chars: int = 6000, full: bool = False) -> dict[str, Any]:
    """The visible text of any window in reading order: chat lists, messages, pages, dialogs, song titles - plus
    the text inside editors/documents/terminals (TextPattern; full=True = the whole document, not just the screen)."""
    hwnd = _target_hwnd(window_id, title_contains, process_contains)
    if not hwnd:
        return _fail(f"No window for '{title_contains or process_contains or window_id}'. Open the app first.",
                     source_api="EnumWindows")
    note = _make_readable(hwnd)
    window = _window_payload(hwnd)
    host = _is_host(hwnd)
    try:
        _root, elements, _chromium = _scan(hwnd, onscreen=True)
    except Exception as exc:
        return _fail(f"UI Automation scan failed: {exc}", source_api="IUIAutomationElement::BuildUpdatedCache")
    area = _visible_area(hwnd)
    items: list[tuple[int, tuple, str]] = []
    for order, element in enumerate(elements):
        try:
            raw = _read(element, cached=True)
        except Exception:
            continue
        rect = raw["rect"]
        if raw["type"] in READ_SKIP or rect[2] <= 0 or rect[3] <= 0 or raw.get("offscreen") or not _intersects(rect, area):
            continue
        name = _clean(raw.get("name") or "")
        if name.lower() in NOISE_NAMES:
            continue
        value = "" if raw.get("password") or not raw.get("p_value") else _clean(raw.get("v_value") or "")
        text = f"{name}: {value}" if name and value and value != name else (name or value)
        if _holds_text(raw) and not raw.get("password") and not host:
            inner = _text_of(element, full=full, limit=max(200, int(max_chars)))
            if inner.strip():
                text = f"{name}:\n{inner}" if name else inner   # editor/document/terminal content, line breaks kept
        if text:
            items.append((order, rect, text if "\n" in text else text[:500]))
    # Chromium repeats a list item's label in the text nodes inside it - keep only the fullest version
    kept: list[tuple[int, tuple, str, str]] = []
    for order, rect, text in sorted(items[:3000], key=lambda it: -(it[1][2] * it[1][3])):
        low = text.lower()
        if any(low in k_low and _inside(rect, k_rect) for _o, k_rect, _t, k_low in kept):
            continue
        kept.append((order, rect, text, low))
    kept.sort(key=lambda k: k[0])   # UIA order = the app's own reading order (DOM order for web apps)
    out, used, truncated = [], 0, False
    for _order, _rect_, text, _low in kept:
        if used + len(text) > max(200, int(max_chars)):
            truncated = True
            break
        out.append(text)
        used += len(text) + 1
    warnings = ([note] if note else []) + _take_notes()
    if not out:
        warnings.append(EMPTY_NOTE)
    return _ok({"window": window, "lines": out, "count": len(out), "truncated": truncated},
               warnings=warnings, source_api="UI Automation bulk fetch")


def _root_window(element: Any) -> int:
    """Top-level window an element lives in (web/XAML elements have no handle of their own - walk up)."""
    walker = _uia()["client"].ControlViewWalker
    current = element
    for _ in range(40):
        try:
            handle = int(current.CurrentNativeWindowHandle or 0)
            if handle:
                return int(user32.GetAncestor(handle, GA_ROOT) or handle)
            current = walker.GetParentElement(current)
            if not current:
                break
        except Exception:
            break
    return int(user32.GetForegroundWindow() or 0)


def _ancestors(element: Any, depth: int = 5) -> list[str]:
    u = _uia()
    walker = u["client"].ControlViewWalker
    chain, current = [], element
    for _ in range(depth):
        try:
            current = walker.GetParentElement(current)
            if not current:
                break
            name = " ".join(str(current.CurrentName or "").split())[:50]
            chain.append(f'{u["types"].get(current.CurrentControlType, "?")} "{name}"')
        except Exception:
            break
    return chain


def ui_element_at(x: int, y: int) -> dict[str, Any]:
    """What is at screen pixel (x, y)? Returns it with a ref for ui_act, plus its parents."""
    u = _uia()
    try:
        element = u["client"].ElementFromPoint(u["U"].tagPOINT(int(x), int(y)))
        raw = _read(element, cached=False)
    except Exception as exc:
        return _fail(f"Nothing readable at ({x},{y}): {exc}", source_api="IUIAutomation::ElementFromPoint")
    hwnd = _root_window(element)
    row = _register(element, raw, hwnd)
    return _ok({"element": _line(row), "details": row, "inside": _ancestors(element),
                "window": _window_payload(hwnd) if hwnd else None},
               source_api="IUIAutomation::ElementFromPoint")


def ui_focused() -> dict[str, Any]:
    """The element that has keyboard focus right now (where typing would go)."""
    u = _uia()
    try:
        element = u["client"].GetFocusedElement()
        raw = _read(element, cached=False)
    except Exception as exc:
        return _fail(f"Couldn't read the focused element: {exc}", source_api="IUIAutomation::GetFocusedElement")
    hwnd = _root_window(element)
    row = _register(element, raw, hwnd)
    return _ok({"element": _line(row), "details": row, "inside": _ancestors(element),
                "window": _window_payload(hwnd) if hwnd else None},
               source_api="IUIAutomation::GetFocusedElement")


def ui_find_text(text: str, window_id: int | None = None, title_contains: str | None = None,
                 process_contains: str | None = None, max_results: int = 10) -> dict[str, Any]:
    """Where is this text in a window? Searches control labels/web text and documents/editors (TextPattern)."""
    hwnd = _target_hwnd(window_id, title_contains, process_contains)
    if not hwnd:
        return _fail("No matching window.", source_api="EnumWindows")
    _make_readable(hwnd)
    try:
        _root, elements, _chromium = _scan(hwnd, onscreen=True)
    except Exception as exc:
        return _fail(f"UI Automation scan failed: {exc}", source_api="UI Automation")
    window = _window_payload(hwnd)
    area = _visible_area(hwnd)
    needle = str(text)
    low = needle.lower()
    limit = max(1, min(int(max_results), 50))
    labels: list[dict[str, Any]] = []
    passages: list[dict[str, Any]] = []
    text_hosts = 0
    _reset_map(hwnd)
    for element in elements:
        try:
            raw = _read(element, cached=True)
        except Exception:
            continue
        rect = raw["rect"]
        on_screen = rect[2] > 0 and rect[3] > 0 and not raw.get("offscreen") and _intersects(rect, area)
        name = str(raw.get("name") or "")
        # 1) text that IS an element: web text nodes, links, buttons, labels, list items
        if low in name.lower() and on_screen and raw["type"] not in ("Pane", "Window", "TitleBar", "Document") \
                and len(labels) < limit:
            row = _register(element, raw, hwnd)
            labels.append({"text": row["name"], "type": row["type"], "ref": row["ref"],
                           "rect": row["rect"], "center": row["center"]})
        # 2) text inside a document/editor (the exact word's box, not the whole control)
        if raw.get("p_text") and text_hosts < 8 and len(passages) < limit:
            text_hosts += 1
            try:
                search = _pattern(element, "text").DocumentRange.Clone()
                for _ in range(limit * 3):
                    found = search.FindText(needle, 0, 1)
                    if not found:
                        break
                    snippet = " ".join(str(found.GetText(200)).split())
                    if low not in snippet.lower():
                        break   # this provider's search is unreliable - stop trusting it
                    boxes = [int(v) for v in (found.GetBoundingRectangles() or [])]
                    if len(boxes) >= 4 and _intersects(tuple(boxes[:4]), area):
                        left, top, width, height = boxes[:4]
                        passages.append({"text": snippet, "in": f'{raw["type"]} "{name[:40]}"',
                                         "rect": [left, top, width, height], "center": [left + width // 2, top + height // 2]})
                        if len(passages) >= limit:
                            break
                    search.MoveEndpointByRange(0, found, 1)   # continue after this match
            except Exception:
                pass
    matches: list[dict[str, Any]] = []
    for hit in sorted(labels + passages, key=lambda h: (h["rect"][1], h["rect"][0])):
        if not any(abs(hit["center"][0] - m["center"][0]) < 6 and abs(hit["center"][1] - m["center"][1]) < 6 for m in matches):
            matches.append(hit)
    return _ok({"query": needle, "window": window, "matches": matches[:limit],
                "note": ("ref = usable with ui_act" if matches else
                         "Not visible right now. It may be scrolled out of view (ui_act action='scroll'), or not text.")},
               source_api="UI Automation element names + TextPattern.FindText")


def explorer_windows() -> dict[str, Any]:
    """Open File Explorer windows/tabs: the folder each shows and the files selected in it."""
    try:
        import win32com.client

        shell = win32com.client.Dispatch("Shell.Application")
        windows = shell.Windows()
    except Exception as exc:
        return _fail(f"Shell.Application unavailable: {exc}", source_api="Shell.Application")
    rows = []
    for index in range(windows.Count):
        try:
            window = windows.Item(index)
            if window is None or not str(window.FullName).lower().endswith("explorer.exe"):
                continue
            document = window.Document
            selected = [item.Path for item in document.SelectedItems()][:50]
            focused = document.FocusedItem.Path if document.FocusedItem is not None else None
            rows.append({"folder": document.Folder.Self.Path, "title": window.LocationName, "window_id": int(window.HWND),
                         "selected": selected, "selected_count": document.SelectedItems().Count, "focused_item": focused})
        except Exception:
            continue
    return _ok({"count": len(rows), "windows": rows}, source_api="Shell.Application.Windows (IShellWindows)")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_USER_ACTIONS = {"ui_act"}
_TARGET = {
    "window_id": {"type": "integer", "description": "Window handle from windows_summary."},
    "title_contains": {"type": "string", "description": "App name or part of the window title, e.g. 'spotify', 'discord', 'whatsapp', 'notepad'. Matches the process name too, and finds apps hidden in the tray."},
    "process_contains": {"type": "string", "description": "Part of the process name only, e.g. 'spotify'."},
}


def build(tool_factory: Callable[..., dict[str, Any]], user_action_names: set[str]) -> dict[str, Any]:
    user_action_names.update(_USER_ACTIONS)
    tools = [
        tool_factory(
            "ui_tree",
            "See ANY app as a TREE through its accessibility tree (no screenshots): sections (navigation, search, "
            "lists, tabs, toolbars, headings) with every button, text box, tab, link, list entry and chat entry "
            "inside, each actionable one numbered [N]. Works for Discord, Spotify, WhatsApp, Teams, Telegram, Steam, "
            "VS Code, Chrome pages, Office, Explorer, Settings and classic apps. ui_act(ref=N) acts on it; "
            "ui_tree(ref=N) zooms into one section (e.g. the chat that just opened) and lists all of it.",
            {**_TARGET,
             "ref": {"type": "integer", "description": "Zoom into this section/list from the previous ui_tree/ui_map."},
             "max_nodes": {"type": "integer", "description": "Max lines. Default 200."},
             "max_items": {"type": "integer", "description": "Items shown per long list before '… N more'. Default 25."},
             "max_depth": {"type": "integer", "description": "Max nesting depth. Default 40."},
             "include_offscreen": {"type": "boolean", "description": "Also include loaded items scrolled out of view."}}),
        tool_factory(
            "ui_map",
            "See ANY app like a screen reader: numbered flat list of every button, text box, link, tab, menu item, "
            "checkbox and list item in a window, with position, current value/state, keyboard shortcut and the "
            "actions it supports. Finds apps by name/process (even hidden in the tray) and wakes Electron/CEF/"
            "WebView2 apps. Then ui_act(ref=N). For the structure (sections, lists) use ui_tree.",
            {**_TARGET,
             "query": {"type": "string", "description": "Only elements whose name/type/value contains this, e.g. 'search'."},
             "scope": {"type": "string", "enum": ["interactive", "all"], "description": "interactive (default) = things you can click/type/select; all = also named labels/text."},
             "include_offscreen": {"type": "boolean", "description": "Also list elements scrolled out of view. Default false."},
             "max_elements": {"type": "integer", "description": "Max elements listed. Default 120."},
             "format": {"type": "string", "enum": ["lines", "json"], "description": "lines (default, compact) or json (full detail)."},
             "restore_minimized": {"type": "boolean", "description": "Show a minimized/tray-hidden window (without taking focus) so its controls are readable. Default true."}}),
        tool_factory(
            "ui_act",
            "Act on elements of ANY app by NAME (easiest: name=\"Play\" + title_contains=\"spotify\", or names=[\"One\","
            "\"Plus\",\"Two\",\"Equals\"] for a sequence) or by ref from ui_tree/ui_map. No mouse and no global "
            "keystrokes: accessibility actions (press, toggle, select, expand, the IAccessible default action of what "
            "is under a web item's centre), ScrollPattern, ShowContextMenu, and for web text boxes UIA focus + text "
            "messages to the app's window (only after focus is confirmed, then read back). Checks the screen changed "
            "and lists which texts appeared/disappeared.",
            {"name": {"type": "string", "description": "Visible name of ONE element, e.g. 'Save', 'Search', 'Equals'."},
             "names": {"type": "array", "items": {"type": "string"}, "description": "Several element names to act on in order."},
             **_TARGET,
             "ref": {"type": "integer", "description": "Element number from the latest ui_tree / ui_map / ui_element_at / ui_focused."},
             "refs": {"type": "array", "items": {"type": "integer"}, "description": "Several refs to act on in order."},
             "action": {"type": "string", "enum": ["click", "double_click", "right_click", "toggle", "set_value", "expand",
                                                   "collapse", "select", "focus", "scroll", "scroll_into_view"],
                        "description": "Default click. set_value fills a text box or slider (needs value). scroll needs value "
                                       "down/up/page_down/page_up/top/bottom or a percentage. right_click opens the app's context menu."},
             "value": {"type": "string", "description": "Text (or number for sliders) for set_value; direction for scroll."},
             "press_enter": {"type": "boolean", "description": "Press Enter after the (last) action, e.g. to submit a search or send a chat message."},
             "use_keyboard": {"type": "boolean", "description": "For set_value on native apps: type (text messages) instead of setting the value directly."}}),
        tool_factory(
            "ui_read",
            "Read the visible text of ANY app window in reading order from its accessibility tree: chat lists with "
            "unread counts, messages, song titles, pages, dialogs.",
            {**_TARGET,
             "max_chars": {"type": "integer", "description": "Default 6000."},
             "full": {"type": "boolean", "description": "For editors/documents/terminals: the whole text, not just what is on screen."}}),
        tool_factory(
            "ui_element_at",
            "What is at this screen pixel? Returns the element (type, name, value, state, rect, actions) with a ref usable "
            "by ui_act, plus the containers it sits in.",
            {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
        tool_factory(
            "ui_focused",
            "The element that has keyboard focus right now (where typing would go), with a ref usable by ui_act."),
        tool_factory(
            "ui_find_text",
            "Find where a piece of text is inside a window: in documents, editors, web pages (TextPattern) and on "
            "buttons/labels. Returns rectangles and refs for ui_act.",
            {"text": {"type": "string", "description": "Text to find (case-insensitive)."}, **_TARGET,
             "max_results": {"type": "integer", "description": "Default 10."}},
            ["text"]),
        tool_factory(
            "explorer_windows",
            "Open File Explorer windows and tabs: the folder each one shows and which files are selected in it "
            "(answers 'what folder am I in' / 'what did I select')."),
    ]
    handlers = {"ui_tree": ui_tree, "ui_map": ui_map, "ui_act": ui_act, "ui_read": ui_read,
                "ui_element_at": ui_element_at, "ui_focused": ui_focused, "ui_find_text": ui_find_text,
                "explorer_windows": explorer_windows}
    return {"tools": tools, "handlers": handlers}
