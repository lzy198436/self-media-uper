"""state.py 并发安全测试：python3 -m unittest discover tests

验证 atomic_update 在并发/陈旧快照下不丢写——根治 B站 20 条只存 2 条的事故。
"""

import os
import sys
import tempfile
import time
import unittest
from multiprocessing import Process
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _writer(home: str, platform: str, names: list, delay: float) -> None:
    """子进程入口（须模块级，spawn 才能 pickle）：逐条原子合并写盘。"""
    os.environ["SMU_HOME"] = home
    import importlib
    from smu import state as S
    importlib.reload(S)  # 让 SMU_HOME 环境变量在子进程生效
    for n in names:
        def _m(s, _n=n, _p=platform):
            S.platform_state(s, _p)["published"][_n] = {"source": "smu", "title": _n}
        S.atomic_update(_m)
        time.sleep(delay)


class TestStateConcurrency(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["SMU_HOME"] = self.home

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def test_stale_snapshot_does_not_clobber(self):
        """陈旧快照合并：A 读旧 state→B 写入→A 再写自己这条，B 的写入不该丢。"""
        import importlib
        from smu import state as S
        importlib.reload(S)
        S.atomic_update(lambda s: S.platform_state(s, "bilibili")["published"].update({"x": {"source": "smu"}}))
        stale = S.load_state()  # A 的旧快照（只有 bilibili.x）
        # B 并发写入 douyin.y
        S.atomic_update(lambda s: S.platform_state(s, "douyin")["published"].update({"y": {"source": "smu"}}))
        # A 用 atomic_update 合并自己这条（基于磁盘最新，而非 stale 整盘覆盖）
        S.atomic_update(lambda s: S.platform_state(s, "bilibili")["published"].update({"z": {"source": "smu"}}))
        final = S.load_state()
        self.assertIn("y", final["platforms"]["douyin"]["published"], "B 的并发写入被覆盖了")
        self.assertEqual(set(final["platforms"]["bilibili"]["published"]), {"x", "z"})

    def test_parallel_processes_no_loss(self):
        """真并发：B站快(20)+抖音慢(10)+视频号(10) 同时写，全部保住。"""
        from smu import state as S
        procs = [
            Process(target=_writer, args=(self.home, "bilibili", [f"{i}_b" for i in range(20)], 0.004)),
            Process(target=_writer, args=(self.home, "douyin", [f"{i}_d" for i in range(10)], 0.04)),
            Process(target=_writer, args=(self.home, "shipinhao", [f"{i}_s" for i in range(10)], 0.025)),
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        import importlib
        importlib.reload(S)
        s = S.load_state()
        self.assertEqual(len(s["platforms"]["bilibili"]["published"]), 20)
        self.assertEqual(len(s["platforms"]["douyin"]["published"]), 10)
        self.assertEqual(len(s["platforms"]["shipinhao"]["published"]), 10)


if __name__ == "__main__":
    unittest.main()
