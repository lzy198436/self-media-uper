"""materials.py 单元测试：python3 -m unittest discover tests"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smu import materials as M  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def make_jpg(path: Path, w: int, h: int) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=gray:s={w}x{h}", "-frames:v", "1", str(path)], check=True)


class TestStandardLayout(unittest.TestCase):
    """标准约定：子文件夹 + 命名关键词，不需要 ffprobe。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        d = self.tmp / "01_测试素材"
        d.mkdir()
        (d / "01_测试素材.mp4").write_bytes(b"x")
        (d / "01_测试素材_竖屏.mp4").write_bytes(b"x")
        (d / "01_测试素材_封面_B站16比9.jpg").write_bytes(b"x")
        (d / "01_测试素材_封面_B站首页4比3.jpg").write_bytes(b"x")
        (d / "01_测试素材_封面_竖版3比4.jpg").write_bytes(b"x")
        (d / "01_测试素材_文案_B站.txt").write_text("标题行\n正文\n#标签1 #标签2", encoding="utf-8")
        (d / "01_测试素材_文案_抖音.txt").write_text("抖音文案", encoding="utf-8")
        (d / "01_测试素材.srt").write_text("", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_match(self):
        mats = M.scan(self.tmp)
        self.assertEqual(len(mats), 1)
        m = mats[0]
        self.assertEqual(m.order, 1)
        self.assertEqual(m.video.name, "01_测试素材.mp4")
        self.assertEqual(m.video_vertical.name, "01_测试素材_竖屏.mp4")
        self.assertIn("16比9", m.cover169.name)
        self.assertIn("4比3", m.cover43.name)
        self.assertIn("3比4", m.cover_vertical.name)
        self.assertEqual(m.copies["bilibili"].name, "01_测试素材_文案_B站.txt")
        self.assertEqual(m.copies["douyin"].name, "01_测试素材_文案_抖音.txt")
        self.assertEqual(m.notes, [])          # 全部走标准约定，无宽容提示
        self.assertTrue(m.complete_for_bilibili)


class TestFlatLayout(unittest.TestCase):
    """平铺目录：视频直接放素材目录里，按同名前缀配对。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "02_平铺素材.mp4").write_bytes(b"x")
        (self.tmp / "02_平铺素材_封面_B站16比9.jpg").write_bytes(b"x")
        (self.tmp / "02_平铺素材.txt").write_text("正文\n#tag", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_flat(self):
        mats = M.scan(self.tmp)
        self.assertEqual(len(mats), 1)
        m = mats[0]
        self.assertEqual(m.name, "02_平铺素材")
        self.assertEqual(m.order, 2)
        self.assertEqual(m.video.name, "02_平铺素材.mp4")
        self.assertIsNotNone(m.cover169)
        # 唯一无平台标识的 txt 兜底为B站文案
        self.assertEqual(m.copies["bilibili"].name, "02_平铺素材.txt")
        self.assertTrue(any("平铺" in n for n in m.notes))
        # 缺 4:3 封面 → 不完整，upload 预检会拦
        self.assertIn("4:3封面", m.missing_for_bilibili())


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg/ffprobe")
class TestRatioClassify(unittest.TestCase):
    """封面文件名无关键词时按实际宽高比分类。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        d = self.tmp / "03_比例识别"
        d.mkdir()
        (d / "03_比例识别.mp4").write_bytes(b"x")
        make_jpg(d / "fengmian-a.jpg", 1920, 1080)
        make_jpg(d / "fengmian-b.jpg", 1200, 900)
        (d / "wenan.txt").write_text("正文", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_ratio(self):
        m = M.scan(self.tmp)[0]
        self.assertEqual(m.cover169.name, "fengmian-a.jpg")
        self.assertEqual(m.cover43.name, "fengmian-b.jpg")
        self.assertEqual(m.copies["bilibili"].name, "wenan.txt")
        self.assertTrue(any("宽高比" in n for n in m.notes))


class TestParseCopyAndSelect(unittest.TestCase):
    def test_parse_copy(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "c.txt"
            f.write_text("标题行\n\n正文一\n正文二\n#法考 #民诉法 #2026法考", encoding="utf-8")
            r = M.parse_copy(f)
            self.assertEqual(r["desc"], "标题行\n正文一\n正文二")
            self.assertEqual(r["tags"], ["法考", "民诉法", "2026法考"])

    def test_select_range(self):
        mats = [M.Material(folder=Path("."), name=f"{i:02d}_x", order=i) for i in range(1, 31)]
        self.assertEqual([m.order for m in M.select(mats, ["11-20"])], list(range(11, 21)))
        self.assertEqual([m.order for m in M.select(mats, ["28-"])], [28, 29, 30])
        self.assertEqual([m.order for m in M.select(mats, ["5", "5", "7"])], [5, 7])
        with self.assertRaises(KeyError):
            M.select(mats, ["99"])


if __name__ == "__main__":
    unittest.main()
