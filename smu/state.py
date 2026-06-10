"""状态与凭据存储：~/.self-media-uper/"""

from __future__ import annotations

import json
import os
from pathlib import Path

SMU_HOME = Path(os.environ.get("SMU_HOME") or Path.home() / ".self-media-uper")
STATE_FILE = SMU_HOME / "state.json"


def load_state() -> dict:
    if STATE_FILE.is_file():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    SMU_HOME.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def platform_state(state: dict, platform: str) -> dict:
    """返回平台子状态（published / topics 等），不存在则就地创建。"""
    p = state.setdefault("platforms", {}).setdefault(platform, {})
    p.setdefault("published", {})
    return p
