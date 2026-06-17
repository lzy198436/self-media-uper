"""状态与凭据存储：~/.self-media-uper/

并发安全：load_state→改→save_state 三步非原子，多个 smu 进程并发跑(不同平台)会互相
覆盖——慢进程持着旧快照 save，把快进程累积的写入抹掉(实测 B站 20 条只剩 2 条)。
凡是要"读改写"state 的地方都走 atomic_update：文件锁内「reload 最新→mutate→save」，
每个进程只把自己这条变更合并进磁盘最新态，不再互相覆盖。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

SMU_HOME = Path(os.environ.get("SMU_HOME") or Path.home() / ".self-media-uper")
STATE_FILE = SMU_HOME / "state.json"
LOCK_FILE = SMU_HOME / "state.lock"


def load_state() -> dict:
    if STATE_FILE.is_file():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    SMU_HOME.mkdir(parents=True, exist_ok=True)
    # 写临时文件再原子 rename，避免并发/崩溃下读到半截 JSON
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


@contextmanager
def _state_lock():
    """跨进程互斥锁(fcntl flock)。锁文件独立于 state.json，避免和 rename 打架。
    非 POSIX(无 fcntl)时退化为无锁——单进程仍正确，仅失去并发保护。"""
    SMU_HOME.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        yield
        return
    f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def atomic_update(mutate: Callable[[dict], None]) -> dict:
    """并发安全地更新 state：文件锁内「读磁盘最新→mutate(state)→写回」，返回写回后的 state。
    mutate 只应改自己负责的那几条(某平台的 published[name]/topics)，
    这样多进程并发时各自的变更都基于磁盘最新态合并，不会互相覆盖。"""
    with _state_lock():
        state = load_state()
        mutate(state)
        save_state(state)
        return state


def platform_state(state: dict, platform: str) -> dict:
    """返回平台子状态（published / topics 等），不存在则就地创建。"""
    p = state.setdefault("platforms", {}).setdefault(platform, {})
    p.setdefault("published", {})
    return p


def handout_state(state: dict, platform: str = "xiaohongshu") -> dict:
    """返回平台下的图文讲义发布子状态 handout_published（与视频 published 并列、互不干扰）。

    讲义图文笔记和视频是两种发布物，各记各的：发了视频不代表发了讲义，反之亦然。
    """
    p = state.setdefault("platforms", {}).setdefault(platform, {})
    return p.setdefault("handout_published", {})
