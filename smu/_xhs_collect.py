"""小红书数据采集（跑在 sau 的 venv，仅依赖 patchright）。

思路（参考 ReaJason/xhs 找到接口名 + 拦截响应，免签名）：
打开创作中心 /new/note-manager → 页面用自己的签名调
`/api/galaxy/creator/note/user/posted` 拉「我的笔记列表(含统计)」→ 我们拦截该响应，
滚动翻页直到不再有新笔记 → 打印 JSON 到 stdout 供 smu 解析。

用法：<sau_venv_python> _xhs_collect.py <cookie_json_path>
输出：stdout 一行 JSON：{"notes": [...]}（出错则 {"error": "..."}）
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

try:
    from patchright.sync_api import sync_playwright
except Exception as e:  # noqa: BLE001
    print(json.dumps({"error": f"patchright import 失败: {e}"}))
    sys.exit(0)

# 真实接口是 /api/galaxy/v2/creator/note/user/posted（带 v2），用宽松子串匹配兼容 v1/v2
NOTE_API = "note/user/posted"
NOTE_MANAGER = "https://creator.xiaohongshu.com/new/note-manager"


def main() -> None:
    cookie_path = Path(sys.argv[1])
    if not cookie_path.is_file():
        print(json.dumps({"error": f"cookie 不存在: {cookie_path}"}))
        return
    cookies = json.loads(cookie_path.read_text(encoding="utf-8")).get("cookies", [])

    by_id: dict[str, dict] = {}

    def on_response(resp):
        if NOTE_API not in resp.url:
            return
        try:
            data = resp.json().get("data", {})
            for n in data.get("notes", []):
                nid = n.get("id")
                if nid:
                    by_id[nid] = n
        except Exception:
            pass

    # 每次用全新临时 profile（复用固定目录会有 chrome 单例锁/残留态，导致接口不触发）
    user_dir = tempfile.mkdtemp(prefix="smu_xhs_")
    with sync_playwright() as p:
        # 小红书强环境检测，headless 取不到数据，必须有头（采集时短暂弹窗）
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=user_dir, headless=False, channel="chrome")
        try:
            ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.on("response", on_response)
            page.goto(NOTE_MANAGER, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(7000)         # 首屏 note/user/posted 较慢
            # 温和滚动触发翻页；数量稳定且 >0 即停
            last = -1
            for _ in range(8):
                if by_id and len(by_id) == last:
                    break
                last = len(by_id)
                try:
                    page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                except Exception:
                    pass
                page.wait_for_timeout(2500)
            print(f"[xhs] 拦截到 {len(by_id)} 条 | url={page.url}", file=sys.stderr)
        finally:
            ctx.close()
            shutil.rmtree(user_dir, ignore_errors=True)

    notes = []
    for n in by_id.values():
        notes.append({
            "video_id": n.get("id", ""),
            "title": (n.get("display_title") or "").strip()[:60],
            "published_at": n.get("time"),                 # "YYYY-MM-DD HH:MM"
            "play": n.get("view_count", 0),
            "like": n.get("likes", 0),
            "comment": n.get("comments_count", 0),
            "share": n.get("shared_count", 0),
            "collect": n.get("collected_count", 0),
        })
    print(json.dumps({"notes": notes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
