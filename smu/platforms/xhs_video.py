"""小红书视频半自动发布：复用 ~/.hermes xiaohongshu-skills 浏览器扩展（日常浏览器）。

替代 sau patchright（陌生浏览器=高风控）：走用户日常浏览器扩展，真实设备指纹+登录态，
风控特征低。半自动：脚本传视频+填文案+自动设封面后，停发布前交用户真人编辑+手点发布
（真人操作 isTrusted=true、真实节奏，会话整体最像真人——对被预警的老账号最稳）。

集成方式照搬 xhs_handout.py（同一套 bridge 通道），视频特有：点「上传视频」tab、
传视频后等处理完成、自动设封面（best-effort，DOM 需联调实测）。

前置（靠用户保证）：Chrome 装 XHS Bridge 扩展并授权小红书全域名、浏览器里「有且仅一个」
已登录的 creator.xiaohongshu.com 标签页。
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from ..materials import Material
from .base import PlatformAdapter
from .sau import parse_platform_copy
# 复用 handout 已验证的工具（venv 定位、绕代理 env、skills 目录）
from .xhs_handout import XHS_SKILLS_DIR, _bridge_env, _xhs_python

XHS_VIDEO_TITLE_MAX = 20
XHS_TAG_MAX = 10


class XhsVideoError(RuntimeError):
    pass


# 在 xiaohongshu-skills venv 里以 python -u -c 运行的视频发布脚本。
_BOOTSTRAP = r'''
import sys, os, time, argparse, json

ap = argparse.ArgumentParser()
ap.add_argument("--scripts-dir", required=True)
ap.add_argument("--video", required=True)
ap.add_argument("--cover", default="")
ap.add_argument("--title", required=True)
ap.add_argument("--content", default="")
ap.add_argument("--tags", default="")
a = ap.parse_args()
sys.path.insert(0, a.scripts_dir)

from xhs.bridge import BridgePage
from xhs.publish_video import (_navigate_to_publish_page, _upload_video,
                               _fill_publish_video_form, _wait_for_publish_button_clickable)

# 宽容点「上传视频」tab：xhs-skills 的 _click_publish_tab 在被别的扩展注入(button-hp-installed)
# 的页面上点不中(rect/elementFromPoint 过滤太严)。只跳明显隐藏的，对真实那个直接 click。
_CLICK_VIDEO_TAB_JS = r"""
(() => {
  const tabs = document.querySelectorAll('div.creator-tab');
  for (const tab of tabs) {
    const span = tab.querySelector('span.title');
    const txt = span ? span.textContent.trim() : tab.textContent.trim();
    if (txt !== '上传视频') continue;
    const st = window.getComputedStyle(tab);
    const r = tab.getBoundingClientRect();
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    if (parseFloat(st.opacity) < 0.01) continue;
    if (tab.getAttribute('aria-hidden') === 'true') continue;
    if (r.left < -1000 || r.top < -1000) continue;
    tab.click();
    return 'clicked';
  }
  return 'not_found';
})()
"""

# 封面 DOM 探查（联调用）：dump 含「封面」文本的可点元素 + 所有 file input 的 accept/容器。
_COVER_DUMP_JS = r"""
(() => {
  const out = [];
  document.querySelectorAll('button, span, div').forEach(el => {
    if (el.children.length > 2) return;
    const t = (el.textContent || '').trim();
    if (/封面|编辑封面|设置封面|选择封面|上传封面|修改封面/.test(t) && t.length < 12) {
      const r = el.getBoundingClientRect();
      out.push({tag: el.tagName, cls: String(el.className).slice(0,40), text: t,
                x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width)});
    }
  });
  const inputs = [...document.querySelectorAll('input[type=file]')].map(i =>
    ({accept: i.getAttribute('accept') || '', cls: String(i.className).slice(0,40),
      parent: i.parentElement ? String(i.parentElement.className).slice(0,40) : ''}));
  return JSON.stringify({coverEls: out, fileInputs: inputs});
})()
"""

# 封面上传入口候选（联调实测后回填真实 selector）。封面 input 限图片，与视频 input(video/*) 区分。
_COVER_INPUT_CANDIDATES = [
    'input[type="file"][accept*="image"]',
    '.cover-edit-container input[type="file"]',
    '.cover-uploader input[type="file"]',
]
_COVER_OPEN_BTN_TEXTS = ['编辑封面', '设置封面', '选择封面', '修改封面']
_COVER_CONFIRM_TEXTS = ['确定', '完成', '应用', '保存']


def _dump_cover_dom(page):
    """联调诊断：把封面相关 DOM 打到 stderr，用于回填真实 selector。SMU_DUMP_COVER=1 时启用。"""
    try:
        print("[cover-dump] " + (page.evaluate(_COVER_DUMP_JS) or ""), file=sys.stderr)
    except Exception as e:
        print(f"[cover-dump] 失败：{e}", file=sys.stderr)


def _set_cover(page, cover_path):
    """传完视频、处理完后设封面。best-effort：找不到/失败只 WARN 不致命（真人手动补）。"""
    if not cover_path:
        return False
    if os.environ.get("SMU_DUMP_COVER") == "1":
        _dump_cover_dom(page)

    def _find_input():
        for sel in _COVER_INPUT_CANDIDATES:
            try:
                if page.has_element(sel):
                    return sel
            except Exception:
                pass
        return None

    sel = _find_input()
    if not sel:
        # 先点「编辑封面」打开弹窗，再找 input
        try:
            page.evaluate("""(() => {
              const texts = %s;
              for (const el of document.querySelectorAll('button, span, div')) {
                if (el.children.length > 3) continue;
                if (texts.includes((el.textContent || '').trim())) { el.click(); return 'clicked'; }
              }
              return 'not_found';
            })()""" % json.dumps(_COVER_OPEN_BTN_TEXTS))
        except Exception:
            pass
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not sel:
            sel = _find_input()
            time.sleep(0.5)
    if not sel:
        print("WARN 未找到封面上传入口（selector 需联调实测，可设 SMU_DUMP_COVER=1 看 DOM），"
              "跳过自动设封面，请发布前手动设", file=sys.stderr)
        return False
    try:
        page.set_file_input(sel, [cover_path])
    except Exception as e:
        print(f"WARN 灌封面失败：{e}（请手动设封面）", file=sys.stderr)
        return False
    time.sleep(1.5)
    try:
        page.evaluate("""(() => {
          const texts = %s;
          for (const el of document.querySelectorAll('button, span')) {
            if (texts.includes((el.textContent || '').trim())) { el.click(); return 'ok'; }
          }
          return 'no_confirm';
        })()""" % json.dumps(_COVER_CONFIRM_TEXTS))
    except Exception:
        pass
    print("[cover] 已尝试设封面，请发布前核对", file=sys.stderr)
    return True


page = BridgePage("ws://localhost:9333")
if not page.is_server_running():
    print("ERR bridge server 未运行", file=sys.stderr); sys.exit(2)
if not page.is_extension_connected():
    print("ERR 浏览器扩展未连接（确认装了 XHS Bridge 扩展、有一个已登录的 "
          "creator.xiaohongshu.com 标签页）", file=sys.stderr); sys.exit(2)

tags = [t for t in a.tags.split(",") if t]

# 1) 导航 + 宽容点「上传视频」tab
_navigate_to_publish_page(page)
deadline = time.monotonic() + 15
clicked = False
while time.monotonic() < deadline:
    if page.evaluate(_CLICK_VIDEO_TAB_JS) == 'clicked':
        clicked = True
        break
    time.sleep(1)
if not clicked:
    print("ERR 没找到/点不中「上传视频」tab", file=sys.stderr); sys.exit(2)
time.sleep(1.5)

# 2) 传视频 + 等处理完（_upload_video 内含 set_file_input + 等发布按钮可点，最长约10min）
try:
    _upload_video(page, a.video)
except Exception as e:
    print(f"ERR 传视频/等待处理失败：{e}", file=sys.stderr); sys.exit(2)

# 3) 填标题/正文/标签（不点发布）
try:
    _fill_publish_video_form(page, a.title, a.content, tags, None, "")
except Exception as e:
    print(f"ERR 填表单失败：{e}", file=sys.stderr); sys.exit(2)

# 4) 自动设封面（best-effort）
if a.cover:
    try:
        _set_cover(page, a.cover)
    except Exception as e:
        print(f"WARN 设封面异常（请手动设）：{e}", file=sys.stderr)

# 5) 半自动停顿：交人真人编辑 + 手点发布（非交互终端如实报错，绝不自动发）
if not sys.stdin or not sys.stdin.isatty():
    print("ERR 非交互终端（疑似 cron），小红书视频半自动需真终端手点发布，已中止",
          file=sys.stderr); sys.exit(2)
print("\n" + "=" * 56, file=sys.stderr)
print(" 小红书视频已填好（视频 + 标题 + 正文 + 标签 + 封面）", file=sys.stderr)
print("    1) 核对封面是否设上（自动设封面是实验功能，没设上请手动设）", file=sys.stderr)
print("    2) 随手刷两下信息流/看通知（降脚本特征）", file=sys.stderr)
print("    3) 核对/修改标题正文（真人编辑），亲手点「发布」", file=sys.stderr)
print("    完成后回到这里按【回车】，我来核对结果。", file=sys.stderr)
print("=" * 56, file=sys.stderr)
sys.stdin.readline()

# 6) 核对发布成功
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


def publish_video_handout(mat: Material, opts) -> dict:
    """发一条小红书视频（扩展半自动）。停发布前交人手点。失败抛 XhsVideoError。"""
    video = mat.video_vertical or mat.video      # 竖屏优先（同 SauAdapter._video）
    cover = mat.cover_vertical                    # 可空（封面 best-effort）
    copy = mat.copies.get("xiaohongshu")
    if not (video and copy):
        raise XhsVideoError(f"{mat.name} 缺关键件（视频/小红书文案）")

    meta = parse_platform_copy(copy)
    title = meta["title"][:XHS_VIDEO_TITLE_MAX]
    tags = meta["tags"][:XHS_TAG_MAX]
    scripts_dir = str(XHS_SKILLS_DIR / "scripts")

    argv = [_xhs_python(), "-u", "-c", _BOOTSTRAP,
            "--scripts-dir", scripts_dir,
            "--video", str(video),
            "--cover", str(cover) if cover else "",
            "--title", title,
            "--content", meta["desc"],
            "--tags", ",".join(tags)]

    if opts.dry_run:
        print(f"  [dry-run] 视频: {video.name}")
        print(f"  [dry-run] 封面: {cover.name if cover else '（无，半自动手动设/截帧）'}")
        print(f"  [dry-run] 标题: {title}")
        print(f"  [dry-run] 标签: {','.join(tags)}")
        return {"id": "(dry-run)", "title": title}

    print("     🖐 小红书视频半自动(扩展)：内容/封面自动填好后，请真人核对+手点发布并回车")
    rc = subprocess.call(argv, cwd=scripts_dir, env=_bridge_env())
    if rc != 0:
        raise XhsVideoError(f"小红书视频半自动未成功（退出码 {rc}）")
    return {
        "id": "",
        "title": title,
        "note": "扩展半自动",
        "video": video.name,
        "cover": cover.name if cover else "",
        "at": datetime.now(timezone.utc).isoformat(),
    }


class XhsVideoAdapter(PlatformAdapter):
    """小红书视频走 xhs-skills 扩展（日常浏览器）的适配器，无缝插进 cmd_upload 循环。"""
    name = "xiaohongshu"

    def login(self) -> None:
        print("扩展通道无需 smu login：用你日常浏览器登录小红书 + 装 XHS Bridge 扩展即可。")

    def is_logged_in(self) -> bool:
        return True   # 登录态在日常浏览器里，smu 不管理；真发时扩展会校验

    def publish(self, material: Material, state: dict, opts) -> dict:
        return publish_video_handout(material, opts)

    def build_meta(self, material: Material, opts) -> dict:
        copy = material.copies.get("xiaohongshu")
        if not copy:
            return {"title": material.name, "desc": "", "tags": []}
        m = parse_platform_copy(copy)
        m["title"] = m["title"][:XHS_VIDEO_TITLE_MAX]
        m["tags"] = m["tags"][:XHS_TAG_MAX]
        return m

    def list_published(self, opts) -> list[dict]:
        raise NotImplementedError("扩展通道暂不支持发布前查重")
