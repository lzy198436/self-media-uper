"""视频号数据采集（跑在 sau 的 venv，仅依赖 patchright）。

思路同小红书（拦截页面已签名响应，免签名）：
打开视频号助手数据中心 → 页面调
`/micro/content/cgi-bin/mmfinderassistant-bin/post/post_list` 拉「我的视频列表(含统计)」
→ 拦截该响应 → 打印 JSON 到 stdout 供 smu 解析。

用法：<sau_venv_python> _channels_collect.py <cookie_json_path>
输出：stdout 一行 JSON：{"notes": [...]}（出错则 {"error": "..."}）
"""

import json
import sys
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

    # 视频号会话主要在 localStorage：必须用 new_context(storage_state=完整json) 恢复，
    # 而非只 add_cookies(只灌 cookie=登出态，post_list 抓不到)。普通独立浏览器即可，
    # 不用持久化 profile(实测会有同目录并发 launch 崩溃，且非必要)。
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        ctx = browser.new_context(storage_state=str(cookie_path))
        try:
            page = ctx.new_page()
            page.on("response", on_response)
            page.goto(DATA_CENTER, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(7000)
            # 翻页：滚到底触发 post_list 续拉。旧版只滚 8 轮、数量稳定 1 轮就停 →
            # 漏抓（实测 11 条只拿到 5）。改成最多 25 轮、连续 3 轮无新增才停，宁慢勿漏。
            last = -1
            stable = 0
            for _ in range(25):
                cur = len(by_id)
                if cur == last:
                    stable += 1
                    if cur > 0 and stable >= 3:
                        break
                else:
                    stable = 0
                last = cur
                try:
                    page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                except Exception:
                    pass
                page.wait_for_timeout(2500)
            print(f"[channels] 拦截到 {len(by_id)} 条 | url={page.url}", file=sys.stderr)
        finally:
            ctx.close()
            browser.close()

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
