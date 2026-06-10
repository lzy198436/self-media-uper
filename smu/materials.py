"""素材目录扫描与配对。

目录约定（每个素材一个子文件夹，文件按命名后缀配对）：

    <素材目录>/
      11_在线诉讼适用规则及庭审故障处理/
        11_在线诉讼适用规则及庭审故障处理.mp4            ← 横版主视频
        11_..._竖屏.mp4                                  ← 竖版（预留给抖音等）
        11_..._封面_B站16比9.jpg                         ← B站主封面
        11_..._封面_B站首页4比3.jpg                      ← B站首页推荐封面
        11_..._封面_竖版3比4.jpg                         ← 竖版封面（预留）
        11_..._文案_B站.txt                              ← B站文案（正文=简介，#行=标签）
        11_..._文案_抖音.txt / _小红书.txt / _视频号.txt ← 预留
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

IMG_EXTS = (".jpg", ".jpeg", ".png")


@dataclass
class Material:
    folder: Path
    name: str                      # 文件夹名，如 "11_在线诉讼适用规则及庭审故障处理"
    order: int | None              # 文件夹名前缀序号，如 11
    video: Path | None = None      # 横版主视频
    video_vertical: Path | None = None
    cover169: Path | None = None
    cover43: Path | None = None
    cover_vertical: Path | None = None
    copies: dict[str, Path] = field(default_factory=dict)  # 平台 → 文案文件

    @property
    def complete_for_bilibili(self) -> bool:
        return self.video is not None

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


_COPY_PLATFORMS = {"B站": "bilibili", "抖音": "douyin", "小红书": "xiaohongshu",
                   "视频号": "shipinhao", "微博": "weibo"}


def _scan_folder(folder: Path) -> Material:
    m = re.match(r"^(\d+)[_\-]", folder.name)
    mat = Material(folder=folder, name=folder.name, order=int(m.group(1)) if m else None)
    for f in sorted(folder.iterdir()):
        if f.name.startswith("."):
            continue
        low = f.name.lower()
        if low.endswith(".mp4"):
            if "竖屏" in f.name or "竖版" in f.name:
                mat.video_vertical = f
            elif mat.video is None or f.stem == folder.name:
                mat.video = f
        elif low.endswith(IMG_EXTS) and "封面" in f.name:
            if "16比9" in f.name:
                mat.cover169 = f
            elif "4比3" in f.name:
                mat.cover43 = f
            elif "3比4" in f.name or "竖" in f.name:
                mat.cover_vertical = f
        elif low.endswith(".txt") and "文案" in f.name:
            for zh, key in _COPY_PLATFORMS.items():
                if zh in f.name:
                    mat.copies[key] = f
                    break
    return mat


def scan(root: Path) -> list[Material]:
    """扫描素材目录，返回按序号（无序号则按名称）排序的素材列表。"""
    if not root.is_dir():
        raise FileNotFoundError(f"素材目录不存在：{root}")
    mats = [_scan_folder(d) for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    mats = [m for m in mats if m.video or m.copies]   # 跳过空文件夹
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
    """按序号/范围/文件夹名选择素材。spec 形如 ["11"], ["11-20"], ["11-"], ["11_在线诉讼..."]。"""
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
