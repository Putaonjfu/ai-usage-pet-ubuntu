#!/usr/bin/env python3
"""Detach the GUI from its caller and keep one instance per user."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

APP_DIR = Path(__file__).resolve().parent


def launch() -> int:
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ai-usage-pet'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The GUI inherits this open file description, so the lock outlives us.
    with (cache / 'instance.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('用量小猫已在后台运行，可从系统托盘打开。')
            return 0
        log_path = cache / 'app.log'
        # Bound the diagnostic log across launches.
        if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
            log_path.replace(cache / 'app.previous.log')
        with log_path.open('a', encoding='utf-8') as log:
            child = subprocess.Popen(
                [sys.executable, str(APP_DIR / 'main.py'), *sys.argv[1:]],
                cwd=APP_DIR,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                pass_fds=(lock.fileno(),),
            )
        time.sleep(0.3)
        if child.poll() is not None:
            print(f'启动失败，请查看日志：{log_path}', file=sys.stderr)
            return child.returncode or 1
        print(f'用量小猫已在后台启动（PID {child.pid}），可以关闭终端。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(launch())
    except OSError as exc:
        print(f'无法启动用量小猫：{exc}', file=sys.stderr)
        raise SystemExit(1)
