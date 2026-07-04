#!/usr/bin/env python
"""CLI: simulate a UIA accessibility client for WeChat (Narrator-like)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from a11y_client import ProbeResult, WeChatA11yClient

try:
    from tool_log import get_logger as _tool_logger
except ImportError:
    _tool_logger = None


def _log(message: str, *args: object) -> None:
    if _tool_logger is None:
        return
    try:
        _tool_logger().info(message, *args)
    except Exception:
        pass


def _print_status_human(result: ProbeResult) -> None:
    status = "可见" if result.visible else "不可见"
    print(
        f"[探测] {status} | class={result.class_name or '-'} | "
        f"reason={result.reason} | children={result.children_count}",
        flush=True,
    )
    if result.error:
        print(f"[探测] 错误: {result.error}", flush=True)


def _print_status(result: ProbeResult, jsonl: bool) -> None:
    if jsonl:
        payload = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **result.to_dict()}
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        _print_status_human(result)


def probe_weixin_once(*, log_result: bool = True) -> ProbeResult:
    """Probe WeChat UI once without printing to stdout."""
    client = WeChatA11yClient()
    try:
        result = client.probe()
        if log_result:
            _log(
                "probe once visible=%s class=%s hwnd=%s reason=%s",
                result.visible,
                result.class_name,
                result.hwnd,
                result.reason,
            )
        return result
    finally:
        client.stop()


def _print_dump(client: WeChatA11yClient, max_depth: int) -> int:
    try:
        elements = client.dump(max_depth=max_depth)
    except RuntimeError as exc:
        print(f"[dump] {exc}", flush=True)
        return 1

    if not elements:
        print("[dump] no controls found (tree may still be hidden)", flush=True)
        return 1

    print(f"[dump] {len(elements)} control(s):", flush=True)
    for info in elements:
        print(info.format_line(), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate a Windows accessibility client for WeChat via IUIAutomation "
            "(attach + tree walk, no Narrator UI)."
        )
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Tree walk interval in seconds for keepalive mode (default: 2.0).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=4,
        help="UI tree walk depth per cycle (default: 4).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Attach once, probe exposure, and exit.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Attach and print control tree (blog-style first-level dump).",
    )
    parser.add_argument(
        "--dump-depth",
        type=int,
        default=1,
        help="Recursion depth for --dump (default: 1 = direct children only).",
    )
    parser.add_argument(
        "--wait",
        type=float,
        metavar="SECONDS",
        help="Block until UI tree is visible or timeout.",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print one compact JSON object per line.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print probe result to stdout (log file only).",
    )
    parser.add_argument(
        "--raw-view",
        action="store_true",
        help="Use RawViewWalker instead of ControlViewWalker.",
    )
    args = parser.parse_args(argv)

    client = WeChatA11yClient(
        interval=args.interval,
        tree_depth=args.depth,
        use_control_view=not args.raw_view,
    )

    if args.once:
        result = client.probe()
        if not args.quiet:
            _print_status(result, args.jsonl)
        _log(
            "probe once visible=%s class=%s hwnd=%s reason=%s",
            result.visible,
            result.class_name,
            result.hwnd,
            result.reason,
        )
        client.stop()
        return 0 if result.visible else 1

    if args.dump:
        code = _print_dump(client, max_depth=args.dump_depth)
        _log("dump depth=%s exit=%s", args.dump_depth, code)
        client.stop()
        return code

    if args.wait is not None:
        result = client.wait_until_visible(timeout=args.wait, poll_interval=args.interval)
        _print_status(result, args.jsonl)
        client.stop()
        return 0 if result.visible else 1

    def on_status(result: ProbeResult) -> None:
        _print_status(result, args.jsonl)

    client.on_status = on_status
    print("[a11y] keepalive started — Ctrl+C to stop", flush=True)
    try:
        client.start(background=False)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
        print("[a11y] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
