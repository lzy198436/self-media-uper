"""数据采集层：拉每条已发视频的播放/互动数据，存本地带时间戳（时间序列）。

各平台采集方式（实测可用）：
  - douyin：cookie GET creator.douyin.com/web/api/media/aweme/post/ → 每条 statistics
  - bilibili：cookie GET member.bilibili.com/x/web/archives → 每稿 stat
  - xiaohongshu：创作中心接口需 x-s 签名，故用 patchright 打开 /new/note-manager，
    拦截页面自己发的已签名 `note/user/posted` 响应（免签名，见 _xhs_collect.py）
  - shipinhao（视频号）：同理可拦 channels 数据中心，待接入（见 _TODO_URLS）

存储：~/.self-media-uper/stats/<platform>.jsonl，每次 pull 对每条视频追加一条快照记录；
append-only 的 jsonl 天然是时间序列，Hermes 直接读它出趋势/最佳时间/周报。
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .state import SMU_HOME

STATS_DIR = SMU_HOME / "stats"
SAU_COOKIES = SMU_HOME / "engines" / "social-auto-upload" / "cookies"
BILI_COOKIE = SMU_HOME / "bilibili.cookies.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36"

# 待接入平台的创作中心数据页（后续同小红书/视频号思路：patchright 拦截已签名响应）
_TODO_URLS: dict[str, str] = {
    # kuaishou 等
}


class StatsError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_get_json(url: str, cookie: str) -> dict:
    req = urllib.request.Request(url, headers={"Cookie": cookie, "User-Agent": UA,
                                               "Referer": url})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# ---------- 各平台 cookie ----------

def _douyin_cookie(account: str) -> str:
    f = SAU_COOKIES / f"douyin_{account}.json"
    if not f.is_file():
        raise StatsError(f"抖音账号「{account}」未登录：{f} 不存在")
    d = json.loads(f.read_text(encoding="utf-8"))
    return "; ".join(f"{c['name']}={c['value']}" for c in d.get("cookies", [])
                     if c.get("domain", "").endswith("douyin.com"))


def _bilibili_cookie() -> str:
    if not BILI_COOKIE.is_file():
        raise StatsError(f"B站未登录：{BILI_COOKIE} 不存在")
    d = json.loads(BILI_COOKIE.read_text(encoding="utf-8"))
    ck = {c["name"]: c["value"] for c in d["cookie_info"]["cookies"]}
    return f"SESSDATA={ck['SESSDATA']}; bili_jct={ck['bili_jct']}; DedeUserID={ck['DedeUserID']}"


# ---------- 各平台采集 ----------

def _pull_douyin(account: str) -> list[dict]:
    cookie = _douyin_cookie(account)
    out: list[dict] = []
    max_cursor = 0
    for _ in range(20):                       # 翻页上限，防失控
        url = ("https://creator.douyin.com/web/api/media/aweme/post/"
               f"?count=20&status=0&scene=0&max_cursor={max_cursor}")
        data = _http_get_json(url, cookie)
        lst = data.get("aweme_list") or []
        for a in lst:
            st = a.get("statistics") or {}
            out.append({
                "video_id": a.get("aweme_id", ""),
                "title": (a.get("desc") or "").strip()[:60],
                "published_at": a.get("create_time"),     # unix 秒
                "play": st.get("play_count", 0),
                "like": st.get("digg_count", 0),
                "comment": st.get("comment_count", 0),
                "share": st.get("share_count", 0),
                "collect": st.get("collect_count", 0),
            })
        if not data.get("has_more") or not lst:
            break
        max_cursor = data.get("max_cursor", 0)
    return out


def _pull_bilibili(account: str = "") -> list[dict]:
    cookie = _bilibili_cookie()
    out: list[dict] = []
    for pn in range(1, 30):
        url = f"https://member.bilibili.com/x/web/archives?status=pubed&pn={pn}&ps=50"
        data = _http_get_json(url, cookie).get("data") or {}
        rows = data.get("arc_audits") or []
        for r in rows:
            ar = r.get("Archive") or {}
            st = r.get("stat") or {}
            out.append({
                "video_id": ar.get("bvid", ""),
                "title": (ar.get("title") or "").strip()[:60],
                "published_at": ar.get("ptime"),          # unix 秒
                "play": st.get("view", 0),
                "like": st.get("like", 0),
                "comment": st.get("reply", 0),
                "share": st.get("share", 0),
                "collect": st.get("favorite", 0),
                "coin": st.get("coin", 0),
            })
        if len(rows) < 50:
            break
    return out


def _pull_browser_intercept(platform: str, sau_platform: str, account: str, script_name: str) -> list[dict]:
    """小红书/视频号通用：跑 patchright 采集脚本拦截已签名响应（免签名）。"""
    cookie = SAU_COOKIES / f"{sau_platform}_{account}.json"
    if not cookie.is_file():
        raise StatsError(f"{platform} 账号「{account}」未登录：{cookie} 不存在")
    sau_py = SMU_HOME / "engines" / "social-auto-upload" / ".venv" / "bin" / "python"
    if not sau_py.is_file():
        raise StatsError(f"找不到 sau venv：{sau_py}（{platform} 采集需要 patchright）")
    script = Path(__file__).resolve().parent / script_name
    proc = subprocess.run([str(sau_py), str(script), str(cookie)],
                          capture_output=True, text=True, timeout=180)
    lines = (proc.stdout or "").strip().splitlines()
    line = lines[-1] if lines else ""
    try:
        data = json.loads(line)
    except Exception:
        raise StatsError(f"{platform} 采集输出解析失败：{proc.stdout[-200:]}{proc.stderr[-200:]}")
    if "error" in data:
        raise StatsError(f"{platform} 采集失败：{data['error']}")
    return data.get("notes", [])


def _pull_xiaohongshu(account: str) -> list[dict]:
    return _pull_browser_intercept("xiaohongshu", "xiaohongshu", account, "_xhs_collect.py")


def _pull_shipinhao(account: str) -> list[dict]:
    return _pull_browser_intercept("shipinhao", "tencent", account, "_channels_collect.py")


_COLLECTORS = {"douyin": _pull_douyin, "bilibili": _pull_bilibili,
               "xiaohongshu": _pull_xiaohongshu, "shipinhao": _pull_shipinhao}


# ---------- 存储 ----------

def _store(platform: str) -> Path:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    return STATS_DIR / f"{platform}.jsonl"


def pull(platform: str, account: str = "main") -> int:
    """采集一次快照，追加到 <platform>.jsonl，返回采集到的视频数。"""
    if platform not in _COLLECTORS:
        url = _TODO_URLS.get(platform, "")
        raise StatsError(f"{platform} 数据采集待接入"
                         + (f"（创作中心数据页 {url}，多需签名接口）" if url else ""))
    rows = _COLLECTORS[platform](account)
    fetched = _now()
    with _store(platform).open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({"platform": platform, "account": account,
                                 "fetched_at": fetched, **r}, ensure_ascii=False) + "\n")
    return len(rows)


def load(platform: str) -> list[dict]:
    f = _store(platform)
    if not f.is_file():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_snapshot(platform: str) -> list[dict]:
    """返回最近一次 pull 的各视频记录。"""
    rows = load(platform)
    if not rows:
        return []
    last = max(r["fetched_at"] for r in rows)
    return [r for r in rows if r["fetched_at"] == last]
