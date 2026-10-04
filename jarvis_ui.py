"""Compact Windows UI Automation layer for JARVIS.

Inspired by WinMind's UIA-first approach:
- semantic controls before screenshots/coordinates
- compact numbered refs
- one cached snapshot per target window
- action + lightweight verification
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from pywinauto import Desktop


_ACTIONABLE = {
    "button", "checkbox", "combobox", "edit", "hyperlink", "listitem",
    "menuitem", "radio", "radiobutton", "tab", "tabitem", "treeitem",
    "splitbutton", "slider", "spinner", "document",
}

_TEXTUAL = {
    "button", "checkbox", "combobox", "edit", "hyperlink", "listitem",
    "menuitem", "radio", "radiobutton", "tab", "tabitem", "treeitem",
    "text", "document", "list",
}


@dataclass
class _Ref:
    ref: str
    control: Any
    name: str
    control_type: str
    value: str
    enabled: bool
    depth: int


class JarvisUI:
    """Small stateful UIA facade.

    Refs are intentionally ephemeral. Any new snapshot invalidates old refs,
    which prevents JARVIS from acting on stale controls after the UI changes.
    """

    def __init__(self) -> None:
        self._desktop = Desktop(backend="uia")
        self._window = None
        self._window_key = ""
        self._refs: dict[str, _Ref] = {}
        self._last_fingerprint = ""

    # ---------- window resolution ----------

    def _windows(self, title: str = "", process: str = "") -> list[Any]:
        title = str(title or "").strip().casefold()
        process = str(process or "").strip().casefold()
        result: list[Any] = []

        for win in self._desktop.windows(visible_only=True):
            try:
                info = win.element_info
                name = (win.window_text() or "").strip()
                proc = str(getattr(info, "process_id", "") or "").strip()
                proc_name = ""
                try:
                    proc_name = str(win.process_id())
                except Exception:
                    pass

                if title and title not in name.casefold():
                    continue
                if process and process not in proc.casefold() and process != proc_name.casefold():
                    # Best-effort process selector. pywinauto does not expose
                    # process executable name consistently on every backend.
                    continue
                result.append(win)
            except Exception:
                continue

        return result

    def _pick_window(self, title: str = "", process: str = "") -> Any:
        if self._window is not None:
            try:
                if self._window.exists() and self._window.is_visible():
                    current = (self._window.window_text() or "").strip()
                    if (not title or title.casefold() in current.casefold()):
                        return self._window
            except Exception:
                pass

        windows = self._windows(title, process)
        if not windows:
            raise RuntimeError("No matching visible Windows UI was found.")

        # Prefer the largest/most populated window. This tends to pick the
        # actual app window over tiny transient helpers.
        scored: list[tuple[int, Any]] = []
        for win in windows:
            try:
                scored.append((len(win.descendants()), win))
            except Exception:
                scored.append((0, win))
        scored.sort(key=lambda item: item[0], reverse=True)

        self._window = scored[0][1]
        try:
            self._window_key = f"{self._window.handle}:{self._window.window_text()}"
        except Exception:
            self._window_key = str(id(self._window))
        return self._window

    # ---------- compact snapshot ----------

    @staticmethod
    def _safe_text(ctrl: Any) -> tuple[str, str]:
        try:
            name = (ctrl.window_text() or "").strip().replace("\n", " ")
        except Exception:
            name = ""
        try:
            value = str(getattr(ctrl.element_info, "value", "") or "").strip().replace("\n", " ")
        except Exception:
            value = ""
        return name[:240], value[:500]

    @staticmethod
    def _control_type(ctrl: Any) -> str:
        try:
            return str(ctrl.element_info.control_type or "").strip().lower()
        except Exception:
            return ""

    @staticmethod
    def _enabled(ctrl: Any) -> bool:
        try:
            return bool(ctrl.is_enabled())
        except Exception:
            return True

    @staticmethod
    def _depth(ctrl: Any) -> int:
        depth = 0
        try:
            parent = ctrl.parent()
            while parent is not None and depth < 30:
                depth += 1
                parent = parent.parent()
        except Exception:
            pass
        return depth

    def _make_snapshot(
        self,
        title: str = "",
        process: str = "",
        max_elements: int = 100,
        max_depth: int = 8,
    ) -> tuple[Any, list[_Ref]]:
        win = self._pick_window(title, process)
        refs: list[_Ref] = []
        seen: set[tuple[str, str, str]] = set()

        controls: list[Any] = [win]
        try:
            controls.extend(win.descendants())
        except Exception:
            pass

        for ctrl in controls:
            if len(refs) >= max_elements:
                break

            try:
                if getattr(ctrl.element_info, "is_password", False):
                    continue
            except Exception:
                pass

            ctype = self._control_type(ctrl)
            if not ctype:
                continue

            name, value = self._safe_text(ctrl)
            if not name and not value:
                continue

            depth = self._depth(ctrl)
            if depth > max_depth:
                continue

            # Avoid flooding the model with anonymous wrappers and duplicate
            # text nodes. Keep actionable controls even when their text repeats.
            key = (name.casefold(), ctype, value.casefold())
            if key in seen and ctype not in _ACTIONABLE:
                continue
            seen.add(key)

            if ctype not in _TEXTUAL:
                continue

            ref = f"u{len(refs) + 1}"
            refs.append(_Ref(ref, ctrl, name, ctype, value, self._enabled(ctrl), depth))

        self._refs = {item.ref: item for item in refs}
        self._window = win
        self._last_fingerprint = self._fingerprint(win)
        return win, refs

    def tree(
        self,
        title: str = "",
        process: str = "",
        max_elements: int = 100,
        max_depth: int = 8,
    ) -> str:
        try:
            _, refs = self._make_snapshot(title, process, max(10, min(180, int(max_elements))), max(1, min(15, int(max_depth))))
            win_title = (self._window.window_text() or "").strip()

            lines = [f"WINDOW: {win_title[:180]}"]
            for item in refs:
                label = item.name or item.value
                if item.name and item.value and item.value != item.name:
                    label = f'{item.name} = "{item.value[:160]}"'
                state = "" if item.enabled else " disabled"
                lines.append(
                    f'[{item.ref}] {item.control_type} "{label[:220]}"{state}'
                )

            return "\n".join(lines)[:18000] or "No readable UI controls found."
        except Exception as exc:
            return f"UI tree failed: {exc}"

    def find_text(self, text: str, title: str = "", process: str = "") -> str:
        target = str(text or "").strip().casefold()
        if not target:
            return "The search text is empty."

        try:
            _, refs = self._make_snapshot(title, process)
            exact: list[_Ref] = []
            partial: list[_Ref] = []

            for item in refs:
                haystack = f"{item.name} {item.value}".casefold()
                if item.name.casefold() == target or item.value.casefold() == target:
                    exact.append(item)
                elif target in haystack:
                    partial.append(item)

            matches = exact or partial
            if not matches:
                return f'No UI element matched "{text}".'

            lines = []
            for item in matches[:20]:
                label = item.name or item.value
                lines.append(
                    f'[{item.ref}] {item.control_type} "{label[:220]}"'
                    + ("" if item.enabled else " disabled")
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"UI find failed: {exc}"

    def focused(self) -> str:
        try:
            focused = self._desktop.get_active()
            name, value = self._safe_text(focused)
            ctype = self._control_type(focused)
            return (
                f'FOCUSED: {ctype} "{name or value}"'
                + (f' value="{value[:300]}"' if value and value != name else "")
            )
        except Exception as exc:
            return f"Could not read focused UI element: {exc}"

    def read(self, title: str = "", process: str = "", max_chars: int = 12000) -> str:
        try:
            _, refs = self._make_snapshot(title, process, max_elements=180)
            parts: list[str] = []
            for item in refs:
                text = item.name or item.value
                if item.name and item.value and item.value != item.name:
                    text = f"{item.name}: {item.value}"
                if text:
                    parts.append(text)
            result = "\n".join(parts)
            return result[:max_chars] if result else "No readable UI text found."
        except Exception as exc:
            return f"UI read failed: {exc}"

    # ---------- actions ----------

    def _resolve(self, ref: str = "", name: str = "", title: str = "", process: str = "") -> _Ref:
        if ref:
            item = self._refs.get(str(ref).strip())
            if item is not None:
                return item

        target = str(name or "").strip().casefold()
        if not target:
            raise RuntimeError("Provide a UI ref or element name.")

        # Refresh if a ref/name is not already available.
        _, refs = self._make_snapshot(title, process)

        exact = [
            item for item in refs
            if item.name.casefold() == target or item.value.casefold() == target
        ]
        if exact:
            return exact[0]

        partial = [
            item for item in refs
            if target in item.name.casefold() or target in item.value.casefold()
        ]
        if partial:
            # Prefer actionable controls and shorter labels.
            partial.sort(key=lambda x: (x.control_type not in _ACTIONABLE, len(x.name or x.value)))
            return partial[0]

        raise RuntimeError(f'UI element "{name}" was not found.')

    @staticmethod
    def _invoke(ctrl: Any) -> None:
        try:
            ctrl.invoke()
            return
        except Exception:
            pass
        ctrl.click_input()

    @staticmethod
    def _set_value(ctrl: Any, value: str) -> None:
        value = str(value)
        try:
            ctrl.set_edit_text(value)
            return
        except Exception:
            pass
        try:
            ctrl.iface_value.SetValue(value)
            return
        except Exception:
            pass

        # Last-resort text entry after focusing the actual control.
        ctrl.set_focus()
        try:
            import pyperclip
            import pyautogui
            pyperclip.copy(value)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.hotkey("ctrl", "v")
        except Exception as exc:
            raise RuntimeError(f"Could not set UI value: {exc}") from exc

    def _fingerprint(self, win: Any) -> str:
        try:
            chunks: list[str] = []
            for ctrl in [win, *win.descendants()[:80]]:
                name, value = self._safe_text(ctrl)
                ctype = self._control_type(ctrl)
                if name or value:
                    chunks.append(f"{ctype}|{name}|{value}")
            return hashlib.sha1("\n".join(chunks).encode("utf-8", "replace")).hexdigest()
        except Exception:
            return ""

    def act(
        self,
        action: str,
        ref: str = "",
        name: str = "",
        value: str = "",
        title: str = "",
        process: str = "",
    ) -> str:
        action = str(action or "").strip().lower()
        if action not in {
            "click", "set_value", "focus", "toggle", "select", "expand",
            "collapse", "scroll_into_view",
        }:
            return f"Unsupported UI action: {action}"

        try:
            item = self._resolve(ref, name, title, process)
            ctrl = item.control
            before = self._fingerprint(self._window)

            if action == "click":
                try:
                    self._invoke(ctrl)
                except Exception:
                    # WebView/Chromium often exposes a useful nested default
                    # action when the outer wrapper does nothing.
                    inner = None
                    try:
                        for child in ctrl.descendants():
                            if self._control_type(child) in _ACTIONABLE and self._safe_text(child)[0]:
                                inner = child
                                break
                    except Exception:
                        pass
                    if inner is None:
                        raise
                    self._invoke(inner)

            elif action == "set_value":
                self._set_value(ctrl, value)

            elif action == "focus":
                ctrl.set_focus()

            elif action == "toggle":
                try:
                    ctrl.toggle()
                except Exception:
                    self._invoke(ctrl)

            elif action == "select":
                try:
                    ctrl.select()
                except Exception:
                    self._invoke(ctrl)

            elif action in {"expand", "collapse"}:
                try:
                    pattern = ctrl.iface_expand_collapse
                    pattern.Expand() if action == "expand" else pattern.Collapse()
                except Exception:
                    self._invoke(ctrl)

            elif action == "scroll_into_view":
                ctrl.scroll_into_view()

            time.sleep(0.15)

            try:
                after = self._fingerprint(self._window)
            except Exception:
                after = ""

            changed = bool(before and after and before != after)
            label = item.name or item.value or item.ref

            # Read back editable values for set_value. This catches many silent
            # failures without forcing a screenshot.
            readback = ""
            if action == "set_value":
                rb_name, rb_value = self._safe_text(ctrl)
                readback = rb_value or rb_name

            return (
                f'UI action "{action}" succeeded on [{item.ref}] '
                f'"{label[:180]}". changed={str(changed).lower()}'
                + (f' readback="{readback[:500]}"' if readback else "")
            )
        except Exception as exc:
            return f'UI action "{action}" failed: {exc}'


_ui = JarvisUI()


def ui_tree(title: str = "", process: str = "", max_elements: int = 100, max_depth: int = 8) -> str:
    return _ui.tree(title, process, max_elements, max_depth)


def ui_find_text(text: str, title: str = "", process: str = "") -> str:
    return _ui.find_text(text, title, process)


def ui_focused() -> str:
    return _ui.focused()


def ui_read(title: str = "", process: str = "", max_chars: int = 12000) -> str:
    return _ui.read(title, process, max_chars)


def ui_act(
    action: str,
    ref: str = "",
    name: str = "",
    value: str = "",
    title: str = "",
    process: str = "",
) -> str:
    return _ui.act(action, ref, name, value, title, process)
