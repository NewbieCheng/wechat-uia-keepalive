#!/usr/bin/env python
"""Unified WeChat 4.x UIA tool: bootstrap, probe, dump, keepalive."""

from __future__ import annotations

import argparse
import ctypes
import math
import os
import sys
import time
from datetime import datetime
from typing import Any, Sequence

import bootstrap_a11y
import start_a11y
from a11y_client import (
    collect_env_info,
    format_render_preset_label,
    format_warmup_preset_label,
    get_render_mode,
    get_render_preset,
    get_warmup_delay_seconds,
    get_warmup_preset,
    next_render_preset,
    next_warmup_preset,
    set_render_preset,
    set_warmup_preset,
)
from tool_log import get_logger, log_dir, log_file_path, setup_logging

TOOL_NAME = "微信 UIA 前置工具"
TOOL_VERSION = "1.3.2"
AUTHOR = "ChaseZ"
CONTENT_WIDTH = 72
LABEL_WIDTH = 12
HISTORY_LINES = 8

_CHASEZ_BANNER = [
    "  ██████╗██╗  ██╗ █████╗ ███████╗███████╗███████╗",
    " ██╔════╝██║  ██║██╔══██╗██╔════╝██╔════╝╚══███╔╝",
    " ██║     ███████║███████║███████╗█████╗    ███╔╝ ",
    " ██║     ██╔══██║██╔══██║╚════██║██╔══╝   ███╔╝ ",
    " ╚██████╗██║  ██║██║  ██║███████║███████╗███████╗",
    "  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝",
]

_ENABLE_VT_PROCESSING = 0x0004


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


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


def _char_display_width(ch: str) -> int:
    code = ord(ch)
    if code <= 0x1F:
        return 0
    if code >= 0x1100 and (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE10 <= code <= 0xFE19
        or 0xFE30 <= code <= 0xFE6F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    ):
        return 2
    if code > 0xFF:
        return 2
    return 1


def _display_width(text: str) -> int:
    return sum(_char_display_width(ch) for ch in text)


def _clip_display(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _display_width(text) <= max_width:
        return text
    if max_width <= 3:
        out = ""
        w = 0
        for ch in text:
            cw = _char_display_width(ch)
            if w + cw > max_width:
                break
            out += ch
            w += cw
        return out
    limit = max_width - 3
    out = ""
    w = 0
    for ch in text:
        cw = _char_display_width(ch)
        if w + cw > limit:
            break
        out += ch
        w += cw
    return out + "..."


def _pad_display(text: str, width: int) -> str:
    pad = max(0, width - _display_width(text))
    return text + (" " * pad)


def _format_status_line(label: str, value: str) -> str:
    label_part = _pad_display(label, LABEL_WIDTH)
    value_width = CONTENT_WIDTH - LABEL_WIDTH - 2
    clipped = _clip_display(value, value_width)
    return f"  {label_part}  {clipped}"


def _format_ui_status(env: dict[str, Any]) -> str:
    if not env.get("installed"):
        return "未检测到微信安装"
    if not env.get("running"):
        return "微信未运行"
    if env.get("ui_visible"):
        cls = env.get("ui_class") or "mmui::*"
        return f"已暴露 ({cls})"
    reason = env.get("ui_reason") or "unknown"
    cls = env.get("ui_class") or "Qt51514QWindowIcon"
    return f"未暴露 ({cls} / {reason})"


def _format_render_mode(env: dict[str, Any]) -> str:
    mode = env.get("render_mode") or "not_running"
    preset = env.get("render_preset") or get_render_preset()
    preset_label = format_render_preset_label(preset)
    if mode == "cpu_software":
        return f"CPU 软件渲染（{preset_label} / 本次启动）"
    if mode == "unknown_running":
        return "已在运行（需重启才生效）"
    return f"CPU 软件渲染（{preset_label} / 待启动）"


def _format_readiness(env: dict[str, Any]) -> str:
    readiness = env.get("readiness") or {}
    return readiness.get("summary") or "未检查"


def _bootstrap_argv_with_preset(extra: list[str] | None = None) -> list[str]:
    argv = list(extra or [])
    preset = get_render_preset()
    if not any(arg == "--render-preset" for arg in argv):
        argv.extend(["--render-preset", preset])
    if not any(arg == "--launch-delay" for arg in argv):
        argv.extend(["--launch-delay", str(get_warmup_delay_seconds())])
    return argv


def _bootstrap_history_message(code: int, env: dict[str, Any]) -> str:
    preset = env.get("render_preset") or get_render_preset()
    warmup = env.get("warmup_preset") or get_warmup_preset()
    preset_label = format_render_preset_label(preset)
    warmup_label = format_warmup_preset_label(warmup)
    if code == 0:
        return f"引导 → 前置引导完成（{warmup_label} / {preset_label}），UI 已暴露"
    if env.get("render_mode") == "unknown_running":
        return f"引导 → 微信已在运行，未切换 CPU 渲染（{preset_label}）"
    if not env.get("ui_visible"):
        if warmup != "maximum":
            suggested_warmup = format_warmup_preset_label(next_warmup_preset(warmup))
            return (
                f"引导 → 未暴露（{warmup_label} / {preset_label}），请关闭微信后"
                f"用菜单 10 加长预热至「{suggested_warmup}」重试"
            )
        suggested = format_render_preset_label(next_render_preset(preset))
        return (
            f"引导 → 最长预热仍失败（{preset_label}），请关闭微信后"
            f"用菜单 9 切换到「{suggested}」重试"
        )
    return f"引导 → 前置引导结束（{warmup_label} / {preset_label}）"


def _format_weixin_version(env: dict[str, Any]) -> str:
    reg = env.get("registry_version") or ""
    file_ver = env.get("file_version") or ""
    if reg and file_ver and reg != file_ver:
        return f"{reg} (文件 {file_ver})"
    return reg or file_ver or "未知"


def _format_probe_history(env: dict[str, Any]) -> str:
    if not env.get("running"):
        return "探测 → 微信未运行"
    if env.get("ui_visible"):
        cls = env.get("ui_class") or "mmui::*"
        return f"探测 → UI 已暴露 ({cls})"
    cls = env.get("ui_class") or "-"
    reason = env.get("ui_reason") or "unknown"
    return f"探测 → UI 未暴露 ({cls} / {reason})"


def _banner_lines(env: dict[str, Any]) -> list[str]:
    log_path = log_file_path()
    running = "运行中" if env.get("running") else "未运行"
    divider = "  " + ("─" * CONTENT_WIDTH)

    lines = [
        *_CHASEZ_BANNER,
        f"  {TOOL_NAME} v{TOOL_VERSION} · {AUTHOR}",
        divider,
        _format_status_line("微信版本", _format_weixin_version(env)),
        _format_status_line("安装路径", env.get("exe_path") or "未安装"),
        _format_status_line("进程状态", running),
        _format_status_line("UI 树状态", _format_ui_status(env)),
        _format_status_line("渲染模式", _format_render_mode(env)),
        _format_status_line(
            "渲染预设",
            format_render_preset_label(env.get("render_preset")),
        ),
        _format_status_line(
            "预热档位",
            format_warmup_preset_label(env.get("warmup_preset")),
        ),
        _format_status_line("就绪检查", _format_readiness(env)),
        _format_status_line("日志文件", str(log_path)),
        divider,
    ]
    return lines


def _menu_lines(env: dict[str, Any] | None = None) -> list[str]:
    warmup_hint = format_warmup_preset_label(
        (env or {}).get("warmup_preset") or get_warmup_preset()
    )
    return [
        "功能菜单",
        f"  1. 前置引导（当前预热：{warmup_hint}）",
        "  2. 快速探测 UI 是否可见",
        "  3. 导出控件树（调查用）",
        "  4. 持续 Keepalive（Ctrl+C 停止）",
        "  5. 前置引导（自定义预热秒数，任意时长）",
        "  6. 刷新状态 / 环境信息",
        "  7. 打开日志目录",
        "  8. 导出诊断报告",
        "  9. 切换渲染预设并重试引导",
        " 10. 切换预热档位并重试引导",
        "  0. 退出",
    ]


class SessionView:
    """Fixed panel + paginated history for customer-facing console UI."""

    def __init__(self) -> None:
        self.history: list[str] = []
        self.history_page = 0
        self.status_message = ""
        self._last_frame = ""
        self._cleared_once = False

    def append(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.history.append(f"  {stamp}  {message}")
        self.history_page = 0
        self.invalidate()

    def set_status(self, message: str) -> None:
        self.status_message = message
        self.invalidate()

    def _max_page(self) -> int:
        if not self.history:
            return 0
        pages = math.ceil(len(self.history) / HISTORY_LINES)
        return max(0, pages - 1)

    def page_up(self) -> None:
        self.history_page = min(self.history_page + 1, self._max_page())
        self.invalidate()

    def page_down(self) -> None:
        self.history_page = max(self.history_page - 1, 0)
        self.invalidate()

    def _history_lines(self) -> list[str]:
        total = len(self.history)
        max_page = self._max_page()
        page_num = max_page - self.history_page + 1 if total else 1
        page_count = max_page + 1 if total else 1
        header = (
            f"[操作历史]  ↑↓ 或 PgUp/PgDn 翻页 | 共 {total} 条 | "
            f"第 {page_num}/{page_count} 页"
        )

        if not self.history:
            return [header, "  （暂无记录）"]

        reversed_hist = list(reversed(self.history))
        start = self.history_page * HISTORY_LINES
        chunk = reversed_hist[start : start + HISTORY_LINES]
        chunk.reverse()
        return [header, *chunk]

    def build_frame(
        self,
        env: dict[str, Any],
        *,
        prompt: str = "",
        extra_lines: list[str] | None = None,
    ) -> str:
        output: list[str] = []
        output.extend(_banner_lines(env))
        output.append("")
        output.extend(self._history_lines())
        if self.status_message:
            output.append("")
            output.append(self.status_message)
        if extra_lines:
            output.append("")
            output.extend(extra_lines)
        output.append("")
        output.extend(_menu_lines(env))
        if prompt:
            output.append("")
            output.append(prompt)
        return "\n".join(output) + "\n"

    def render(
        self,
        env: dict[str, Any],
        *,
        prompt: str = "",
        extra_lines: list[str] | None = None,
        force: bool = False,
    ) -> None:
        frame = self.build_frame(env, prompt=prompt, extra_lines=extra_lines)
        if not force and frame == self._last_frame:
            return

        prefix = ""
        if not self._cleared_once:
            prefix = "\033[2J\033[H"
            self._cleared_once = True
        else:
            prefix = "\033[H"

        sys.stdout.write(prefix + frame)
        sys.stdout.flush()
        self._last_frame = frame

    def invalidate(self) -> None:
        self._last_frame = ""


def _log_env(env: dict[str, Any]) -> None:
    log = get_logger()
    log.info(
        "环境 | 版本=%s | 运行=%s | 渲染=%s | UI=%s | class=%s | reason=%s",
        _format_weixin_version(env),
        env.get("running"),
        env.get("render_mode"),
        env.get("ui_visible"),
        env.get("ui_class"),
        env.get("ui_reason"),
    )


def _probe_env() -> dict[str, Any]:
    result = start_a11y.probe_weixin_once()
    env = collect_env_info(probe_ui=False)
    env["ui_visible"] = result.visible
    env["ui_class"] = result.class_name
    env["ui_reason"] = result.reason
    env["window_name"] = result.window_name
    env["hwnd"] = result.hwnd
    env["children_count"] = result.children_count
    env["nodes_touched"] = result.nodes_touched
    if result.error:
        env["error"] = result.error
    env["render_mode"] = get_render_mode()
    return env


def _read_line_prompt(session: SessionView, env: dict[str, Any], prompt: str) -> str | None:
    if sys.platform != "win32":
        try:
            session.render(env, prompt=prompt, force=True)
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return None

    import msvcrt

    buffer = ""
    session.render(env, prompt=f"{prompt}{buffer}", force=True)

    while True:
        if not msvcrt.kbhit():
            time.sleep(0.05)
            continue

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            special = msvcrt.getwch()
            if special in ("H", "I"):
                session.page_up()
                session.render(env, prompt=f"{prompt}{buffer}", force=True)
            elif special in ("P", "Q"):
                session.page_down()
                session.render(env, prompt=f"{prompt}{buffer}", force=True)
            continue
        if ch == "\r":
            return buffer
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x08":
            if buffer:
                buffer = buffer[:-1]
                session.render(env, prompt=f"{prompt}{buffer}", force=True)
            continue
        if ch == "\x1b":
            return None
        if ch.isprintable():
            buffer += ch
            session.render(env, prompt=f"{prompt}{buffer}", force=True)


def _read_choice(
    session: SessionView,
    env: dict[str, Any],
    prompt: str,
    default: str,
) -> str | None:
    try:
        value = _read_line_prompt(session, env, prompt)
        if value is None:
            return None
        return value.strip() or default
    except (EOFError, KeyboardInterrupt):
        return None


def _open_log_directory(session: SessionView, env: dict[str, Any]) -> int:
    path = log_dir()
    get_logger().info("打开日志目录: %s", path)
    try:
        os.startfile(path)
    except Exception as exc:
        session.append(f"打开日志目录失败: {exc}")
        session.set_status(f"[错误] 无法打开日志目录: {exc}")
        return 1
    session.append(f"已打开日志目录: {path}")
    session.set_status(f"[完成] 已打开日志目录: {path}")
    return 0


def _export_diagnostic_report(env: dict[str, Any]) -> tuple[int, str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = log_dir() / f"diagnostic_{stamp}.txt"
    lines = [
        f"{TOOL_NAME} v{TOOL_VERSION} | {AUTHOR} 制作",
        f"生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "[微信环境]",
        f"  注册表版本: {env.get('registry_version') or '-'}",
        f"  文件版本:   {env.get('file_version') or '-'}",
        f"  安装路径:   {env.get('exe_path') or '-'}",
        f"  已安装:     {env.get('installed')}",
        f"  运行中:     {env.get('running')}",
        f"  渲染预设:   {format_render_preset_label(env.get('render_preset'))}",
        f"  预热档位:   {format_warmup_preset_label(env.get('warmup_preset'))}",
        f"  就绪检查:   {_format_readiness(env)}",
        "",
        "[UI Automation]",
        f"  UI 可见:    {env.get('ui_visible')}",
        f"  窗口类名:   {env.get('ui_class') or '-'}",
        f"  窗口标题:   {env.get('window_name') or '-'}",
        f"  HWND:       {env.get('hwnd') or 0}",
        f"  子控件数:   {env.get('children_count') or 0}",
        f"  遍历节点:   {env.get('nodes_touched') or 0}",
        f"  探测原因:   {env.get('ui_reason') or '-'}",
    ]
    if env.get("error"):
        lines.extend(["", "[错误]", f"  {env['error']}"])
    lines.extend(["", "[日志文件]", f"  {log_file_path()}"])

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    get_logger().info("诊断报告已导出: %s", report_path)
    return 0, str(report_path)


def _dispatch_choice(
    choice: str,
    env: dict[str, Any],
    session: SessionView,
) -> tuple[int, dict[str, Any], str]:
    log = get_logger()
    log.info("用户选择菜单项: %s", choice)
    session.set_status("")

    if choice == "1":
        code = bootstrap_a11y.main(_bootstrap_argv_with_preset())
        env = collect_env_info(probe_ui=True)
        return code, env, _bootstrap_history_message(code, env)

    if choice == "2":
        env = _probe_env()
        _log_env(env)
        return (0 if env.get("ui_visible") else 1), env, _format_probe_history(env)

    if choice == "3":
        depth = _read_choice(session, env, "导出深度 [1]: ", "1") or "1"
        code = start_a11y.main(["--dump", "--dump-depth", depth])
        env = collect_env_info(probe_ui=True)
        msg = "导出 → 控件树已输出到上方控制台"
        return code, env, msg

    if choice == "4":
        session.render(
            env,
            extra_lines=["按 Ctrl+C 可停止 Keepalive。"],
            force=True,
        )
        code = start_a11y.main([])
        env = collect_env_info(probe_ui=True)
        msg = "Keepalive → 已停止"
        return code, env, msg

    if choice == "5":
        default_delay = str(int(get_warmup_delay_seconds()))
        delay = (
            _read_choice(session, env, f"预热秒数 [{default_delay}]: ", default_delay)
            or default_delay
        )
        code = bootstrap_a11y.main(
            _bootstrap_argv_with_preset(["--launch-delay", delay])
        )
        env = collect_env_info(probe_ui=True)
        if code == 0:
            msg = (
                f"引导 → 自定义预热 {delay} 秒完成"
                f"（{format_render_preset_label()})"
            )
        else:
            msg = _bootstrap_history_message(code, env)
        return code, env, msg

    if choice == "6":
        env = collect_env_info(probe_ui=True)
        _log_env(env)
        readiness = env.get("readiness") or {}
        issues = readiness.get("issues") or []
        if issues:
            return 0, env, f"刷新 → 就绪检查 {readiness.get('summary')}：{'；'.join(issues)}"
        return 0, env, "刷新 → 状态已更新，就绪检查通过"

    if choice == "7":
        code = _open_log_directory(session, env)
        return code, env, session.history[-1].strip() if session.history else ""

    if choice == "8":
        code, report_path = _export_diagnostic_report(env)
        msg = f"诊断 → 报告已保存: {report_path}"
        session.set_status(f"[完成] 诊断报告已保存: {report_path}")
        return code, env, msg

    if choice == "9":
        current = get_render_preset()
        new_preset = next_render_preset(current)
        set_render_preset(new_preset)
        env = collect_env_info(probe_ui=False)
        env["render_preset"] = new_preset
        readiness = env.get("readiness") or {}
        if readiness.get("weixin_running"):
            session.set_status(
                "[提示] 请先完全退出微信，再按 1、9 或 10 执行引导。"
            )
            return 1, env, (
                f"预设 → 已切换到 {format_render_preset_label(new_preset)}"
                f"（请先退出微信再引导）"
            )
        code = bootstrap_a11y.main(_bootstrap_argv_with_preset())
        env = collect_env_info(probe_ui=True)
        return code, env, _bootstrap_history_message(code, env)

    if choice == "10":
        current = get_warmup_preset()
        new_warmup = next_warmup_preset(current)
        set_warmup_preset(new_warmup)
        env = collect_env_info(probe_ui=False)
        env["warmup_preset"] = new_warmup
        env["warmup_delay_sec"] = int(get_warmup_delay_seconds(new_warmup))
        readiness = env.get("readiness") or {}
        if readiness.get("weixin_running"):
            session.set_status(
                "[提示] 请先完全退出微信，再按 1、9 或 10 执行引导。"
            )
            return 1, env, (
                f"预热 → 已切换到 {format_warmup_preset_label(new_warmup)}"
                f"（请先退出微信再引导）"
            )
        code = bootstrap_a11y.main(_bootstrap_argv_with_preset())
        env = collect_env_info(probe_ui=True)
        return code, env, _bootstrap_history_message(code, env)

    session.set_status(f"无效选项: {choice!r}，请重新输入。")
    return 1, env, f"无效选项: {choice!r}"


def _interactive_loop(
    *,
    bootstrap_first: bool = False,
    bootstrap_argv: list[str] | None = None,
) -> int:
    _ensure_utf8_console()
    setup_logging()
    log = get_logger()
    log.info("启动 %s v%s | %s 制作", TOOL_NAME, TOOL_VERSION, AUTHOR)

    session = SessionView()
    env = collect_env_info(probe_ui=True)
    _log_env(env)
    readiness = env.get("readiness") or {}
    session.append("启动 → 工具已就绪")
    if not readiness.get("ready"):
        issues = readiness.get("issues") or []
        if issues:
            session.append(f"就绪 → {readiness.get('summary')}：{issues[0]}")
    last_code = 0
    menu_default = "1"

    if bootstrap_first:
        log.info("执行引导流程 (CLI bootstrap)")
        session.render(
            env,
            extra_lines=["正在执行引导流程，请稍候..."],
            force=True,
        )
        last_code = bootstrap_a11y.main(bootstrap_argv or [])
        env = collect_env_info(probe_ui=True)
        if last_code == 0:
            session.append("引导 → CLI 引导完成，UI 已暴露")
        else:
            session.append("引导 → CLI 引导结束")
        menu_default = "2"

    while True:
        prompt = f"输入序号 [{menu_default}]: "
        choice = _read_choice(session, env, prompt, menu_default)
        if choice is None:
            log.info("用户中断退出")
            session.append("退出 → 用户中断")
            session.render(env, force=True)
            print("已退出。")
            return last_code
        if choice == "0":
            log.info("用户选择退出")
            session.append("退出 → 用户选择退出")
            session.render(env, force=True)
            print("已退出。")
            return last_code

        last_code, env, history_msg = _dispatch_choice(choice, env, session)
        if history_msg and choice != "7":
            session.append(history_msg)
        if choice in {"1", "3", "4", "5", "9", "10"}:
            session._cleared_once = False
            session.invalidate()
        menu_default = "2"


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--no-pause",
        action="store_true",
        help="Reserved for automation; interactive mode never waits for Enter.",
    )

    parser = argparse.ArgumentParser(
        prog="wechat-uia-tool",
        description=f"{TOOL_NAME} v{TOOL_VERSION} | {AUTHOR} 制作",
        parents=[common],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{TOOL_NAME} v{TOOL_VERSION} | {AUTHOR} 制作",
    )

    sub = parser.add_subparsers(dest="command")

    bootstrap = sub.add_parser(
        "bootstrap",
        help="Run a11y warmup, launch WeChat, wait for UI tree, dump controls.",
        parents=[common],
    )
    bootstrap.add_argument("--launch-delay", type=float, default=360.0)
    bootstrap.add_argument("--interval", type=float, default=2.0)
    bootstrap.add_argument("--depth", type=int, default=4)
    bootstrap.add_argument("--dump-depth", type=int, default=1)
    bootstrap.add_argument("--wait-timeout", type=float, default=300.0)
    bootstrap.add_argument("--skip-launch", action="store_true")
    bootstrap.add_argument("--jsonl", action="store_true")
    bootstrap.add_argument("--once-probe", action="store_true")
    bootstrap.add_argument(
        "--render-preset",
        choices=("community", "full_software", "angle_warp"),
        default=None,
    )

    probe = sub.add_parser("probe", help="Probe WeChat UI visibility once.", parents=[common])
    probe.add_argument("--jsonl", action="store_true")
    probe.add_argument("--quiet", action="store_true")
    probe.add_argument("--raw-view", action="store_true")

    dump = sub.add_parser("dump", help="Dump WeChat control tree.", parents=[common])
    dump.add_argument("--dump-depth", type=int, default=1)
    dump.add_argument("--raw-view", action="store_true")

    keep = sub.add_parser(
        "keepalive",
        help="Keep UIA tree exposed until Ctrl+C.",
        parents=[common],
    )
    keep.add_argument("--interval", type=float, default=2.0)
    keep.add_argument("--depth", type=int, default=4)
    keep.add_argument("--jsonl", action="store_true")
    keep.add_argument("--raw-view", action="store_true")

    wait = sub.add_parser(
        "wait",
        help="Wait until UI tree becomes visible.",
        parents=[common],
    )
    wait.add_argument("--timeout", type=float, default=300.0)
    wait.add_argument("--interval", type=float, default=2.0)
    wait.add_argument("--jsonl", action="store_true")
    wait.add_argument("--raw-view", action="store_true")

    sub.add_parser(
        "menu",
        help="Interactive menu (default when exe has no args).",
        parents=[common],
    )
    return parser


def _bootstrap_argv(ns: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if ns.launch_delay != 360.0:
        argv.extend(["--launch-delay", str(ns.launch_delay)])
    if ns.interval != 2.0:
        argv.extend(["--interval", str(ns.interval)])
    if ns.depth != 4:
        argv.extend(["--depth", str(ns.depth)])
    if ns.dump_depth != 1:
        argv.extend(["--dump-depth", str(ns.dump_depth)])
    if ns.wait_timeout != 300.0:
        argv.extend(["--wait-timeout", str(ns.wait_timeout)])
    if ns.skip_launch:
        argv.append("--skip-launch")
    if ns.jsonl:
        argv.append("--jsonl")
    if ns.once_probe:
        argv.append("--once-probe")
    if getattr(ns, "render_preset", None):
        argv.extend(["--render-preset", ns.render_preset])
    return argv


def _a11y_argv(
    ns: argparse.Namespace,
    *,
    once: bool = False,
    dump: bool = False,
    wait: float | None = None,
) -> list[str]:
    argv: list[str] = []
    if once:
        argv.append("--once")
    if dump:
        argv.append("--dump")
        if ns.dump_depth != 1:
            argv.extend(["--dump-depth", str(ns.dump_depth)])
    if wait is not None:
        argv.extend(["--wait", str(wait)])
    if getattr(ns, "interval", 2.0) != 2.0:
        argv.extend(["--interval", str(ns.interval)])
    if getattr(ns, "depth", 4) != 4:
        argv.extend(["--depth", str(ns.depth)])
    if getattr(ns, "jsonl", False):
        argv.append("--jsonl")
    if getattr(ns, "quiet", False):
        argv.append("--quiet")
    if getattr(ns, "raw_view", False):
        argv.append("--raw-view")
    return argv


def _is_interactive_session(args: argparse.Namespace) -> bool:
    if getattr(args, "jsonl", False):
        return False
    if getattr(args, "once_probe", False):
        return False
    if args.command in {None, "menu", "bootstrap"}:
        return True
    return False


def run(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8_console()
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.no_pause:
        os.environ["WECHAT_UIA_NO_PAUSE"] = "1"

    if _is_interactive_session(args):
        if args.command == "bootstrap":
            return _interactive_loop(
                bootstrap_first=True,
                bootstrap_argv=_bootstrap_argv(args),
            )
        return _interactive_loop()

    setup_logging()
    if args.command == "bootstrap":
        return bootstrap_a11y.main(_bootstrap_argv(args))
    if args.command == "probe":
        return start_a11y.main(_a11y_argv(args, once=True))
    if args.command == "dump":
        return start_a11y.main(_a11y_argv(args, dump=True))
    if args.command == "keepalive":
        return start_a11y.main(_a11y_argv(args))
    if args.command == "wait":
        return start_a11y.main(_a11y_argv(args, wait=args.timeout))

    parser.print_help()
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
