"""视频号数据采集（跑在 sau 的 venv，仅依赖 patchright）。

思路同小红书（拦截页面已签名响应，免签名）：
打开视频号助手数据中心 → 页面调
`/micro/content/cgi-bin/mmfinderassistant-bin/post/post_list` 拉「我的视频列表(含统计)」
→ 拦截该响应 → 打印 JSON 到 stdout 供 smu 解析。

用法：<sau_venv_python> _channels_collect.py <cookie_json_path>
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

POST_API = "post/post_list"
DATA_CENTER = "https://channels.weixin.qq.com/platform/dataCenter"


def main() -> None:
    cookie_path = Path(sys.argv[1])
    if not cookie_path.is_file():
        print(json.dumps({"error": f"cookie 不存在: {cookie_path}"}))
        return
    cookies = json.loads(cookie_path.read_text(encoding="utf-8")).get("cookies", [])

    by_id: dict[str, dict] = {}

    def on_response(resp):
        if POST_API not in resp.url:
            return
        try:
            for n in resp.json().get("data", {}).get("list", []):
                oid = n.get("objectId")
                if oid:
                    by_id[oid] = n
        except Exception:
            pass

    user_dir = tempfile.mkdtemp(prefix="smu_ch_")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=user_dir, headless=False, channel="chrome")
        try:
            ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.on("response", on_response)
            page.goto(DATA_CENTER, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(7000)
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
            print(f"[channels] 拦截到 {len(by_id)} 条 | url={page.url}", file=sys.stderr)
        finally:
            ctx.close()
            shutil.rmtree(user_dir, ignore_errors=True)

    notes = []
    for n in by_id.values():
        desc = n.get("desc") or {}
        # shortTitle 形如 [{"shortTitle": "你会借钱吗？"}]，可能为空串
        st = ""
        raw_st = desc.get("shortTitle")
        if isinstance(raw_st, list) and raw_st and isinstance(raw_st[0], dict):
            st = raw_st[0].get("shortTitle") or ""
        elif isinstance(raw_st, dict):
            st = raw_st.get("shortTitle") or ""
        elif isinstance(raw_st, str):
            st = raw_st
        title = str(st or desc.get("description") or "").strip()[:60]
        notes.append({
            "video_id": n.get("objectId", ""),
            "title": title,
            "published_at": n.get("createTime"),       # unix 秒
            "play": n.get("readCount", 0),
            "like": n.get("likeCount", 0),
            "comment": n.get("commentCount", 0),
            "share": n.get("forwardCount", 0),
            "collect": n.get("favCount", 0),
        })
    print(json.dumps({"notes": notes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
