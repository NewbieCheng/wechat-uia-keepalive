#!/usr/bin/env python
"""CLI: keep WeChat UIA tree exposed without launching Narrator."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime

from uia_client import ProbeResult, WeChatUIAClient


def _print_status(result: ProbeResult, jsonl: bool) -> None:
    payload = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **result.to_dict()}
    if jsonl:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def start_narrator(duration: float) -> None:
    print(f"[narrator] launching narrator.exe for {duration:.0f}s ...", flush=True)
    subprocess.Popen(["narrator.exe"])
    time.sleep(duration)
    subprocess.run(["taskkill", "/IM", "Narrator.exe", "/F"], check=False)
    print("[narrator] stopped", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lightweight UIA client to expose WeChat UI tree (no Narrator)."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Probe interval in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=4,
        help="UI tree walk depth per probe (default: 4).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single probe and exit.",
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
        "--narrator-fallback",
        type=float,
        metavar="SECONDS",
        help="If UI stays hidden, launch narrator.exe for SECONDS then retry.",
    )
    args = parser.parse_args()

    client = WeChatUIAClient(interval=args.interval, tree_depth=args.depth)

    if args.once:
        _print_status(client.probe(), args.jsonl)
        return 0

    if args.wait is not None:
        result = client.wait_until_visible(timeout=args.wait, poll_interval=args.interval)
        if not result.visible and args.narrator_fallback:
            start_narrator(args.narrator_fallback)
            result = client.wait_until_visible(timeout=args.wait, poll_interval=args.interval)
        _print_status(result, args.jsonl)
        return 0 if result.visible else 1

    def on_status(result: ProbeResult) -> None:
        _print_status(result, args.jsonl)

    client.on_status = on_status
    print("[keepalive] started — Ctrl+C to stop", flush=True)
    try:
        client.start(background=False)
    except KeyboardInterrupt:
        client.stop()
        print("[keepalive] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
