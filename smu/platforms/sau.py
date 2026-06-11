"""social-auto-upload(sau)适配器：抖音/小红书/快手共用一套 sau CLI。

设计要点：
  - 不改 sau 源码：通过 `_sau_humanize` 运行时补丁给 sau 注入随机延迟（防机械节奏）。
  - 调用方式：用 sau 自己的 venv python 跑，先 import 拟人化补丁，再运行 sau_cli。
  - 素材映射：竖屏视频 + 平台文案（首行=标题，正文=描述，#行=标签）+ 3:4 竖版封面。
  - 登录态：复用 sau 的账号 cookie 文件（~/.self-media-uper/engines/social-auto-upload/cookies/）。

已知限制：sau 抖音目前自动选「内容为个人观点或见解」声明，AI 声明需后续在 sau 侧处理。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..materials import Material, parse_copy, _media_size
from ..state import SMU_HOME, platform_state
from .base import PlatformAdapter

# 抖音封面槽路由：实测 sau 的 set_thumbnail 标签页顺序与当前抖音 UI 相反
# （--thumbnail-landscape 实际落到抖音「竖版」槽，--thumbnail-portrait 落到「横版」槽）。
# 若日后 sau 修正了顺序，把这里改成 False 即可，无需动其它逻辑。
_DOUYIN_COVER_SWAP = True

_RATIO_TOL = 0.12   # 比例匹配容差

SAU_DIR = Path(os.environ.get("SMU_SAU_DIR") or SMU_HOME / "engines" / "social-auto-upload")
PLAT_DIR = Path(__file__).resolve().parent          # 含 _sau_humanize.py

# sau 子命令名（smu 平台名 → sau 平台名）。视频号在 sau 里叫 tencent。
_SAU_PLATFORM = {"douyin": "douyin", "xiaohongshu": "xiaohongshu",
                 "kuaishou": "kuaishou", "shipinhao": "tencent"}

# 各平台标题长度上限
_MAX_TITLE = {"douyin": 30, "xiaohongshu": 20, "kuaishou": 30, "shipinhao": 30}
TITLE_MAX = 30

# 各平台话题/标签数量上限（超限会导致 sau 卡在话题下拉框死循环）
#   抖音：发布页最多 5 个话题（实测超过会卡住）
_MAX_TAGS = {"douyin": 5, "xiaohongshu": 10, "kuaishou": 5, "shipinhao": 10}

# 封面策略：dual=竖3:4+横4:3 双封面（抖音/视频号），single=单张3:4（小红书）
_COVER_MODE = {"douyin": "dual", "xiaohongshu": "single", "kuaishou": "single", "shipinhao": "dual"}

# 抖音自主声明类型（取值须与抖音发布页弹窗选项文字完全一致）
DOUYIN_AI_DECLARATION = "内容由AI生成"


class SauError(RuntimeError):
    pass


def _sau_python() -> str:
    py = SAU_DIR / ".venv" / "bin" / "python"
    if not py.is_file():
        raise SauError(f"找不到 sau 的 venv：{py}\n请先安装 social-auto-upload（见 docs/sau-setup.md）")
    return str(py)


def _run_sau(sau_args: list[str], *, interactive: bool = False, env_extra: dict | None = None) -> subprocess.CompletedProcess | int:
    """用 sau venv + 拟人化补丁运行 sau_cli。interactive=True 直通终端（登录扫码用）。"""
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(PLAT_DIR)!r}); "
        "import _sau_humanize; import runpy; "
        "sys.argv = ['sau'] + sys.argv[1:]; "
        "runpy.run_module('sau_cli', run_name='__main__')"
    )
    cmd = [_sau_python(), "-c", bootstrap, *sau_args]
    env = {**os.environ, **(env_extra or {})}
    if interactive:
        return subprocess.call(cmd, cwd=str(SAU_DIR), env=env)
    return subprocess.run(cmd, cwd=str(SAU_DIR), env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def parse_platform_copy(path: Path) -> dict:
    """平台文案：首行=标题，其余非#行=描述，#行=标签。"""
    raw = parse_copy(path)              # {desc: 全部非#正文, tags: [...]}
    lines = [l for l in raw["desc"].splitlines() if l.strip()]
    title = lines[0] if lines else ""
    desc = "\n".join(lines[1:]) if len(lines) > 1 else ""
    return {"title": title[:TITLE_MAX], "desc": desc, "tags": raw["tags"]}


class SauAdapter(PlatformAdapter):
    def __init__(self, platform: str):
        if platform not in _SAU_PLATFORM:
            raise SauError(f"sau 不支持平台 {platform}")
        self.name = platform
        self.sau_platform = _SAU_PLATFORM[platform]

    def _account(self, opts) -> str:
        return getattr(opts, "account", None) or "main"

    # ---------- 登录 ----------

    def login(self) -> None:
        acct = os.environ.get("SMU_ACCOUNT", "main")
        print(f"登录 {self.name}（扫码），账号标签：{acct}")
        rc = _run_sau([self.sau_platform, "login", "--account", acct, "--headed"], interactive=True)
        sys.exit(rc)

    def is_logged_in(self, account: str = "main") -> bool:
        ck = SAU_DIR / "cookies" / f"{self.sau_platform}_{account}.json"
        return ck.is_file()

    # ---------- 文案/素材映射 ----------

    def build_meta(self, material: Material, opts) -> dict:
        title_max = _MAX_TITLE.get(self.name, 30)
        copy_path = material.copies.get(self.name)
        if copy_path:
            m = parse_platform_copy(copy_path)
        else:
            m = {"title": material.name, "desc": "", "tags": []}
        m["title"] = m["title"][:title_max]
        # 附加固定标签（话题等），文案自带的话题优先，固定标签补位
        tags = list(m["tags"])
        for t in getattr(opts, "ensure_tags", []):
            if t and t not in tags:
                tags.append(t)
        m["tags"] = tags[:_MAX_TAGS.get(self.name, 5)]   # 超限会卡死，必须截断
        return m

    def _video(self, material: Material) -> Path | None:
        # 竖屏平台优先竖屏视频，没有则退回主视频
        return material.video_vertical or material.video

    @staticmethod
    def _pick_cover(candidates: list[Path], target_ratio: float, want_portrait: bool):
        """按真实宽高比（ffprobe）从候选图里挑最匹配的。返回 (路径, 实际比例, 偏差)。"""
        best = None
        best_ratio = None
        best_diff = 1e9
        for p in candidates:
            sz = _media_size(p)
            if not sz:
                continue
            w, h = sz
            if h == 0:
                continue
            if want_portrait and not h > w:        # 竖图必须高>宽
                continue
            if not want_portrait and not w > h:    # 横图必须宽>高
                continue
            r = w / h
            diff = abs(r - target_ratio)
            if diff < best_diff:
                best, best_ratio, best_diff = p, r, diff
        return best, best_ratio, best_diff

    def detect_douyin_covers(self, material: Material) -> dict:
        """传图前先检测：按真实比例挑 3:4 竖封面 + 4:3 横封面，不靠文件名。

        抖音：竖封面 3:4，横封面 4:3（不是 16:9）。比例对不上的不传（避免传错）。
        返回 {portrait, landscape, notes, warnings}。
        """
        cands = [c for c in (material.cover_vertical, material.cover169,
                             material.cover43) if c]
        # 去重保序
        seen: set = set()
        cands = [c for c in cands if not (c in seen or seen.add(c))]

        portrait, pr, pd = self._pick_cover(cands, 3 / 4, want_portrait=True)    # 竖 3:4
        landscape, lr, ld = self._pick_cover(cands, 4 / 3, want_portrait=False)   # 横 4:3

        notes, warnings = [], []
        if portrait and pd <= _RATIO_TOL:
            notes.append(f"竖封面 ✓ {portrait.name}（实测 {pr:.2f}≈0.75）")
        else:
            portrait = None
            warnings.append("没有比例接近 3:4 的竖封面，竖封面不传（平台会自动截帧）")
        if landscape and ld <= _RATIO_TOL:
            notes.append(f"横封面 ✓ {landscape.name}（实测 {lr:.2f}≈1.33）")
        else:
            landscape = None
            warnings.append("没有比例接近 4:3 的横封面，横封面不传")
        return {"portrait": portrait, "landscape": landscape, "notes": notes, "warnings": warnings}

    def _cover_args(self, material: Material) -> tuple[list[str], list[str], list[str]]:
        """按平台返回封面命令参数 + 提示 + 警告。
        single（小红书/快手）：单张 3:4 --thumbnail；dual（抖音/视频号）：竖3:4 + 横4:3。
        抖音的 sau 标签页顺序与 UI 相反，用 _DOUYIN_COVER_SWAP 对调参数名。
        """
        c = self.detect_douyin_covers(material)        # {portrait(3:4), landscape(4:3), ...}
        mode = _COVER_MODE.get(self.name, "single")
        args: list[str] = []
        notes = list(c["notes"])
        warnings = list(c["warnings"])
        if mode == "single":
            if c["portrait"]:
                args += ["--thumbnail", str(c["portrait"])]
            warnings = [w for w in warnings if "横封面" not in w]   # 单封面不需要横封面
            return args, notes, warnings
        # dual
        pslot, lslot = "--thumbnail-portrait", "--thumbnail-landscape"
        if self.name == "douyin" and _DOUYIN_COVER_SWAP:
            pslot, lslot = "--thumbnail-landscape", "--thumbnail-portrait"
        if c["portrait"]:
            args += [pslot, str(c["portrait"])]
        if c["landscape"]:
            args += [lslot, str(c["landscape"])]
        return args, notes, warnings

    # ---------- 发布 ----------

    def publish(self, material: Material, state: dict, opts) -> dict:
        video = self._video(material)
        if not video:
            raise SauError(f"{material.name} 没有可用视频")
        acct = self._account(opts)
        if not self.is_logged_in(acct) and not opts.dry_run:
            raise SauError(f"{self.name} 账号「{acct}」未登录：先运行 smu login --platform {self.name}")

        meta = self.build_meta(material, opts)
        args = [self.sau_platform, "upload-video",
                "--account", acct,
                "--file", str(video),
                "--title", meta["title"]]
        if meta["desc"]:
            args += ["--desc", meta["desc"]]
        if meta["tags"]:
            args += ["--tags", ",".join(meta["tags"])]
        # 封面：传图前按真实比例检测（不靠文件名），按平台出 single/dual 参数。
        cover_args, cnotes, cwarn = self._cover_args(material)
        for n in cnotes:
            print(f"     🔍 {n}")
        for w in cwarn:
            print(f"     ⚠️ {w}", file=sys.stderr)
        args += cover_args
        if getattr(opts, "schedule", None):
            args += ["--schedule", opts.schedule]
        args += ["--headed"]   # 有头真实 Chrome，最隐蔽

        # 抖音自主声明：默认选「内容由AI生成」（与B站AI声明一致），--no-ai-statement 则用 sau 默认
        env_extra = {}
        if self.name == "douyin" and getattr(opts, "ai_statement", True):
            env_extra["SMU_DOUYIN_DECLARATION"] = DOUYIN_AI_DECLARATION
            print(f"     🧾 自主声明将选「{DOUYIN_AI_DECLARATION}」")

        if opts.dry_run:
            print("  [dry-run] sau", " ".join(f"'{a}'" if (" " in a or "\n" in a) else a for a in args))
            return {"id": "(dry-run)", "title": meta["title"]}

        proc = _run_sau(args, env_extra=env_extra)
        out = proc.stdout or ""
        for line in out.splitlines():
            print("   ", line.rstrip())
        ok = proc.returncode == 0 and ("发布成功" in out or "submitted" in out or "upload submitted" in out.lower())
        if not ok:
            raise SauError(f"sau 退出码 {proc.returncode}")
        return {
            "id": "",            # sau 不回 aweme_id，留空
            "title": meta["title"],
            "account": acct,
            "scheduled": getattr(opts, "schedule", None),
            "at": datetime.now(timezone.utc).isoformat(),
        }
