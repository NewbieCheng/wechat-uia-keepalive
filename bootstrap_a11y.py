#!/usr/bin/env python
"""Bootstrap WeChat 4.x UIA: warm up a11y client, launch WeChat, verify UI tree."""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import sys
import time
from datetime import datetime
from typing import Any, Callable

from a11y_client import (
    ElementInfo,
    ProbeResult,
    WeChatA11yClient,
    is_weixin_running,
    launch_weixin,
)

try:
    from tool_log import get_logger as _tool_logger
except ImportError:
    _tool_logger = None


def _log(message: str, *args: Any) -> None:
    if _tool_logger is None:
        return
    try:
        _tool_logger().info(message, *args)
    except Exception:
        pass

_ENABLE_VT_PROCESSING = 0x0004


def _ensure_utf8_console() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(
                handle, mode.value | _ENABLE_VT_PROCESSING
            )
    except Exception:
        pass


def _console_width() -> int:
    try:
        return max(shutil.get_terminal_size(fallback=(80, 24)).columns, 60)
    except Exception:
        return 80


class _ProgressLine:
    """Single-line in-place progress output (no scrolling)."""

    def __init__(self) -> None:
        self._active = False

    def update(self, text: str) -> None:
        width = _console_width()
        line = text[: max(width - 1, 1)]
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()
        self._active = True

    def finish(self, final_text: str | None = None) -> None:
        if not self._active:
            return
        if final_text:
            width = _console_width()
            sys.stdout.write("\r\033[K" + final_text[: max(width - 1, 1)] + "\n")
        else:
            sys.stdout.write("\r\033[K\n")
        sys.stdout.flush()
        self._active = False


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _render_progress_bar(elapsed: float, total: float, width: int = 24) -> str:
    if total <= 0:
        ratio = 1.0
    else:
        ratio = min(max(elapsed / total, 0.0), 1.0)
    filled = int(width * ratio)
    if filled >= width:
        bar = "=" * width
    elif filled <= 0:
        bar = ">" + "-" * (width - 1)
    else:
        bar = "=" * filled + ">" + "-" * (width - filled - 1)
    percent = int(ratio * 100)
    return (
        f"[{bar}] {percent:3d}% "
        f"{_format_duration(elapsed)}/{_format_duration(total)}"
    )


def _emit(payload: dict[str, Any], jsonl: bool) -> None:
    if jsonl:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def _log_info(message: str) -> None:
    print(message, flush=True)


def _log_phase(phase: str, jsonl: bool, **extra: Any) -> None:
    if not jsonl:
        _log_phase_human(phase, **extra)
    else:
        payload = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "phase": phase,
            **extra,
        }
        _emit(payload, jsonl=True)
    _log("phase=%s %s", phase, extra)


def _log_phase_human(phase: str, **extra: Any) -> None:
    if phase == "bootstrap_start":
        delay = extra.get("launch_delay_sec", 360)
        _log_info("=" * 56)
        _log_info("  微信 4.x UIA 前置引导")
        _log_info("=" * 56)
        _log_info(
            f"[步骤 1/4] 预热 {int(delay)} 秒后启动微信；"
            f"期间请保持本窗口运行。"
        )
        _log("bootstrap_start delay=%ss interval=%s depth=%s", delay, extra.get("interval"), extra.get("depth"))
        return
    if phase == "launch_skipped":
        _log_info("[步骤 2/4] 已跳过启动微信。")
        return
    if phase == "launch_weixin":
        if extra.get("already_running"):
            _log_info("[步骤 2/4] 检测到微信已在运行，跳过启动。")
        else:
            _log_info("[步骤 2/4] 正在启动微信...")
        return
    if phase == "launch_failed":
        _log_info(f"[错误] 无法启动微信: {extra.get('error', 'unknown')}")
        return
    if phase == "launch_done":
        if extra.get("launched"):
            _log_info("[步骤 2/4] 微信已启动。")
        return
    if phase == "wait_visible":
        timeout = int(extra.get("timeout_sec", 300))
        _log_info(f"[步骤 3/4] 等待微信 UI 树暴露（最多 {timeout} 秒）...")
        return
    if phase == "dump_empty":
        _log_info("[警告] 控件树为空，UI 可能仍未完全暴露。")
        return
    if phase == "dump_start":
        _log_info(f"[步骤 4/4] 导出控件树，共 {extra.get('count', 0)} 个控件：")
        return
    if phase == "bootstrap_done":
        _log_info(
            f"[完成] 前置引导成功，UI 已可见，检测到 {extra.get('controls', 0)} 个控件。"
        )
        return
    if phase == "bootstrap_stopped":
        return


def _print_probe(result: ProbeResult, jsonl: bool, phase: str) -> None:
    if jsonl:
        payload = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "phase": phase,
            **result.to_dict(),
        }
        _emit(payload, jsonl=True)
        return

    if phase == "wait_result":
        if result.visible:
            _log_info(
                f"[结果] UI 已暴露: {result.class_name} "
                f"(hwnd={result.hwnd}, 子控件={result.children_count})"
            )
        else:
            _log_info(
                f"[结果] UI 仍未暴露: reason={result.reason}, "
                f"class={result.class_name or 'unknown'}"
            )
            if result.error:
                _log_info(f"[结果] 错误: {result.error}")
        _log(
            "probe %s visible=%s class=%s hwnd=%s reason=%s children=%s",
            phase,
            result.visible,
            result.class_name,
            result.hwnd,
            result.reason,
            result.children_count,
        )
        return

    if phase == "once_probe":
        status = "可见" if result.visible else "不可见"
        _log_info(
            f"[探测] {status} | class={result.class_name or '-'} | "
            f"reason={result.reason} | children={result.children_count}"
        )


def _print_dump(elements: list[ElementInfo], jsonl: bool) -> None:
    if jsonl:
        for info in elements:
            _emit(
                {
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "phase": "dump_element",
                    **info.to_dict(),
                },
                jsonl=True,
            )
        return

    for info in elements:
        print(info.format_line(), flush=True)


def _print_a11y_banner(delay_sec: float) -> None:
    _log_info("[无障碍] 正在模拟 Windows 无障碍客户端接入（UI Automation / 类似讲述人）")
    _log_info("[无障碍] 已通过 IUIAutomation 挂载并持续遍历微信控件树")
    _log_info("[无障碍] 目的是让微信暴露完整 UI 结构（mmui::MainWindow）")
    _log_info(f"[无障碍] 预热倒计时 {_format_duration(delay_sec)}，结束后将启动微信")
    print(flush=True)


def _warmup_countdown(
    delay_sec: float,
    jsonl: bool,
    on_tick: Callable[[], None] | None = None,
) -> None:
    total = max(float(delay_sec), 0.0)
    deadline = time.monotonic() + total
    jsonl_report_interval = 30.0
    next_jsonl_report = time.monotonic()

    if not jsonl:
        _print_a11y_banner(total)

    progress = _ProgressLine()
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        elapsed = total - remaining
        if on_tick:
            on_tick()

        if jsonl:
            now = time.monotonic()
            if remaining <= 0 or now >= next_jsonl_report:
                _emit(
                    {
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "phase": "a11y_warmup",
                        "remaining_sec": int(remaining),
                        "progress": round(min(elapsed / total, 1.0), 4) if total else 1.0,
                        "message": "simulating_accessibility_client",
                    },
                    jsonl=True,
                )
                next_jsonl_report = now + jsonl_report_interval
        else:
            progress.update(
                f"[无障碍] 模拟接入中 {_render_progress_bar(elapsed, total)} "
                f"剩{_format_duration(remaining)}"
            )

        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))

    if not jsonl:
        progress.finish("[无障碍] 预热完成，准备启动微信。")
    _log("a11y_warmup finished delay=%ss", total)


def _wait_with_progress(
    client: WeChatA11yClient,
    timeout: float,
    poll_interval: float,
    jsonl: bool,
) -> ProbeResult:
    deadline = time.monotonic() + timeout
    last = ProbeResult(visible=False, reason="timeout")
    jsonl_report_interval = 10.0
    next_jsonl_report = time.monotonic()

    progress = _ProgressLine()

    while time.monotonic() < deadline:
        last = client.probe()
        if last.visible:
            if not jsonl:
                progress.finish()
            return last

        elapsed = timeout - max(0.0, deadline - time.monotonic())
        remaining = max(0.0, deadline - time.monotonic())

        if jsonl:
            now = time.monotonic()
            if now >= next_jsonl_report:
                _emit(
                    {
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "phase": "wait_visible",
                        "remaining_sec": int(remaining),
                        "progress": round(min(elapsed / timeout, 1.0), 4) if timeout else 1.0,
                        "class_name": last.class_name,
                        "reason": last.reason,
                    },
                    jsonl=True,
                )
                next_jsonl_report = now + jsonl_report_interval
        else:
            status = last.class_name or last.reason
            progress.update(
                f"[等待] UI暴露中 {_render_progress_bar(elapsed, timeout)} "
                f"剩{_format_duration(remaining)} ({status})"
            )

        time.sleep(poll_interval)

    if not jsonl:
        progress.finish()
    last.reason = "timeout"
    return last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "WeChat 4.x UIA bootstrap: run a11y keepalive, wait, launch WeChat, "
            "then verify UI tree exposure."
        )
    )
    parser.add_argument(
        "--launch-delay",
        type=float,
        default=360.0,
        help="Seconds to keep a11y warm before launching WeChat (default: 360).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Keepalive probe interval in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=4,
        help="UI tree walk depth per probe (default: 4).",
    )
    parser.add_argument(
        "--dump-depth",
        type=int,
        default=1,
        help="Control tree dump depth after success (default: 1).",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for UI tree after launch (default: 300).",
    )
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="Skip launching WeChat (assume it is already running or will be started manually).",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print compact JSON objects, one per line.",
    )
    parser.add_argument(
        "--once-probe",
        action="store_true",
        help="Run a single probe and exit (debug).",
    )
    args = parser.parse_args(argv)
    _ensure_utf8_console()

    client = WeChatA11yClient(interval=args.interval, tree_depth=args.depth)

    if args.once_probe:
        result = client.probe()
        _print_probe(result, args.jsonl, phase="once_probe")
        client.stop()
        return 0 if result.visible else 1

    _log_phase(
        "bootstrap_start",
        args.jsonl,
        launch_delay_sec=args.launch_delay,
        interval=args.interval,
        depth=args.depth,
    )

    client.start(background=True)
    try:
        _warmup_countdown(args.launch_delay, args.jsonl)

        if args.skip_launch:
            _log_phase("launch_skipped", args.jsonl, already_running=is_weixin_running())
        else:
            _log_phase("launch_weixin", args.jsonl, already_running=is_weixin_running())
            try:
                launched = launch_weixin()
            except FileNotFoundError as exc:
                _log_phase("launch_failed", args.jsonl, error=str(exc))
                return 1
            _log_phase(
                "launch_done",
                args.jsonl,
                launched=launched,
                already_running=not launched,
            )

        _log_phase("wait_visible", args.jsonl, timeout_sec=args.wait_timeout)
        result = _wait_with_progress(
            client,
            timeout=args.wait_timeout,
            poll_interval=args.interval,
            jsonl=args.jsonl,
        )
        _print_probe(result, args.jsonl, phase="wait_result")

        if not result.visible:
            return 1

        elements = client.dump(max_depth=args.dump_depth)
        if not elements:
            _log_phase("dump_empty", args.jsonl)
            return 1

        _log_phase("dump_start", args.jsonl, count=len(elements))
        _print_dump(elements, args.jsonl)
        _log_phase("bootstrap_done", args.jsonl, visible=True, controls=len(elements))
        return 0
    finally:
        client.stop()
        _log_phase("bootstrap_stopped", args.jsonl)


if __name__ == "__main__":
    sys.exit(main())
