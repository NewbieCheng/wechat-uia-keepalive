"""File + console logging for WeChat UIA tool troubleshooting."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_LOG_DIR: Path | None = None
_LOG_FILE: Path | None = None
_CONFIGURED = False


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def log_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = app_dir() / "logs"
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def log_file_path() -> Path:
    global _LOG_FILE
    if _LOG_FILE is None:
        stamp = datetime.now().strftime("%Y%m%d")
        _LOG_FILE = log_dir() / f"WeChatUIA-{stamp}.log"
    return _LOG_FILE


def setup_logging() -> Path:
    global _CONFIGURED
    path = log_file_path()
    if _CONFIGURED:
        return path

    root = logging.getLogger("wechat_uia")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[日志] %(message)s"))
    root.addHandler(console)

    _CONFIGURED = True
    root.info("日志系统已启动: %s", path)
    return path


def get_logger() -> logging.Logger:
    setup_logging()
    return logging.getLogger("wechat_uia")
