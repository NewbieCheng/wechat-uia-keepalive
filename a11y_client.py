"""Real UIA accessibility client simulator for WeChat (Narrator-like).

Attaches to WeChat via comtypes IUIAutomation, walks the control tree with
ControlViewWalker, and keeps the client alive so WeChat exposes full providers
(Qt51514QWindowIcon -> mmui::MainWindow).

Reference: https://blog.csdn.net/weixin_26763955/article/details/159909455
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import queue
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass, field
from typing import Any, Callable

import comtypes.client
import pythoncom
import win32api
import win32con
import win32gui
import win32process
import win32security

logger = logging.getLogger(__name__)

IUIAUTOMATION_CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"

WECHAT_PROCESS_NAMES = frozenset({"WeChat.exe", "Weixin.exe"})
WECHAT_TITLES = frozenset({"微信", "Weixin"})
WECHAT_SHELL_CLASS = "Qt51514QWindowIcon"
WECHAT_KNOWN_CLASSES = frozenset(
    {WECHAT_SHELL_CLASS, "mmui::MainWindow", "mmui::LoginWindow"}
)
VISIBLE_CLASS_PREFIX = "mmui::"

RENDER_PRESET_ORDER: tuple[str, ...] = ("community", "full_software", "angle_warp")

RENDER_PRESET_LABELS: dict[str, str] = {
    "community": "社区推荐",
    "full_software": "全软件栈",
    "angle_warp": "ANGLE WARP",
}

RENDER_PRESETS: dict[str, dict[str, str]] = {
    "community": {
        "QT_OPENGL": "software",
    },
    "full_software": {
        "QT_OPENGL": "software",
        "QT_QUICK_BACKEND": "software",
    },
    "angle_warp": {
        "QT_OPENGL": "software",
        "QT_QUICK_BACKEND": "software",
        "QT_ANGLE_PLATFORM": "warp",
    },
}

_GPU_ENV_KEYS: tuple[str, ...] = (
    "QT_OPENGL",
    "QT_ANGLE_PLATFORM",
    "QT_QUICK_BACKEND",
)

# Backward-compatible alias
SOFT_RENDER_ENV = RENDER_PRESETS["angle_warp"]

_session_cpu_launched = False
_session_render_preset = "community"

PM_REMOVE = 0x0001

# UIA ControlTypeId -> name (subset)
CONTROL_TYPE_NAMES: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}


@dataclass
class ElementInfo:
    name: str
    class_name: str
    control_type: str
    enabled: bool
    depth: int = 0

    def format_line(self) -> str:
        indent = "  " * self.depth
        return (
            f"{indent}Name={self.name!r}  Class={self.class_name!r}  "
            f"Type={self.control_type}  Enabled={self.enabled}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "class_name": self.class_name,
            "control_type": self.control_type,
            "enabled": self.enabled,
            "depth": self.depth,
        }


@dataclass
class ProbeResult:
    visible: bool
    reason: str
    hwnd: int = 0
    class_name: str = ""
    window_name: str = ""
    children_count: int = 0
    nodes_touched: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "visible": self.visible,
            "reason": self.reason,
            "hwnd": self.hwnd,
            "class_name": self.class_name,
            "window_name": self.window_name,
            "children_count": self.children_count,
            "nodes_touched": self.nodes_touched,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class LaunchResult:
    launched: bool
    reason: str = "ok"


def _process_basename(pid: int) -> str:
    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    try:
        path = win32process.GetModuleFileNameEx(handle, 0)
        return path.rsplit("\\", 1)[-1]
    except Exception:
        return ""
    finally:
        win32api.CloseHandle(handle)


def _is_wechat_process(pid: int) -> bool:
    name = _process_basename(pid)
    return name in WECHAT_PROCESS_NAMES


def find_wechat_hwnd() -> int:
    """Return the best WeChat main window handle, or 0."""
    hwnds = find_wechat_hwnds()
    return hwnds[0] if hwnds else 0


def find_wechat_hwnds() -> list[int]:
    """Enumerate candidate WeChat top-level window handles."""
    found: list[int] = []

    for title in WECHAT_TITLES:
        hwnd = win32gui.FindWindow(WECHAT_SHELL_CLASS, title)
        if hwnd and hwnd not in found:
            found.append(hwnd)

    def callback(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindow(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if title not in WECHAT_TITLES and cls not in WECHAT_KNOWN_CLASSES:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if _is_wechat_process(pid):
            if hwnd not in found:
                found.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)

    def _hwnd_priority(hwnd: int) -> tuple[int, int]:
        cls = win32gui.GetClassName(hwnd)
        if cls.startswith(VISIBLE_CLASS_PREFIX):
            return (0, hwnd)
        if cls == WECHAT_SHELL_CLASS:
            return (1, hwnd)
        return (2, hwnd)

    found.sort(key=_hwnd_priority)
    return found


def _is_visible_class(class_name: str) -> bool:
    return class_name.startswith(VISIBLE_CLASS_PREFIX)


def is_weixin_running() -> bool:
    """Return True if Weixin.exe is running."""
    for pid in win32process.EnumProcesses():
        try:
            if _process_basename(pid).lower() == "weixin.exe":
                return True
        except Exception:
            continue
    return False


def where_weixin() -> str:
    """Resolve Weixin.exe path from the current-user registry."""
    reg_path = r"Software\Tencent\Weixin"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
        install_dir = winreg.QueryValueEx(key, "InstallPath")[0]
    return os.path.join(install_dir, "Weixin.exe")


def get_weixin_version() -> str:
    """Read WeChat version from registry (same logic as pyweixin)."""
    try:
        reg_path = r"Software\Tencent\Weixin"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            int_version = winreg.QueryValueEx(key, "Version")[0]
        hex_str = hex(int_version)[5:]
        return f"{hex_str[0]}.{hex_str[1]}.{int(hex_str[2], 16)}.{int(hex_str[-2:], 16)}"
    except Exception:
        return ""


def get_weixin_file_version(exe_path: str) -> str:
    try:
        info = win32api.GetFileVersionInfo(exe_path, "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        return (
            f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}."
            f"{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
        )
    except Exception:
        return ""


def get_render_preset() -> str:
    return _session_render_preset


def set_render_preset(preset: str) -> str:
    global _session_render_preset
    if preset not in RENDER_PRESETS:
        raise ValueError(f"Unknown render preset: {preset!r}")
    _session_render_preset = preset
    return preset


def next_render_preset(current: str | None = None) -> str:
    preset = current or get_render_preset()
    if preset not in RENDER_PRESET_ORDER:
        return RENDER_PRESET_ORDER[0]
    idx = RENDER_PRESET_ORDER.index(preset)
    return RENDER_PRESET_ORDER[(idx + 1) % len(RENDER_PRESET_ORDER)]


def format_render_preset_label(preset: str | None = None) -> str:
    preset = preset or get_render_preset()
    label = RENDER_PRESET_LABELS.get(preset, preset)
    return f"{label} ({preset})"


def build_launch_env(preset: str | None = None) -> dict[str, str]:
    preset_id = preset or get_render_preset()
    if preset_id not in RENDER_PRESETS:
        raise ValueError(f"Unknown render preset: {preset_id!r}")

    env = os.environ.copy()
    for key in _GPU_ENV_KEYS:
        env.pop(key, None)
    env.update(RENDER_PRESETS[preset_id])
    return env


def _is_current_process_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _process_is_elevated(pid: int) -> bool:
    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    try:
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        try:
            return bool(win32security.IsTokenElevated(token))
        finally:
            win32api.CloseHandle(token)
    except Exception:
        return False
    finally:
        win32api.CloseHandle(handle)


def _weixin_pids() -> list[int]:
    pids: list[int] = []
    for pid in win32process.EnumProcesses():
        try:
            if _process_basename(pid).lower() == "weixin.exe":
                pids.append(pid)
        except Exception:
            continue
    return pids


def _system_dpi_percent() -> int:
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(100, int(round(dpi / 96 * 100)))
    except Exception:
        return 100


def collect_launch_readiness() -> dict[str, Any]:
    """Pre-flight checks before bootstrap launch."""
    issues: list[str] = []
    weixin_running = is_weixin_running()
    tool_elevated = _is_current_process_elevated()

    if weixin_running:
        issues.append("请先完全退出微信")
        pids = _weixin_pids()
        weixin_elevated = any(_process_is_elevated(pid) for pid in pids)
        if weixin_elevated != tool_elevated:
            issues.append("建议工具与微信都用/都不用管理员")

    dpi_percent = _system_dpi_percent()
    if dpi_percent > 100:
        issues.append(f"系统缩放 {dpi_percent}%，可设兼容性 DPI=系统")

    installed = False
    exe_path = ""
    try:
        exe_path = where_weixin()
        installed = os.path.isfile(exe_path)
        if not installed:
            issues.append("未找到 Weixin.exe")
    except Exception as exc:
        issues.append(f"未找到微信安装路径: {exc}")

    return {
        "ready": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "summary": "通过" if not issues else f"{len(issues)} 项待处理",
        "weixin_running": weixin_running,
        "tool_elevated": tool_elevated,
        "system_dpi_percent": dpi_percent,
        "installed": installed,
        "exe_path": exe_path,
    }


def get_render_mode() -> str:
    """Return render mode token for the status panel."""
    if not is_weixin_running():
        return "not_running"
    if _session_cpu_launched:
        return "cpu_software"
    return "unknown_running"


def launch_weixin(
    exe_path: str | None = None,
    *,
    preset: str | None = None,
) -> LaunchResult:
    """Launch WeChat with CPU software rendering if not already running."""
    global _session_cpu_launched, _session_render_preset

    preset_id = preset or get_render_preset()
    set_render_preset(preset_id)

    if is_weixin_running():
        return LaunchResult(launched=False, reason="already_running")

    path = exe_path or where_weixin()
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Weixin.exe not found: {path}")

    env = build_launch_env(preset_id)
    install_dir = os.path.dirname(path)
    subprocess.Popen(
        [path],
        cwd=install_dir,
        env=env,
        close_fds=False,
    )
    _session_cpu_launched = True
    return LaunchResult(launched=True, reason="ok")


def _control_type_name(control_type_id: int) -> str:
    return CONTROL_TYPE_NAMES.get(control_type_id, f"ControlType({control_type_id})")


def _valid_element(element: Any) -> bool:
    """True for a live IUIAutomationElement; comtypes uses null POINTER, not None."""
    if not element:
        return False
    try:
        _ = element.CurrentControlType
        return True
    except Exception:
        return False


def _read_element(element: Any) -> ElementInfo | None:
    if not _valid_element(element):
        return None
    try:
        return ElementInfo(
            name=element.CurrentName or "",
            class_name=element.CurrentClassName or "",
            control_type=_control_type_name(element.CurrentControlType),
            enabled=bool(element.CurrentIsEnabled),
        )
    except Exception as exc:
        logger.debug("read element failed: %s", exc)
        return None


def _count_siblings(element: Any, walker: Any) -> int:
    count = 0
    child = walker.GetFirstChildElement(element)
    while _valid_element(child):
        count += 1
        child = walker.GetNextSiblingElement(child)
    return count


def _walk_tree(
    element: Any,
    walker: Any,
    *,
    max_depth: int,
    max_children: int,
    depth: int = 0,
    counter: list[int] | None = None,
) -> int:
    if counter is None:
        counter = [0]
    if depth > max_depth:
        return counter[0]
    try:
        _ = element.CurrentClassName
        _ = element.CurrentName
        counter[0] += 1
    except Exception:
        return counter[0]

    child = walker.GetFirstChildElement(element)
    seen = 0
    while _valid_element(child) and seen < max_children:
        _walk_tree(
            child,
            walker,
            max_depth=max_depth,
            max_children=max_children,
            depth=depth + 1,
            counter=counter,
        )
        seen += 1
        child = walker.GetNextSiblingElement(child)
    return counter[0]


def _matches_element(
    info: ElementInfo,
    *,
    class_name: str | None = None,
    name: str | None = None,
    control_type: str | None = None,
) -> bool:
    if class_name is not None and class_name not in info.class_name:
        return False
    if name is not None and name not in info.name:
        return False
    if control_type is not None and control_type != info.control_type:
        return False
    return True


def _find_elements_impl(
    element: Any,
    walker: Any,
    *,
    class_name: str | None = None,
    name: str | None = None,
    control_type: str | None = None,
    max_depth: int = 8,
    max_children: int = 48,
    depth: int = 0,
    matches: list[ElementInfo] | None = None,
) -> list[ElementInfo]:
    if matches is None:
        matches = []

    info = _read_element(element)
    if info is not None and depth > 0:
        info.depth = depth
        if _matches_element(
            info,
            class_name=class_name,
            name=name,
            control_type=control_type,
        ):
            matches.append(info)

    if depth >= max_depth:
        return matches

    child = walker.GetFirstChildElement(element)
    seen = 0
    while _valid_element(child) and seen < max_children:
        _find_elements_impl(
            child,
            walker,
            class_name=class_name,
            name=name,
            control_type=control_type,
            max_depth=max_depth,
            max_children=max_children,
            depth=depth + 1,
            matches=matches,
        )
        seen += 1
        child = walker.GetNextSiblingElement(child)
    return matches


def _dump_siblings(
    element: Any,
    walker: Any,
    *,
    depth: int = 0,
    max_depth: int = 1,
    lines: list[ElementInfo] | None = None,
) -> list[ElementInfo]:
    if lines is None:
        lines = []
    child = walker.GetFirstChildElement(element)
    while _valid_element(child):
        info = _read_element(child)
        if info is not None:
            info.depth = depth
            lines.append(info)
            if depth + 1 < max_depth:
                _dump_siblings(
                    child,
                    walker,
                    depth=depth + 1,
                    max_depth=max_depth,
                    lines=lines,
                )
        child = walker.GetNextSiblingElement(child)
    return lines


def _pump_messages() -> None:
    user32 = ctypes.windll.user32
    msg = ctypes.wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


class _UIAutomationSTA:
    """Dedicated STA thread owning the IUIAutomation instance."""

    def __init__(self, use_control_view: bool = True) -> None:
        self.use_control_view = use_control_view
        self._queue: queue.Queue[tuple[Callable[[], Any], queue.Queue[Any]] | None] = (
            queue.Queue()
        )
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._init_error: Exception | None = None
        self.automation: Any = None
        self.walker: Any = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.alive:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="wechat-a11y-sta",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise TimeoutError("UIA STA thread failed to initialize within 15s")
        if self._init_error is not None:
            raise self._init_error

    def stop(self) -> None:
        if not self.alive:
            return
        self._queue.put(None)
        self._thread.join(timeout=5)

    def call(self, fn: Callable[[], Any], timeout: float = 30.0) -> Any:
        if not self.alive:
            self.start()
        result_queue: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._queue.put((fn, result_queue))
        try:
            status, value = result_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("STA call timed out") from exc
        if status == "error":
            raise value
        return value

    def _init_uia(self) -> None:
        ui_core = comtypes.client.GetModule("UIAutomationCore.dll")
        self.automation = comtypes.client.CreateObject(
            IUIAUTOMATION_CLSID,
            interface=ui_core.IUIAutomation,
        )
        if self.use_control_view:
            self.walker = self.automation.ControlViewWalker
        else:
            self.walker = self.automation.RawViewWalker

    def _run(self) -> None:
        pythoncom.CoInitialize()
        try:
            self._init_uia()
            self._ready.set()
        except Exception as exc:
            self._init_error = exc
            self._ready.set()
            return

        while True:
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                _pump_messages()
                continue

            if item is None:
                break

            fn, result_queue = item
            try:
                result_queue.put(("ok", fn()))
            except Exception as exc:
                result_queue.put(("error", exc))
            _pump_messages()

        pythoncom.CoUninitialize()


class WeChatA11yClient:
    """Narrator-like UIA client: attach via ElementFromHandle and walk the tree."""

    def __init__(
        self,
        interval: float = 2.0,
        tree_depth: int = 4,
        max_children: int = 24,
        use_control_view: bool = True,
        on_status: Callable[[ProbeResult], None] | None = None,
    ) -> None:
        self.interval = interval
        self.tree_depth = tree_depth
        self.max_children = max_children
        self.use_control_view = use_control_view
        self.on_status = on_status
        self._sta = _UIAutomationSTA(use_control_view=use_control_view)
        self._stop = threading.Event()
        self._loop_thread: threading.Thread | None = None

    def _attach(self, hwnd: int) -> Any:
        return self._sta.automation.ElementFromHandle(hwnd)

    def _probe_hwnd(self, target_hwnd: int) -> ProbeResult:
        try:
            root = self._attach(target_hwnd)
            info = _read_element(root)
            class_name = info.class_name if info else win32gui.GetClassName(target_hwnd)
            window_name = info.name if info else win32gui.GetWindowText(target_hwnd)

            nodes = _walk_tree(
                root,
                self._sta.walker,
                max_depth=self.tree_depth,
                max_children=self.max_children,
            )
            children_count = _count_siblings(root, self._sta.walker)

            info = _read_element(root)
            if info:
                class_name = info.class_name
                window_name = info.name

            visible = _is_visible_class(class_name)
            return ProbeResult(
                visible=visible,
                reason="ok" if visible else "ui_tree_hidden",
                hwnd=target_hwnd,
                class_name=class_name,
                window_name=window_name,
                children_count=children_count,
                nodes_touched=nodes,
            )
        except Exception as exc:
            return ProbeResult(
                visible=False,
                reason="probe_error",
                hwnd=target_hwnd,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _probe_impl(self, hwnd: int | None = None) -> ProbeResult:
        if hwnd is not None:
            return self._probe_hwnd(hwnd)

        target_hwnds = find_wechat_hwnds()
        if not target_hwnds:
            return ProbeResult(visible=False, reason="window_not_found")

        last = ProbeResult(visible=False, reason="ui_tree_hidden")
        best_visible: ProbeResult | None = None
        for target_hwnd in target_hwnds:
            result = self._probe_hwnd(target_hwnd)
            if not result.visible:
                last = result
                continue
            if result.class_name in ("mmui::MainWindow", "mmui::LoginWindow"):
                return result
            if best_visible is None:
                best_visible = result
        if best_visible is not None:
            return best_visible
        return last

    def probe(self, hwnd: int | None = None) -> ProbeResult:
        self._sta.start()
        return self._sta.call(lambda: self._probe_impl(hwnd))

    def walk_desktop_shallow(self, max_depth: int = 2) -> int:
        """Shallow walk of desktop root to signal an active a11y client."""

        def _impl() -> int:
            root = self._sta.automation.GetRootElement()
            return _walk_tree(
                root,
                self._sta.walker,
                max_depth=max_depth,
                max_children=min(self.max_children, 8),
            )

        self._sta.start()
        return self._sta.call(_impl)

    def dump(
        self,
        hwnd: int | None = None,
        *,
        max_depth: int = 1,
        include_root: bool = False,
    ) -> list[ElementInfo]:
        """Dump control tree siblings (blog default: direct children only)."""

        def _impl() -> list[ElementInfo]:
            target_hwnd = hwnd
            if target_hwnd is None:
                probe = self._probe_impl(None)
                if probe.hwnd == 0:
                    raise RuntimeError("WeChat window not found")
                target_hwnd = probe.hwnd
            root = self._attach(target_hwnd)
            lines: list[ElementInfo] = []
            if include_root:
                info = _read_element(root)
                if info is not None:
                    info.depth = 0
                    lines.append(info)
                lines.extend(
                    _dump_siblings(
                        root,
                        self._sta.walker,
                        depth=1,
                        max_depth=max_depth + 1,
                    )
                )
            else:
                lines.extend(
                    _dump_siblings(
                        root,
                        self._sta.walker,
                        depth=0,
                        max_depth=max_depth,
                    )
                )
            return lines

        self._sta.start()
        return self._sta.call(_impl)

    def find_elements(
        self,
        hwnd: int | None = None,
        *,
        class_name: str | None = None,
        name: str | None = None,
        control_type: str | None = None,
        max_depth: int | None = None,
    ) -> list[ElementInfo]:
        """Depth-first search for controls matching optional filters."""

        def _impl() -> list[ElementInfo]:
            target_hwnd = hwnd
            if target_hwnd is None:
                probe = self._probe_impl(None)
                if probe.hwnd == 0:
                    raise RuntimeError("WeChat window not found")
                target_hwnd = probe.hwnd
            root = self._attach(target_hwnd)
            return _find_elements_impl(
                root,
                self._sta.walker,
                class_name=class_name,
                name=name,
                control_type=control_type,
                max_depth=max_depth if max_depth is not None else self.tree_depth + 4,
                max_children=self.max_children,
            )

        self._sta.start()
        return self._sta.call(_impl)

    def find_first(
        self,
        hwnd: int | None = None,
        *,
        class_name: str | None = None,
        name: str | None = None,
        control_type: str | None = None,
        max_depth: int | None = None,
    ) -> ElementInfo | None:
        elements = self.find_elements(
            hwnd=hwnd,
            class_name=class_name,
            name=name,
            control_type=control_type,
            max_depth=max_depth,
        )
        return elements[0] if elements else None

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

    def _keepalive_loop(self) -> None:
        self._sta.start()
        while not self._stop.is_set():
            result = self.probe()
            if self.on_status:
                self.on_status(result)
            self._stop.wait(self.interval)

    def start(self, background: bool = True) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            return
        self._stop.clear()
        if background:
            self._loop_thread = threading.Thread(
                target=self._keepalive_loop,
                name="wechat-a11y-keepalive",
                daemon=True,
            )
            self._loop_thread.start()
        else:
            self._keepalive_loop()

    def stop(self) -> None:
        self._stop.set()
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=self.interval + 3)
        self._sta.stop()

    @property
    def running(self) -> bool:
        return self._loop_thread is not None and self._loop_thread.is_alive()


def collect_env_info(*, probe_ui: bool = False) -> dict[str, Any]:
    """Collect WeChat install/runtime info for the tool status panel."""
    readiness = collect_launch_readiness()
    info: dict[str, Any] = {
        "installed": readiness.get("installed", False),
        "exe_path": readiness.get("exe_path") or "",
        "registry_version": "",
        "file_version": "",
        "running": is_weixin_running(),
        "render_mode": get_render_mode(),
        "render_preset": get_render_preset(),
        "readiness": readiness,
        "ui_visible": False,
        "ui_class": "",
        "ui_reason": "",
        "window_name": "",
        "hwnd": 0,
        "children_count": 0,
        "nodes_touched": 0,
    }
    try:
        exe_path = info["exe_path"] or where_weixin()
        info["installed"] = os.path.isfile(exe_path)
        info["exe_path"] = exe_path
        info["registry_version"] = get_weixin_version()
        if info["installed"]:
            info["file_version"] = get_weixin_file_version(exe_path)
    except Exception as exc:
        info["ui_reason"] = f"env_error:{exc}"

    if probe_ui and info["running"]:
        client = WeChatA11yClient()
        try:
            result = client.probe()
            info["ui_visible"] = result.visible
            info["ui_class"] = result.class_name
            info["ui_reason"] = result.reason
            info["window_name"] = result.window_name
            info["hwnd"] = result.hwnd
            info["children_count"] = result.children_count
            info["nodes_touched"] = result.nodes_touched
            if result.error:
                info["error"] = result.error
        finally:
            client.stop()
    elif not info["running"]:
        info["ui_reason"] = "weixin_not_running"
    elif not probe_ui:
        info["ui_reason"] = "not_probed"

    info["render_mode"] = get_render_mode()
    info["render_preset"] = get_render_preset()
    info["readiness"] = collect_launch_readiness()
    return info


@dataclass
class KeepAliveSession:
    """Context manager that starts keepalive in a background thread."""

    client: WeChatA11yClient = field(default_factory=WeChatA11yClient)

    def __enter__(self) -> WeChatA11yClient:
        self.client.start(background=True)
        return self.client

    def __exit__(self, *_: Any) -> None:
        self.client.stop()
