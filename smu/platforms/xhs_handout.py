"""小红书图文讲义半自动发布：复用 ~/.hermes xiaohongshu-skills 的浏览器扩展通道。

单张竖版封面图 + PDF 讲义附件 + 小红书文案，填好内容后停在发布按钮前交人手点
（半自动，规避「脚本自动发布」风控）。

集成方式同 sau.py：用 xiaohongshu-skills 自己的 venv + 内联 -c bootstrap subprocess 调用
（它的 venv 有 websockets，smu 没有，必须用它的解释器）。走用户日常浏览器扩展，
登录态和指纹和真人一致，风控特征远低于 patchright 陌生浏览器。

前置条件（靠用户保证）：
  - Chrome 装了 XHS Bridge 扩展并启用、已授予小红书全部域名访问权
  - 浏览器里「有且仅有一个」creator.xiaohongshu.com 标签页（多开会 attach 错），已登录
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..materials import Material
from .sau import parse_platform_copy   # 首行=标题/其余非#=正文/#=标签，直接复用

XHS_SKILLS_DIR = Path(os.environ.get("SMU_XHS_SKILLS_DIR")
                      or Path.home() / ".hermes" / "skills" / "xiaohongshu-skills")

XHS_TITLE_MAX = 20   # 小红书图文标题上限
XHS_TAG_MAX = 10


class HandoutError(RuntimeError):
    pass


def _xhs_python() -> str:
    py = XHS_SKILLS_DIR / ".venv" / "bin" / "python"
    if not py.is_file():
        raise HandoutError(
            f"找不到 xiaohongshu-skills 的 venv：{py}\n"
            f"（可用 SMU_XHS_SKILLS_DIR 指定其它路径）")
    return str(py)


def _bridge_env() -> dict:
    """连 bridge 必须绕过 SOCKS 代理，否则 websockets 报 requires python-socks。
    requests/websockets 各认一套大小写，两套都设。"""
    return {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "no_proxy": "localhost,127.0.0.1,::1",
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "ALL_PROXY": "",
        "all_proxy": "",
    }


# 在 xiaohongshu-skills venv 里以 python -u -c 运行的发布脚本（零落盘，跟 smu 走版本控制）。
_BOOTSTRAP = r'''
import sys, os, time, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--scripts-dir", required=True)
ap.add_argument("--cover", required=True)
ap.add_argument("--pdf", required=True)
ap.add_argument("--title", required=True)
ap.add_argument("--content", default="")
ap.add_argument("--tags", default="")
a = ap.parse_args()
sys.path.insert(0, a.scripts_dir)

from xhs.bridge import BridgePage
from xhs.publish import (fill_publish_form, _navigate_to_publish_page,
                         _upload_images, _fill_publish_form)
from xhs.types import PublishImageContent

PDF_INPUT = '.file-relation-container input[type="file"]'

# 自己点「上传图文」tab：xhs-skills 的 _click_publish_tab 过滤太严(rect.left<0/
# elementFromPoint 遮挡)，且页面常被别的扩展注入(button-hp-installed/data-hp-bound)
# 干扰判定 → 点不中。这里宽容版：找 span.title=="上传图文" 的 .creator-tab，
# 只跳过明显隐藏的(opacity极小/移出屏幕/aria-hidden)，对真实那个直接 click。
_CLICK_IMAGE_TAB_JS = r"""
(() => {
  const tabs = document.querySelectorAll('div.creator-tab');
  for (const tab of tabs) {
    const span = tab.querySelector('span.title');
    const txt = span ? span.textContent.trim() : tab.textContent.trim();
    if (txt !== '上传图文') continue;
    const st = window.getComputedStyle(tab);
    const r = tab.getBoundingClientRect();
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    if (parseFloat(st.opacity) < 0.01) continue;
    if (tab.getAttribute('aria-hidden') === 'true') continue;
    if (r.left < -1000 || r.top < -1000) continue;   // 移出屏幕的(left:-9999px)
    tab.click();
    return 'clicked';
  }
  return 'not_found';
})()
"""


def _fill_image_form(page, content):
    """替代 fill_publish_form：导航→自己宽容点图文 tab→传图→填表单(不发布)。"""
    import time as _t
    _navigate_to_publish_page(page)
    deadline = _t.monotonic() + 15
    clicked = False
    while _t.monotonic() < deadline:
        if page.evaluate(_CLICK_IMAGE_TAB_JS) == 'clicked':
            clicked = True
            break
        _t.sleep(1)
    if not clicked:
        raise RuntimeError("没找到/点不中「上传图文」tab")
    _t.sleep(1.5)
    _upload_images(page, content.image_paths)
    tags = content.tags[:10]
    _fill_publish_form(page, content.title, content.content, tags,
                       content.schedule_time, content.is_original, content.visibility)


page = BridgePage("ws://localhost:9333")
if not page.is_server_running():
    print("ERR bridge server 未运行", file=sys.stderr); sys.exit(2)
if not page.is_extension_connected():
    print("ERR 浏览器扩展未连接（确认 Chrome 装了 XHS Bridge 扩展、有一个已登录的 "
          "creator.xiaohongshu.com 标签页）", file=sys.stderr); sys.exit(2)

tags = [t for t in a.tags.split(",") if t]
content = PublishImageContent(title=a.title, content=a.content, tags=tags,
                              image_paths=[a.cover], is_original=False, visibility="")

# 1) 传封面图 + 填标题/正文/话题（不点发布）
try:
    _fill_image_form(page, content)
except Exception as e:
    print(f"ERR 填表单/传封面失败：{e}", file=sys.stderr); sys.exit(2)

# 2) 灌 PDF：进编辑页后 PDF「选择文件」模块才渲染，先等它出现
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        if page.has_element(PDF_INPUT):
            break
    except Exception:
        pass
    time.sleep(1)
else:
    print("ERR 未出现 PDF 附件模块（file-relation-container），无法挂 PDF",
          file=sys.stderr); sys.exit(2)

try:
    page.set_file_input(PDF_INPUT, [a.pdf])   # CDP DOM.setFileInputFiles，对 display:none 有效
except Exception as e:
    print(f"ERR 灌 PDF 失败：{e}", file=sys.stderr); sys.exit(2)

# 校验 PDF 真挂上（input.files.length>0 或出现文件项节点）
attached = False
for _ in range(30):
    try:
        n = page.evaluate(
            "(()=>{const i=document.querySelector('"
            ".file-relation-container input[type=\\\"file\\\"]');"
            "return i&&i.files?i.files.length:0;})()")
        if n and int(n) > 0:
            attached = True; break
    except Exception:
        pass
    time.sleep(1)
if not attached:
    print("WARN 未能确认 PDF 已挂上，请在浏览器里手动核对附件", file=sys.stderr)

# 3) 半自动：停在发布按钮前，交人手点（非交互终端如实报错，绝不自动发）
if not sys.stdin or not sys.stdin.isatty():
    print("ERR 非交互终端（疑似 cron），小红书图文讲义半自动需真终端手点发布，已中止",
          file=sys.stderr); sys.exit(2)
print("\n" + "=" * 56, file=sys.stderr)
print(" 小红书图文讲义已填好（封面图 + 标题 + 正文 + 话题 + PDF 附件）", file=sys.stderr)
print("    1) 随手刷两下信息流/看下通知（降低脚本特征）", file=sys.stderr)
print("    2) 核对标题/正文/封面/PDF 附件无误", file=sys.stderr)
print("    3) 亲手点「发布」按钮", file=sys.stderr)
print("    完成后回到这里按【回车】，我来核对结果。", file=sys.stderr)
print("=" * 56, file=sys.stderr)
sys.stdin.readline()

# 4) 核对发布成功：URL 跳 publish/success 或离开发布编辑页
def _url():
    try:
        return page.evaluate("window.location.href") or ""
    except Exception:
        return ""
for _ in range(10):
    u = _url()
    if "publish/success" in u or "/publish/publish" not in u:
        print("OK 已离开发布页，按成功处理"); sys.exit(0)
    time.sleep(1)
print("还没检测到发布成功页。若已发布请按回车确认，否则点完发布再按回车。", file=sys.stderr)
sys.stdin.readline()
u = _url()
if "publish/success" in u or "/publish/publish" not in u:
    print("OK 已离开发布页，按成功处理"); sys.exit(0)
print("ERR 仍停留在发布页，未检测到发布成功，按失败处理", file=sys.stderr); sys.exit(1)
'''


def publish_handout(mat: Material, opts) -> dict:
    """发一条小红书图文讲义（封面图 + PDF）。半自动：填好停发布前交人手点。失败抛 HandoutError。"""
    cover = mat.cover_vertical
    pdf = mat.handout_pdf
    copy = mat.copies.get("xiaohongshu")
    if not (cover and pdf and copy):
        raise HandoutError(f"{mat.name} 缺关键件（封面图/PDF/小红书文案）")

    meta = parse_platform_copy(copy)              # {title, desc, tags}
    title = meta["title"][:XHS_TITLE_MAX]
    tags = meta["tags"][:XHS_TAG_MAX]
    scripts_dir = str(XHS_SKILLS_DIR / "scripts")

    argv = [_xhs_python(), "-u", "-c", _BOOTSTRAP,
            "--scripts-dir", scripts_dir,
            "--cover", str(cover),
            "--pdf", str(pdf),
            "--title", title,
            "--content", meta["desc"],
            "--tags", ",".join(tags)]

    if opts.dry_run:
        print(f"  [dry-run] 标题: {title}")
        print(f"  [dry-run] 封面: {cover.name}")
        print(f"  [dry-run] PDF : {pdf.name}")
        print(f"  [dry-run] 标签: {','.join(tags)}")
        return {"note": "图文讲义", "title": title, "id": "(dry-run)"}

    print(f"     🖐 小红书图文讲义半自动：内容自动填好后，请在浏览器手点发布并回终端按回车")
    rc = subprocess.call(argv, cwd=scripts_dir, env=_bridge_env())
    if rc != 0:
        raise HandoutError(f"小红书图文讲义半自动未成功（退出码 {rc}）")
    return {
        "note": "图文讲义",
        "title": title,
        "cover": cover.name,
        "pdf": pdf.name,
        "at": datetime.now(timezone.utc).isoformat(),
    }
