"""Lightweight UIA accessibility client for WeChat on Windows.

WeChat 4.x hides its UI tree from casual automation probes. When a legitimate
UI Automation client connects, the app loads full control providers — the same
mechanism Windows Narrator uses, without launching narrator.exe.

Reference: https://blog.csdn.net/weixin_26763955/article/details/159909455
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pythoncom
import uiautomation as auto
import win32gui

WECHAT_TITLES = ("微信", "Weixin")
WECHAT_SHELL_CLASS = "Qt51514QWindowIcon"
VISIBLE_CLASS_PREFIX = "mmui::"


@dataclass
class ProbeResult:
    visible: bool
    reason: str
    hwnd: int = 0
    class_name: str = ""
    window_name: str = ""
    children_count: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "visible": self.visible,
            "reason": self.reason,
            "hwnd": self.hwnd,
            "class_name": self.class_name,
            "window_name": self.window_name,
            "children_count": self.children_count,
        }
        if self.error:
            payload["error"] = self.error
        return payload


def find_wechat_hwnd() -> int:
    for title in WECHAT_TITLES:
        hwnd = win32gui.FindWindow(WECHAT_SHELL_CLASS, title)
        if hwnd:
            return hwnd
    return 0


def _is_visible_class(class_name: str) -> bool:
    return class_name.startswith(VISIBLE_CLASS_PREFIX)


def find_wechat_control() -> auto.Control | None:
    """Locate WeChat top-level window via UIA, visible or hidden."""
    for title in WECHAT_TITLES:
        for class_name in ("mmui::MainWindow", "mmui::LoginWindow", WECHAT_SHELL_CLASS):
            control = auto.WindowControl(
                searchDepth=1,
                Name=title,
                ClassName=class_name,
            )
            if control.Exists(0, 0):
                return control
    return None


def touch_ui_tree(root: auto.Control, max_depth: int = 4, max_children: int = 12) -> int:
    """Walk the UIA tree so WeChat keeps control providers active."""
    visited = 0

    def walk(control: auto.Control, depth: int) -> None:
        nonlocal visited
        if depth > max_depth:
            return
        try:
            _ = control.ClassName
            _ = control.Name
            _ = control.ControlTypeName
            visited += 1
            for child in control.GetChildren()[:max_children]:
                walk(child, depth + 1)
        except Exception:
            return

    walk(root, 0)
    return visited


class WeChatUIAClient:
    """Minimal UIA client that periodically probes WeChat via UIA."""

    def __init__(
        self,
        interval: float = 2.0,
        tree_depth: int = 4,
        on_status: Callable[[ProbeResult], None] | None = None,
    ) -> None:
        self.interval = interval
        self.tree_depth = tree_depth
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def probe(self) -> ProbeResult:
        pythoncom.CoInitialize()
        hwnd = find_wechat_hwnd()
        if hwnd == 0:
            return ProbeResult(visible=False, reason="window_not_found")

        try:
            control = find_wechat_control()
            if control is None:
                control = auto.ControlFromHandle(hwnd)
            class_name = control.ClassName
            window_name = control.Name

            # Always walk the tree — this is what makes WeChat treat us as an
            # accessibility client and eventually swap Qt51514QWindowIcon → mmui::*
            touched = touch_ui_tree(control, max_depth=self.tree_depth)
            children = control.GetChildren()
            class_name = control.ClassName
            visible = _is_visible_class(class_name)

            return ProbeResult(
                visible=visible,
                reason="ok" if visible else "ui_tree_hidden",
                hwnd=hwnd,
                class_name=class_name,
                window_name=window_name,
                children_count=max(len(children), touched),
            )
        except Exception as exc:
            return ProbeResult(
                visible=False,
                reason="probe_error",
                hwnd=hwnd,
                error=f"{type(exc).__name__}: {exc}",
            )

    def wait_until_visible(
        self,
        timeout: float = 300.0,
        poll_interval: float | None = None,
    ) -> ProbeResult:
        poll = poll_interval if poll_interval is not None else self.interval
        deadline = time.monotonic() + timeout
        last = ProbeResult(visible=False, reason="timeout")

        while time.monotonic() < deadline:
            last = self.probe()
            if last.visible:
                return last
            if self.on_status:
                self.on_status(last)
            time.sleep(poll)

        last.reason = "timeout"
        return last

    def _loop(self) -> None:
        pythoncom.CoInitialize()
        while not self._stop.is_set():
            result = self.probe()
            if self.on_status:
                self.on_status(result)
            self._stop.wait(self.interval)

    def start(self, background: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if background:
            self._thread = threading.Thread(
                target=self._loop,
                name="wechat-uia-keepalive",
                daemon=True,
            )
            self._thread.start()
        else:
            self._loop()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval + 2)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


@dataclass
class KeepAliveSession:
    """Context manager that starts keepalive in a background thread."""

    client: WeChatUIAClient = field(default_factory=WeChatUIAClient)

    def __enter__(self) -> WeChatUIAClient:
        self.client.start(background=True)
        return self.client

    def __exit__(self, *_: Any) -> None:
        self.client.stop()
