"""B站投稿适配器（实测验证于 2026-06-10）。

底层用 biliup-cli（biliup/biliup 源码编译，--submit web 走 vu/web/add/v3），
配合 cookie 直调 member API 完成 4:3 封面上传与话题解析。

实测确认的提交字段（--extra-fields 平铺进 JSON body）：
  creation_statement: {"id": 1}   创作声明「含AI生成内容」（错误格式报 21001，
                                  纯文本旧版走 neutral_mark，合法值见 archive/pre）
  cover43:   4:3 首页推荐封面 URL（先 POST /x/vu/web/cover/up 拿 URL）
  topic_id / mission_id:          话题（/x/vupre/web/topic/search 按名精确匹配）
  human_type2: 1010               新分区「知识」
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..materials import Material, parse_copy
from ..state import SMU_HOME, platform_state
from .base import PlatformAdapter

COOKIE_FILE = SMU_HOME / "bilibili.cookies.json"
LEGACY_COOKIE = Path.home() / ".lvying-engine" / "bilibili.cookies.json"

TITLE_MAX = 80
DESC_MAX = 1900
TAG_MAX_COUNT = 10
TAG_MAX_LEN = 20

DEFAULT_TID = 124            # 旧分区：社科·法律·心理
DEFAULT_HUMAN_TYPE2 = 1010   # 新分区：知识
CREATION_STATEMENT_AI = {"id": 1}   # 含AI生成内容
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36"


class BilibiliError(RuntimeError):
    pass


def find_biliup() -> str:
    exe = shutil.which("biliup") or str(Path.home() / ".local/bin/biliup")
    if not Path(exe).is_file():
        raise BilibiliError("找不到 biliup，编译方法见 docs/biliup-build.md")
    return exe


class BilibiliAdapter(PlatformAdapter):
    name = "bilibili"

    # ---------- 登录 ----------

    def _ensure_cookie_file(self) -> None:
        if COOKIE_FILE.is_file():
            return
        if LEGACY_COOKIE.is_file():
            SMU_HOME.mkdir(parents=True, exist_ok=True)
            shutil.copy(LEGACY_COOKIE, COOKIE_FILE)
            print(f"已从 {LEGACY_COOKIE} 迁移登录态")

    def is_logged_in(self) -> bool:
        self._ensure_cookie_file()
        return COOKIE_FILE.is_file()

    def login(self) -> None:
        SMU_HOME.mkdir(parents=True, exist_ok=True)
        print(f"登录态将保存到 {COOKIE_FILE}（请选「扫码登录」）")
        sys.exit(subprocess.call([find_biliup(), "-u", str(COOKIE_FILE), "login"]))

    def renew(self) -> None:
        sys.exit(subprocess.call([find_biliup(), "-u", str(COOKIE_FILE), "renew"]))

    def _cookies(self) -> dict[str, str]:
        if not self.is_logged_in():
            raise BilibiliError("未登录：先运行 smu login")
        d = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        return {c["name"]: c["value"] for c in d["cookie_info"]["cookies"]}

    # ---------- member API ----------

    def _member_api(self, path: str, data: dict | None = None) -> dict:
        ck = self._cookies()
        cookie = f"SESSDATA={ck['SESSDATA']}; bili_jct={ck['bili_jct']}; DedeUserID={ck['DedeUserID']}"
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(f"https://member.bilibili.com{path}", data=body, headers={
            "Cookie": cookie, "User-Agent": UA,
            **({"Content-Type": "application/x-www-form-urlencoded"} if body else {}),
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        if resp.get("code") != 0:
            raise BilibiliError(f"{path} 返回 {resp.get('code')}: {resp.get('message')}")
        return resp.get("data") or {}

    def _upload_cover(self, path: Path) -> str:
        ck = self._cookies()
        img = base64.b64encode(path.read_bytes()).decode()
        return self._member_api("/x/vu/web/cover/up", {
            "cover": "data:image/jpeg;base64," + img, "csrf": ck["bili_jct"]})["url"]

    def _resolve_topic(self, name: str, state: dict) -> tuple[int, int] | None:
        pstate = platform_state(state, self.name)
        cache = pstate.setdefault("topics", {})
        if name in cache:
            return cache[name]["topic_id"], cache[name]["mission_id"]
        q = urllib.parse.quote(name)
        data = self._member_api(f"/x/vupre/web/topic/search?keywords={q}&page_size=20&offset=0")
        for t in (data.get("result") or {}).get("topics") or []:
            if t["name"] == name:
                cache[name] = {"topic_id": t["id"], "mission_id": t["mission_id"]}
                return t["id"], t["mission_id"]
        return None

    # ---------- 投稿 ----------

    def build_meta(self, material: Material, opts) -> dict:
        title = (f"{opts.title_prefix}{material.name}")[:TITLE_MAX]
        desc, copy_tags = "", []
        copy_path = material.copies.get("bilibili")
        if copy_path:
            parsed = parse_copy(copy_path)
            desc = parsed["desc"][:DESC_MAX]
            copy_tags = parsed["tags"]
        tags: list[str] = []
        for t in [opts.topic or "", *getattr(opts, "ensure_tags", []), *copy_tags]:
            t = t.strip()[:TAG_MAX_LEN]
            if t and t not in tags:
                tags.append(t)
        return {"title": title, "desc": desc, "tags": tags[:TAG_MAX_COUNT]}

    def publish(self, material: Material, state: dict, opts) -> dict:
        if not material.video:
            raise BilibiliError(f"{material.name} 缺少横版视频")
        meta = self.build_meta(material, opts)

        extra: dict = {"human_type2": opts.human_type2}
        if opts.ai_statement:
            extra["creation_statement"] = CREATION_STATEMENT_AI
        if opts.topic and not opts.dry_run:
            topic = self._resolve_topic(opts.topic, state)
            if topic:
                extra["topic_id"], extra["mission_id"] = topic
            else:
                print(f"    ⚠️ 找不到话题「{opts.topic}」，跳过话题", file=sys.stderr)
        if material.cover43 and not opts.dry_run:
            extra["cover43"] = self._upload_cover(material.cover43)

        cmd = [find_biliup(), "-u", str(COOKIE_FILE), "upload",
               "--submit", "web",
               "--title", meta["title"],
               "--desc", meta["desc"],
               "--tag", ",".join(meta["tags"]),
               "--tid", str(opts.tid),
               "--copyright", "1",
               "--extra-fields", json.dumps(extra, ensure_ascii=False)]
        if material.cover169:
            cmd += ["--cover", str(material.cover169)]
        if getattr(opts, "private", False):
            cmd += ["--is-only-self", "1"]
        if getattr(opts, "dtime", None):
            cmd += ["--dtime", str(opts.dtime)]
        if getattr(opts, "line", None):
            cmd += ["--line", opts.line]
        cmd.append(str(material.video))

        if opts.dry_run:
            print("  [dry-run]", " ".join(f"'{c}'" if (" " in c or "{" in c) else c for c in cmd))
            return {"bvid": "(dry-run)", "title": meta["title"]}

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        captured = []
        assert proc.stdout is not None
        for line in proc.stdout:
            captured.append(line)
            print("   ", line.rstrip())
        proc.wait()
        out = "".join(captured)
        if proc.returncode != 0 or "投稿成功" not in out:
            raise BilibiliError(f"biliup 退出码 {proc.returncode}")
        m = re.search(r"(BV[0-9A-Za-z]{10})", out)
        return {
            "bvid": m.group(1) if m else "",
            "title": meta["title"],
            "private": bool(getattr(opts, "private", False)),
            "at": datetime.now(timezone.utc).isoformat(),
        }

    # ---------- 对账 ----------

    def sync(self, materials: list[Material], state: dict) -> list[tuple[str, str]]:
        """拉取已发布稿件，按「标题以素材文件夹名结尾」匹配并标记。"""
        archives: list[dict] = []
        for pn in range(1, 40):
            data = self._member_api(
                f"/x/web/archives?status=pubed,not_pubed,is_pubing&pn={pn}&ps=50")
            batch = data.get("arc_audits") or []
            archives.extend(a["Archive"] for a in batch)
            if len(batch) < 50:
                break
        pstate = platform_state(state, self.name)
        matched: list[tuple[str, str]] = []
        for mat in materials:
            if mat.name in pstate["published"]:
                continue
            for ar in archives:
                if ar["title"].endswith(mat.name):
                    pstate["published"][mat.name] = {
                        "bvid": ar["bvid"], "title": ar["title"],
                        "source": "sync", "at": datetime.now(timezone.utc).isoformat(),
                    }
                    matched.append((mat.name, ar["bvid"]))
                    break
        return matched
