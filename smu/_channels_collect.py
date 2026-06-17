"""视频号数据采集（跑在 sau 的 venv，仅依赖 patchright）。

打开视频号助手「帖子管理」页 /platform/post/list（不是 dataCenter——它会重定向到首页只剩
最近 5 条），拦截首个 `post/post_list` 请求拿到 URL+body 模板，再在页面上下文用 fetch
复刻该请求、递增 currentPage 翻页直到 continueFlag=False，拿全部视频（含统计）。
这样复用页面自己的 cookie+签名，免签名，且能翻全（实测 52 条全拿到，旧滚动法只 5 条）。

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
POST_LIST_PAGE = "https://channels.weixin.qq.com/platform/post/list"

# 页面上下文翻页脚本：复刻首个 post_list 请求，递增 currentPage 直到 continueFlag=False。
_PAGINATE_JS = """async (args) => {
  const [url, bodyStr] = args;
  const base = JSON.parse(bodyStr);
  const all = [];
  const seen = {};
  let page = 1, total = 0;
  for (let i = 0; i < 30; i++) {
    base.currentPage = page;
    base.timestamp = String(Date.now());
    let j;
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(base),
        credentials: 'include',
      });
      j = await resp.json();
    } catch (e) { break; }
    const d = (j && j.data) || {};
    total = d.totalCount || total;
    for (const n of (d.list || [])) {
      if (n.objectId && !seen[n.objectId]) { seen[n.objectId] = 1; all.push(n); }
    }
    if (!d.continueFlag) break;
    page++;
  }
  return {list: all, total: total};
}"""


def main() -> None:
    cookie_path = Path(sys.argv[1])
    if not cookie_path.is_file():
        print(json.dumps({"error": f"cookie 不存在: {cookie_path}"}))
        return

    tmpl = {"url": None, "body": None}

    def on_request(req):
        if POST_API in req.url and tmpl["url"] is None:
            tmpl["url"] = req.url
            tmpl["body"] = req.post_data

    # 视频号会话主要在 localStorage：必须用 new_context(storage_state=完整json) 恢复，
    # 而非只 add_cookies(只灌 cookie=登出态，post_list 抓不到)。普通独立浏览器即可。
    items: list = []
    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        ctx = browser.new_context(storage_state=str(cookie_path))
        try:
            page = ctx.new_page()
            page.on("request", on_request)
            page.goto(POST_LIST_PAGE, wait_until="domcontentloaded", timeout=45000)
            # 等首个 post_list 请求出现（拿到 URL+body 模板）
            for _ in range(20):
                if tmpl["url"]:
                    break
                page.wait_for_timeout(500)
            if not tmpl["url"]:
                print(json.dumps({"error": "未捕获 post_list 请求（页面结构变化或未登录）"}))
                return
            result = page.evaluate(_PAGINATE_JS, [tmpl["url"], tmpl["body"]])
            items = result.get("list", [])
            total = result.get("total", 0)
            print(f"[channels] 翻页拿到 {len(items)}/{total} 条 | url={page.url}", file=sys.stderr)
        finally:
            ctx.close()
            browser.close()

    notes = []
    for n in items:
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
