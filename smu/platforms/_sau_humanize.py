"""sau 拟人化运行时补丁（猴子补丁，不改 social-auto-upload 源码）。

在 sau 进程启动时 import 本模块即可生效——它补的是 sau 底层的 patchright，
不碰 sau 任何代码，所以 sau 升级不受影响。

两处补丁：
  1. BrowserType.launch / launch_persistent_context → 注入随机 slow_mo
     （每个浏览器动作前加 N 毫秒延迟，N 每次运行随机），让微操作不再机械等距。
  2. Page.wait_for_timeout(ms) → 把固定等待改成 ms*factor + 随机抖动，
     让步骤之间的停顿像人一样长短不一。

通过环境变量调参（毫秒/倍数）：
  SMU_SLOWMO_MIN / SMU_SLOWMO_MAX   每次运行的 slow_mo 取值区间，默认 0（关闭）
                                    —— slow_mo 会给每个微操作加延迟，可能搅乱动态 UI
                                       （下拉框/日期选择器），默认不开；需要时再调大。
  SMU_WAIT_FACTOR_MIN / _MAX        wait_for_timeout 的乘数区间，默认 1.2~2.2（主要拟人手段）
  SMU_WAIT_JITTER_MS                额外随机抖动上限（毫秒），默认 1200
关闭全部：SMU_HUMANIZE=0
"""

from __future__ import annotations

import os
import random
import sys


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def install() -> None:
    if os.environ.get("SMU_HUMANIZE", "1") == "0":
        return
    try:
        import patchright.async_api as pa
    except Exception:
        return

    # slow_mo 默认关（0）：太激进会搅乱动态 UI。需要时设环境变量开启。
    slowmo = random.uniform(_envf("SMU_SLOWMO_MIN", 0), _envf("SMU_SLOWMO_MAX", 0))
    wf_min = _envf("SMU_WAIT_FACTOR_MIN", 1.2)
    wf_max = _envf("SMU_WAIT_FACTOR_MAX", 2.2)
    jitter = _envf("SMU_WAIT_JITTER_MS", 1200)

    # ---- 补丁 1：launch / launch_persistent_context 注入 slow_mo（仅当 >0）----
    if slowmo > 0:
        for meth in ("launch", "launch_persistent_context"):
            orig = getattr(pa.BrowserType, meth, None)
            if orig is None:
                continue

            def make(orig_fn):
                async def wrapper(self, *args, **kwargs):
                    kwargs.setdefault("slow_mo", slowmo)
                    return await orig_fn(self, *args, **kwargs)
                return wrapper

            setattr(pa.BrowserType, meth, make(orig))

    # ---- 补丁 2：wait_for_timeout 随机化 ----
    orig_wait = pa.Page.wait_for_timeout

    async def humanized_wait(self, timeout):
        factor = random.uniform(wf_min, wf_max)
        extra = random.uniform(0, jitter)
        return await orig_wait(self, timeout * factor + extra)

    pa.Page.wait_for_timeout = humanized_wait

    # ---- 补丁 3（macOS 兼容）：Control+A 全选 → Meta+A ----
    # sau 是按 Windows/Linux 写的，用 Control+KeyA 全选输入框（比如设定时时清空日期）。
    # macOS 全选是 Cmd(Meta)+A，否则选不中，会导致「定时<2小时」等 bug。
    if sys.platform == "darwin":
        orig_press = pa.Keyboard.press

        async def mac_press(self, key, *args, **kwargs):
            if isinstance(key, str) and key.lower().replace("control", "ctrl") in ("ctrl+a", "ctrl+keya"):
                key = "Meta+KeyA"
            return await orig_press(self, key, *args, **kwargs)

        pa.Keyboard.press = mac_press


install()
