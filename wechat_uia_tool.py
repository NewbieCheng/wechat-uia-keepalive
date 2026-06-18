#!/usr/bin/env python
"""Unified WeChat 4.x UIA tool: bootstrap, probe, dump, keepalive."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import bootstrap_a11y
import start_a11y
from a11y_client import collect_env_info
from tool_log import get_logger, log_dir, log_file_path, setup_logging

TOOL_NAME = "微信 UIA 前置工具"
TOOL_VERSION = "1.1.0"
AUTHOR = "ChaseZ"
PANEL_WIDTH = 58


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


def _clip(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _panel_line(label: str, value: str) -> str:
    label_part = f"  {label:<10}"
    value_width = PANEL_WIDTH - len(label_part) - 1
    return f"{label_part} {_clip(value, value_width)}"


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


def _format_weixin_version(env: dict[str, Any]) -> str:
    reg = env.get("registry_version") or ""
    file_ver = env.get("file_version") or ""
    if reg and file_ver and reg != file_ver:
        return f"{reg} (文件 {file_ver})"
    return reg or file_ver or "未知"


def _print_banner(env: dict[str, Any] | None = None) -> None:
    env = env or collect_env_info(probe_ui=False)
    log_path = log_file_path()
    top = "╔" + "═" * PANEL_WIDTH + "╗"
    mid = "╠" + "═" * PANEL_WIDTH + "╣"
    bot = "╚" + "═" * PANEL_WIDTH + "╝"

    print(top)
    title = f"  {TOOL_NAME}  v{TOOL_VERSION}"
    print(f"║{title:<{PANEL_WIDTH}}║")
    print(f"║{'':<{PANEL_WIDTH}}║")
    print(f"║{('  ' + AUTHOR + ' 制作'):<{PANEL_WIDTH}}║")
    print(mid)
    print(f"║{_panel_line('微信版本', _format_weixin_version(env)):<{PANEL_WIDTH}}║")
    print(f"║{_panel_line('安装路径', env.get('exe_path') or '未安装'):<{PANEL_WIDTH}}║")
    running = "运行中" if env.get("running") else "未运行"
    print(f"║{_panel_line('进程状态', running):<{PANEL_WIDTH}}║")
    print(f"║{_panel_line('UI 树状态', _format_ui_status(env)):<{PANEL_WIDTH}}║")
    print(f"║{_panel_line('日志文件', str(log_path)):<{PANEL_WIDTH}}║")
    print(bot)
    print()


def _print_menu() -> None:
    print("功能菜单")
    print("  1. 前置引导（默认 6 分钟后启动微信）")
    print("  2. 快速探测 UI 是否可见")
    print("  3. 导出控件树（调查用）")
    print("  4. 持续 Keepalive（Ctrl+C 停止）")
    print("  5. 前置引导（自定义预热秒数）")
    print("  6. 刷新状态 / 环境信息")
    print("  7. 打开日志目录")
    print("  8. 导出诊断报告")
    print("  0. 退出")
    print()


def _read_choice(prompt: str, default: str) -> str | None:
    try:
        return input(prompt).strip() or default
    except (EOFError, KeyboardInterrupt):
        return None


def _log_env(env: dict[str, Any]) -> None:
    log = get_logger()
    log.info(
        "环境 | 版本=%s | 运行=%s | UI=%s | class=%s | reason=%s",
        _format_weixin_version(env),
        env.get("running"),
        env.get("ui_visible"),
        env.get("ui_class"),
        env.get("ui_reason"),
    )


def _refresh_status(*, probe_ui: bool = True) -> dict[str, Any]:
    env = collect_env_info(probe_ui=probe_ui)
    _print_banner(env)
    _log_env(env)
    return env


def _open_log_directory() -> int:
    path = log_dir()
    get_logger().info("打开日志目录: %s", path)
    try:
        os.startfile(path)
    except Exception as exc:
        print(f"[错误] 无法打开日志目录: {exc}")
        return 1
    print(f"[完成] 已打开日志目录: {path}")
    return 0


def _export_diagnostic_report(env: dict[str, Any] | None = None) -> int:
    env = env or collect_env_info(probe_ui=True)
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
    print(f"[完成] 诊断报告已保存: {report_path}")
    return 0


def _dispatch_choice(choice: str, env: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    log = get_logger()
    log.info("用户选择菜单项: %s", choice)

    if choice == "1":
        code = bootstrap_a11y.main([])
        env = collect_env_info(probe_ui=True)
        return code, env
    if choice == "2":
        code = start_a11y.main(["--once"])
        env = collect_env_info(probe_ui=True)
        return code, env
    if choice == "3":
        depth = _read_choice("导出深度 [1]: ", "1") or "1"
        code = start_a11y.main(["--dump", "--dump-depth", depth])
        env = collect_env_info(probe_ui=True)
        return code, env
    if choice == "4":
        print("按 Ctrl+C 可停止 Keepalive。")
        code = start_a11y.main([])
        env = collect_env_info(probe_ui=True)
        return code, env
    if choice == "5":
        delay = _read_choice("预热秒数 [360]: ", "360") or "360"
        code = bootstrap_a11y.main(["--launch-delay", delay])
        env = collect_env_info(probe_ui=True)
        return code, env
    if choice == "6":
        env = _refresh_status(probe_ui=True)
        return 0, env
    if choice == "7":
        return _open_log_directory(), env
    if choice == "8":
        return _export_diagnostic_report(env), env

    print(f"无效选项: {choice!r}，请重新输入。")
    return 1, env


def _interactive_loop(
    *,
    bootstrap_first: bool = False,
    bootstrap_argv: list[str] | None = None,
) -> int:
    _ensure_utf8_console()
    setup_logging()
    log = get_logger()
    log.info("启动 %s v%s | %s 制作", TOOL_NAME, TOOL_VERSION, AUTHOR)

    env = _refresh_status(probe_ui=True)
    last_code = 0
    menu_default = "1"

    if bootstrap_first:
        log.info("执行引导流程 (CLI bootstrap)")
        last_code = bootstrap_a11y.main(bootstrap_argv or [])
        env = collect_env_info(probe_ui=True)
        _print_banner(env)
        print("\n[提示] 引导完成，可继续输入序号检查，输入 0 退出。\n")
        menu_default = "2"

    while True:
        _print_menu()
        choice = _read_choice(f"输入序号 [{menu_default}]: ", menu_default)
        if choice is None:
            log.info("用户中断退出")
            print("\n已退出。")
            return last_code
        if choice == "0":
            log.info("用户选择退出")
            print("已退出。")
            return last_code

        last_code, env = _dispatch_choice(choice, env)
        if choice not in {"6", "7"}:
            _print_banner(env)
        if choice in {"1", "5"}:
            print("\n[提示] 引导完成，可继续输入序号检查，输入 0 退出。\n")
        elif choice not in {"6", "7", "8"}:
            print("\n[提示] 操作完成，可继续输入序号检查，输入 0 退出。\n")
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

    probe = sub.add_parser("probe", help="Probe WeChat UI visibility once.", parents=[common])
    probe.add_argument("--jsonl", action="store_true")
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
