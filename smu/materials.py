"""素材目录扫描与配对。

标准约定（推荐，零配置直接用）——每个素材一个子文件夹，文件按命名后缀配对：

    <素材目录>/
      11_在线诉讼适用规则及庭审故障处理/
        11_在线诉讼适用规则及庭审故障处理.mp4            ← 横版主视频
        11_..._竖屏.mp4                                  ← 竖版（预留给抖音等）
        11_..._封面_B站16比9.jpg                         ← B站主封面
        11_..._封面_B站首页4比3.jpg                      ← B站首页推荐封面
        11_..._封面_竖版3比4.jpg                         ← 竖版封面（预留）
        11_..._文案_B站.txt                              ← B站文案（正文=简介，#行=标签）
        11_..._文案_抖音.txt / _小红书.txt / _视频号.txt ← 预留

宽容规则（目录不完全符合约定时自动启用，识别结果记入 Material.notes）：
  - 平铺目录：素材目录里直接放视频文件（不分文件夹）→ 每个视频是一个素材，
    按「同名前缀」匹配封面和文案（如 01_xx.mp4 + 01_xx_封面.jpg + 01_xx.txt）。
  - 封面比例：文件名没有 16比9/4比3 关键词时，用 ffprobe 读图片实际宽高比分类。
  - 文案兜底：文件名没有平台关键词、但文件夹里只有一个 txt/md → 当作B站文案。
  - 视频歧义：多个横版视频时优先取与素材同名的；否则取第一个并记入 notes。
  - 格式：视频 mp4/mov/mkv/webm，封面 jpg/jpeg/png/webp，文案 txt/md。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
COPY_EXTS = {".txt", ".md"}

_COPY_PLATFORMS = {"B站": "bilibili", "抖音": "douyin", "小红书": "xiaohongshu",
                   "视频号": "shipinhao", "微博": "weibo"}

_RATIO_TOL = 0.06   # 宽高比分类容差


@dataclass
class Material:
    folder: Path
    name: str                      # 文件夹名（平铺模式为视频文件名去后缀）
    order: int | None              # 名称前缀序号，如 11
    video: Path | None = None      # 横版主视频
    video_vertical: Path | None = None
    cover169: Path | None = None
    cover43: Path | None = None
    cover_vertical: Path | None = None
    copies: dict[str, Path] = field(default_factory=dict)  # 平台 → 文案文件
    notes: list[str] = field(default_factory=list)         # 宽容识别/歧义提示

    @property
    def complete_for_bilibili(self) -> bool:
        return not self.missing_for_bilibili()

    def missing_for_bilibili(self) -> list[str]:
        out = []
        if not self.video:
            out.append("视频")
        if not self.cover169:
            out.append("16:9封面")
        if not self.cover43:
            out.append("4:3封面")
        if "bilibili" not in self.copies:
            out.append("B站文案")
        return out


def _media_size(path: Path) -> tuple[int, int] | None:
    """ffprobe 读取图片/视频的宽高，失败返回 None。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20)
        w, h = r.stdout.strip().splitlines()[0].split(",")[:2]
        return int(w), int(h)
    except Exception:
        return None


def _ratio_close(size: tuple[int, int], target: float) -> bool:
    w, h = size
    return h > 0 and abs(w / h - target) <= _RATIO_TOL


def _classify(mat: Material, files: list[Path]) -> None:
    """把一组文件归类进 Material。先按文件名约定，再按宽容规则兜底。"""
    videos = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
    images = [f for f in files if f.suffix.lower() in IMG_EXTS]
    texts = [f for f in files if f.suffix.lower() in COPY_EXTS]

    # ---- 视频：竖屏/竖版关键词分流，横版多个时优先同名 ----
    landscape: list[Path] = []
    for f in videos:
        if "竖屏" in f.name or "竖版" in f.name:
            mat.video_vertical = mat.video_vertical or f
        else:
            landscape.append(f)
    if landscape:
        exact = [f for f in landscape if f.stem == mat.name]
        mat.video = (exact or landscape)[0]
        if not exact and len(landscape) > 1:
            mat.notes.append(f"有 {len(landscape)} 个横版视频，选用了 {mat.video.name}")

    # ---- 封面 第一轮：文件名关键词 ----
    rest_images: list[Path] = []
    for f in images:
        if "16比9" in f.name:
            mat.cover169 = mat.cover169 or f
        elif "4比3" in f.name:
            mat.cover43 = mat.cover43 or f
        elif "3比4" in f.name or "竖" in f.name:
            mat.cover_vertical = mat.cover_vertical or f
        else:
            rest_images.append(f)

    # ---- 封面 第二轮：ffprobe 实际宽高比 ----
    for f in rest_images:
        size = _media_size(f)
        if not size:
            continue
        if not mat.cover169 and _ratio_close(size, 16 / 9):
            mat.cover169 = f
            mat.notes.append(f"按宽高比识别 16:9 封面：{f.name}")
        elif not mat.cover43 and _ratio_close(size, 4 / 3):
            mat.cover43 = f
            mat.notes.append(f"按宽高比识别 4:3 封面：{f.name}")
        elif not mat.cover_vertical and size[0] < size[1]:
            mat.cover_vertical = f
            mat.notes.append(f"按宽高比识别竖版封面：{f.name}")

    # ---- 文案 第一轮：平台关键词 ----
    rest_texts: list[Path] = []
    for f in texts:
        for zh, key in _COPY_PLATFORMS.items():
            if zh in f.name:
                mat.copies.setdefault(key, f)
                break
        else:
            rest_texts.append(f)

    # ---- 文案 第二轮：唯一未识别文本当B站文案 ----
    if "bilibili" not in mat.copies and len(rest_texts) == 1:
        mat.copies["bilibili"] = rest_texts[0]
        mat.notes.append(f"文件名无平台标识，把唯一文本当B站文案：{rest_texts[0].name}")


def _order_of(name: str) -> int | None:
    m = re.match(r"^(\d+)[_\-]", name)
    return int(m.group(1)) if m else None


def scan(root: Path) -> list[Material]:
    """扫描素材目录：子文件夹模式 + 平铺模式并存，按序号（无序号按名称）排序。"""
    if not root.is_dir():
        raise FileNotFoundError(f"素材目录不存在：{root}")
    mats: list[Material] = []

    # 子文件夹模式
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        mat = Material(folder=d, name=d.name, order=_order_of(d.name))
        _classify(mat, [f for f in sorted(d.iterdir()) if f.is_file() and not f.name.startswith(".")])
        if mat.video or mat.copies:
            mats.append(mat)

    # 平铺模式：素材目录里直接放的视频文件
    root_files = [f for f in sorted(root.iterdir()) if f.is_file() and not f.name.startswith(".")]
    for v in [f for f in root_files if f.suffix.lower() in VIDEO_EXTS
              if "竖屏" not in f.name and "竖版" not in f.name]:
        mat = Material(folder=root, name=v.stem, order=_order_of(v.stem))
        companions = [f for f in root_files if f != v and f.stem.startswith(v.stem)]
        _classify(mat, [v, *companions])
        mat.notes.insert(0, "平铺目录素材（建议整理成每素材一个文件夹）")
        mats.append(mat)

    return sorted(mats, key=lambda m: (m.order is None, m.order if m.order is not None else 0, m.name))


def parse_copy(path: Path) -> dict:
    """解析文案：非 # 行做简介（去空行，与手动投稿格式一致），# 行解析为标签。"""
    tags: list[str] = []
    body: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        if all(t.startswith("#") for t in tokens):
            tags.extend(t.lstrip("#").strip() for t in tokens if t.lstrip("#").strip())
        else:
            body.append(line)
    return {"desc": "\n".join(body), "tags": tags}


def select(mats: list[Material], spec: list[str]) -> list[Material]:
    """按序号/范围/名称选择素材。spec 形如 ["11"], ["11-20"], ["11-"], ["11_在线诉讼..."]。"""
    by_order = {m.order: m for m in mats if m.order is not None}
    by_name = {m.name: m for m in mats}
    picked: list[Material] = []
    for s in spec:
        s = s.strip()
        if re.fullmatch(r"\d+", s):
            m = by_order.get(int(s))
            if not m:
                raise KeyError(f"找不到序号 {s} 的素材")
            picked.append(m)
        elif re.fullmatch(r"\d+\s*-\s*\d*", s):
            lo_s, hi_s = [x.strip() for x in s.split("-", 1)]
            lo = int(lo_s)
            hi = int(hi_s) if hi_s else max(by_order, default=lo)
            picked.extend(by_order[i] for i in range(lo, hi + 1) if i in by_order)
        elif s in by_name:
            picked.append(by_name[s])
        else:
            raise KeyError(f"无法识别的素材选择：{s}")
    seen: set[str] = set()
    return [m for m in picked if not (m.name in seen or seen.add(m.name))]
