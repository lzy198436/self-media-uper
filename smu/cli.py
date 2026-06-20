"""smu — 自媒体批量投稿 CLI。

  smu scan   <素材目录>                      扫描素材并显示投稿状态
  smu status <素材目录>                      已投/待投统计 + 下一个待投
  smu login  [--platform bilibili]           扫码登录（真终端运行）
  smu renew                                  刷新B站登录态
  smu sync   <素材目录>                      拉取已发布稿件自动对账标记
  smu mark   <素材目录> 1-10 [--unmark]      手动标记（不）已投稿
  smu upload <素材目录> 11 12 / 11-20 / --all [--private] [--dry-run]

每个素材目录可放 smu.json 覆盖默认参数，例如：
  {"title_prefix": "【2026法考邪修流（民诉）】", "topic": "bilibili法考季",
   "ensure_tags": ["法考邪修流", "2026法考备考"]}
"""

from __future__ import annotations

import argparse
import json
import sys
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from . import materials as M
from .platforms import get_platform
from .state import atomic_update, load_state, platform_state

# 中性默认值：具体项目的标题前缀/话题/固定标签放素材目录的 smu.json，不写死在代码里。
DEFAULTS = {
    "title_prefix": "",
    "topic": "",
    "ensure_tags": [],
}

# 发布档位（借鉴 MatrixFlow 的激进/稳健/保守三档）：一档配一套随机间隔 + 每日上限。
# 只作用于浏览器平台（抖音/小红书/视频号/快手）；B站走 API，单列更快的间隔。
_PROFILES = {
    "aggressive":   {"interval": (120, 300),  "daily_cap": 20},   # 激进：2~5 分钟，日上限 20
    "steady":       {"interval": (300, 720),  "daily_cap": 10},   # 稳健（默认）：5~12 分钟，10
    "conservative": {"interval": (600, 1200), "daily_cap": 5},    # 保守：10~20 分钟，5
}
# B站 API 上传间隔。原为 3~8 分钟防集中批量,用户实测间隔不影响播放量,改回 1~2 分钟提速。
_BILIBILI_INTERVAL = (60, 120)

# 各平台默认发布间隔 60-120 秒(用户要求:发完一条 1-2 分钟随机就发下一条,不再等 10 分钟)。
# 注意:抖音/视频号是浏览器自动化,1-2 分钟连发"机器批量"特征比 B站 API 高,是用户明确选择。
# 想放慢仍可用 --min-interval/--max-interval 覆盖。
_PLATFORM_DEFAULT_INTERVAL = {
    "bilibili":    (60, 120),
    "douyin":      (60, 120),
    "shipinhao":   (60, 120),
    "xiaohongshu": (60, 120),
}

# 各平台安全节奏（按 2026 纯内容/不带货平台规则内置）：编排默认按此守安全线。
#   interval=条间随机间隔秒；daily_cap=单日稳妥上限(0=无硬日限但别集中)；soft=单次编排软上限
_PLATFORM_LIMITS = {
    "bilibili":  {"interval": (60, 120),   "daily_cap": 0,  "soft": 8},
    "douyin":    {"interval": (60, 120),   "daily_cap": 10, "soft": 10},
    "shipinhao": {"interval": (60, 120),   "daily_cap": 8,  "soft": 8},
}


def _profile(args) -> dict:
    return _PROFILES.get(getattr(args, "profile", None) or "steady", _PROFILES["steady"])


def resolve_interval(args) -> tuple[int, int]:
    """返回 (最小秒, 最大秒)。优先命令行 --min/--max；否则用平台默认间隔(B站/抖音/视频号
    /小红书均 60-120 秒)，不在表里的平台回退 profile 档位。"""
    lo = getattr(args, "min_interval", None)
    hi = getattr(args, "max_interval", None)
    if lo is None or hi is None:
        if args.platform in _PLATFORM_DEFAULT_INTERVAL:
            d_lo, d_hi = _PLATFORM_DEFAULT_INTERVAL[args.platform]
        else:
            d_lo, d_hi = _profile(args)["interval"]
        lo = d_lo if lo is None else lo
        hi = d_hi if hi is None else hi
    return (min(lo, hi), max(lo, hi))


def published_today(state: dict, platform: str) -> int:
    """统计今天（本地日期）该平台经 smu 发布的条数，用于每日上限。"""
    from datetime import datetime, timezone
    today = datetime.now().astimezone().date()
    pub = platform_state(state, platform)["published"]
    n = 0
    for rec in pub.values():
        if not isinstance(rec, dict) or rec.get("source") != "smu":
            continue
        at = rec.get("at") or rec.get("uploaded_at")
        if not at:
            continue
        try:
            d = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().date()
            if d == today:
                n += 1
        except ValueError:
            continue
    return n


def fail(msg: str):
    sys.stdout.flush()
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(1)


def _norm_title(s: str) -> str:
    """归一化标题用于查重匹配：去掉开头序号前缀(如 01_)，再去空白与常见标点。"""
    import re as _re
    s = _re.sub(r"^\s*\d+[_\-]\s*", "", s or "")   # 素材名的 NN_ 前缀平台标题没有
    return _re.sub(r"[\s#【】\[\]()（）!！?？、,，.。:：~\-—_|]+", "", s)


def title_matches(name: str, title: str) -> bool:
    """素材名与平台标题是否指同一条：归一化后任一为另一方子串（容忍前缀钩子/截断），
    带最小长度护栏防误判。"""
    n, t = _norm_title(name), _norm_title(title)
    if len(n) < 4 or len(t) < 4:
        return n == t and bool(n)
    return n in t or t in n


def dir_config(root: Path) -> dict:
    cfg = dict(DEFAULTS)
    f = root / "smu.json"
    if f.is_file():
        cfg.update(json.loads(f.read_text(encoding="utf-8")))
    return cfg


def apply_dir_config(args) -> None:
    cfg = dir_config(args.dir)
    if args.title_prefix is None:
        args.title_prefix = cfg["title_prefix"]
    if args.topic is None:
        args.topic = cfg["topic"]
    args.ensure_tags = cfg["ensure_tags"]


def cmd_scan(args) -> None:
    mats = M.scan(args.dir)
    state = load_state()
    published = platform_state(state, args.platform)["published"]
    print(f"素材目录：{args.dir}（{len(mats)} 个）  平台：{args.platform}")
    n_warn = 0
    for m in mats:
        mark = "✅" if m.name in published else "· "
        missing = m.missing_for_bilibili()
        note = f"  ⚠️缺{','.join(missing)}" if missing else ""
        n_warn += bool(missing or m.notes)
        bvid = published.get(m.name, {}).get("bvid", "")
        print(f"  {mark} {m.name}{note}  {bvid}")
        for n in m.notes:
            print(f"       ↳ {n}")
    if not mats:
        print("⚠️ 没有识别到任何素材。支持两种布局：①每素材一个子文件夹 ②视频直接平铺在目录里；"
              "视频格式 mp4/mov/mkv/webm。目录约定详见 README。")
    elif n_warn:
        print(f"\n⚠️ {n_warn} 个素材有缺件或宽容识别提示；缺关键件的素材默认会被 upload 拒绝"
              "（--allow-incomplete 可放行）。")


def cmd_status(args) -> None:
    mats = M.scan(args.dir)
    state = load_state()
    published = platform_state(state, args.platform)["published"]
    done = [m for m in mats if m.name in published]
    pending = [m for m in mats if m.name not in published]
    # 两个口径分开报，根治「state 17 vs status 15」的困惑：
    #   命中 = 本目录素材里已投稿的（status 关心的）
    #   记录 = state.published 总条数（可能 > 命中：含孤儿——曾投但目录里已无对应素材）
    mat_names = {m.name for m in mats}
    orphans = [name for name in published if name not in mat_names]
    print(f"素材 {len(mats)} 个 | 本目录命中已投稿 {len(done)} | 待投稿 {len(pending)}")
    print(f"state 记录 {len(published)} 条（命中 {len(done)} + 孤儿 {len(orphans)}）")
    if orphans:
        print(f"⚠️ {len(orphans)} 条孤儿记录（state 有、本目录无对应素材，不计入待投/命中）：")
        for name in orphans:
            rec = published[name]
            src = rec.get("source", "?") if isinstance(rec, dict) else "?"
            vid = (rec.get("bvid") or rec.get("id") or "") if isinstance(rec, dict) else ""
            print(f"     · {name}  [{src}]  {vid}")
    if pending:
        nxt = pending[0]
        print(f"下一个待投：{'%02d' % nxt.order if nxt.order is not None else ''} {nxt.name}")
    # 小红书额外显示图文讲义维度（与视频各记各的）
    if args.platform == "xiaohongshu":
        from .state import handout_state
        ho = handout_state(state, args.platform)
        ho_done = [m for m in mats if m.name in ho]
        print(f"图文讲义：已发 {len(ho_done)} | 待发 {len(mats) - len(ho_done)}")


def cmd_stats(args) -> None:
    from . import stats as S
    if args.action == "pull":
        try:
            n = S.pull(args.platform, args.account)
        except S.StatsError as e:
            fail(str(e))
        print(f"✅ {args.platform} 采集 {n} 条视频数据 → {S._store(args.platform)}")
        return
    # show
    snap = S.latest_snapshot(args.platform)
    if not snap:
        print(f"{args.platform} 还没有数据，先跑：smu stats pull {args.platform}")
        return
    snap.sort(key=lambda r: r.get("play", 0), reverse=True)
    tot = {k: sum(r.get(k, 0) for r in snap) for k in ("play", "like", "comment", "share", "collect")}
    print(f"{args.platform} · 最近快照 {snap[0]['fetched_at'][:19]} · {len(snap)} 条视频")
    print(f"合计：播放 {tot['play']} | 赞 {tot['like']} | 评论 {tot['comment']} | 分享 {tot['share']} | 收藏 {tot['collect']}")
    print("播放 Top:")
    for r in snap[:args.top]:
        print(f"  {r.get('play', 0):>7} 播放 · {r.get('like', 0)}赞 · {r['title']}")


def cmd_login(args) -> None:
    if getattr(args, "account", None):
        os.environ["SMU_ACCOUNT"] = args.account
    get_platform(args.platform).login()


def cmd_renew(args) -> None:
    get_platform("bilibili").renew()


def cmd_sync(args) -> None:
    mats = M.scan(args.dir)
    platform = get_platform(args.platform)
    matched: list = []
    # sync 含网络拉取，整体走 atomic_update：锁内 reload→sync(改 state)→写回，
    # 不会被并发的 upload 覆盖(sync 是低频运维，持锁期间阻塞 upload 可接受)。
    def _sync(state):
        matched.extend(platform.sync(mats, state))
    atomic_update(_sync)
    if matched:
        print(f"对账完成，新标记 {len(matched)} 个已投稿：")
        for name, vid in matched:
            print(f"  ✅ {name}  {vid}")
    else:
        print("对账完成，没有新增匹配")
    cmd_status(args)


def cmd_mark(args) -> None:
    mats = M.scan(args.dir)
    picked = M.select(mats, args.items)
    names = [m.name for m in picked]
    is_handout = getattr(args, "handout", False)
    kind = "讲义" if is_handout else "投稿"

    def _mark(state):
        if is_handout:
            from .state import handout_state
            book = handout_state(state, args.platform)
        else:
            book = platform_state(state, args.platform)["published"]
        for name in names:
            if args.unmark:
                book.pop(name, None)
                print(f"  ↩️ 取消标记{kind} {name}")
            else:
                book.setdefault(name, {
                    "note": "图文讲义" if is_handout else "",
                    "bvid": "", "title": name, "source": "manual",
                    "at": datetime.now(timezone.utc).isoformat()})
                print(f"  ✅ 标记已{kind} {name}")
    atomic_update(_mark)


def cmd_upload(args) -> None:
    apply_dir_config(args)
    mats = M.scan(args.dir)
    # 小红书视频默认走扩展(日常浏览器，风控低)；--engine sau 一键回退到 patchright。
    engine = getattr(args, "engine", None)
    if engine is None:
        engine = "extension" if args.platform == "xiaohongshu" else "sau"
    platform = get_platform(args.platform, engine)
    state = load_state()
    published = platform_state(state, args.platform)["published"]

    if args.all:
        targets = [m for m in mats if m.name not in published]
    elif args.items:
        targets = M.select(mats, args.items)
        already = [m.name for m in targets if m.name in published]
        if already and not args.force:
            fail(f"已投稿过（--force 可重投）：{', '.join(already)}")
    elif getattr(args, "count", None):
        # --count N：取该平台自己待发的前 N 条（编排/连发用，各平台进度独立）
        pending = [m for m in mats if m.name not in published]
        targets = pending[:args.count]
    else:
        fail("请指定素材（序号/范围/--all/--count），如：smu upload <目录> 11-20")
    if not targets:
        print("没有待投稿的素材")
        return

    # ---- 每日上限（按 profile 档位）：浏览器平台防风控，B站 API 不限 ----
    if args.platform != "bilibili" and not args.dry_run:
        cap = _profile(args)["daily_cap"]
        done_today = published_today(state, args.platform)
        remaining = cap - done_today
        if remaining <= 0:
            fail(f"今日 {args.platform} 已发 {done_today} 条，达到「{args.profile or 'steady'}」档每日上限 {cap}。"
                 f"明天再发，或换 --profile aggressive，或 --no-daily-cap 强制。")
        if len(targets) > remaining and not args.no_daily_cap:
            print(f"⚠️ 今日已发 {done_today} 条，「{args.profile or 'steady'}」档上限 {cap}，"
                  f"本次只发前 {remaining} 条（剩余明天再发，或 --no-daily-cap 解除）")
            targets = targets[:remaining]

    # ---- 上传前预检：逐素材展示将提交的内容，缺关键件默认拒绝 ----
    prof = "" if args.platform == "bilibili" else f"，档位：{args.profile or 'steady'}"
    print(f"预检 {len(targets)} 个素材 → {args.platform}{prof}"
          + ("（仅自己可见）" if args.private else "")
          + f"，标题前缀：{args.title_prefix}")
    incomplete: list[str] = []
    for m in targets:
        meta = platform.build_meta(m, args) if hasattr(platform, "build_meta") else {}
        missing = m.missing_for_bilibili()
        head = "❌" if missing else "✓ "
        print(f"\n{head} {m.name}" + (f"  ⚠️缺{','.join(missing)}" if missing else ""))
        for n in m.notes:
            print(f"     ↳ {n}")
        print(f"     视频: {m.video.name if m.video else '（无）'}")
        print(f"     封面: 16:9 {'✓ ' + m.cover169.name if m.cover169 else '✗ B站自动截帧'}"
              f" | 4:3 {'✓ ' + m.cover43.name if m.cover43 else '✗ 不设'}")
        if meta:
            desc_head = (meta.get("desc") or "").split("\n")[0][:50]
            print(f"     标题: {meta.get('title', '')}")
            print(f"     简介: {desc_head + '…' if meta.get('desc') else '（空）'}")
            print(f"     标签: {','.join(meta.get('tags', []))}")
        if missing:
            incomplete.append(m.name)
    if incomplete and not args.allow_incomplete:
        fail(f"{len(incomplete)} 个素材缺关键件（视频/封面/B站文案），已全部拒绝上传。\n"
             f"  缺件素材：{' '.join(incomplete)}\n"
             f"  补齐素材后重试，或确认接受降级（无封面=B站截帧、无文案=空简介）再加 --allow-incomplete。")
    targets = [m for m in targets if m.video]

    # ---- pre-publish verify：发布前去平台搜标题查重（治本，防 state 漂移导致的重复发）----
    # 一次性拉平台已发列表（浏览器拦截类太重，不逐条查），命中的跳过并回写 state。
    if not args.dry_run and not getattr(args, "no_verify", False) and targets:
        try:
            remote = platform.list_published(args)
        except NotImplementedError:
            remote = None
            print(f"     ℹ️ {args.platform} 暂不支持发布前查重，跳过（仍按本地 state 判重）")
        except Exception as e:
            remote = None
            print(f"     ⚠️ 发布前查重失败（按未发处理，继续）：{e}", file=sys.stderr)
        if remote is not None:
            print(f"🔎 发布前查重：平台已有 {len(remote)} 条，比对 {len(targets)} 个待投…")
            survivors = []
            for m in targets:
                hit = next((r for r in remote if title_matches(m.name, r.get("title", ""))), None)
                if hit:
                    print(f"     ⏭️ 跳过 {m.name}：平台已存在「{hit.get('title', '')[:30]}」{hit.get('id', '')}")
                    rec = {
                        "id": hit.get("id", ""), "title": hit.get("title", ""),
                        "source": "verify", "at": datetime.now(timezone.utc).isoformat()}
                    def _merge(disk, _rec=rec, _name=m.name):
                        platform_state(disk, args.platform)["published"][_name] = _rec
                    state = atomic_update(_merge)
                    published = platform_state(state, args.platform)["published"]
                else:
                    survivors.append(m)
            skipped = len(targets) - len(survivors)
            if skipped:
                print(f"     查重命中 {skipped} 条已存在，本次实发 {len(survivors)} 条")
            targets = survivors
    if not targets:
        print("查重后没有待投稿的素材（平台已全部存在）")
        return

    failed = []
    for i, mat in enumerate(targets):
        print(f"\n[{i + 1}/{len(targets)}] {mat.name}")
        try:
            record = platform.publish(mat, state, args)
        except Exception as e:
            failed.append(mat.name)
            print(f"    ❌ 失败：{e}", file=sys.stderr)
            record = None
        if record and not args.dry_run:
            record["source"] = "smu"
            # 并发安全：锁内 reload 最新→只合并这条 published(+本进程新增的 topics 缓存)→写回，
            # 不再整盘覆盖。多平台并发跑时各自的记录都保得住(根治 B站丢 18 条)。
            def _merge(disk, _rec=record, _name=mat.name):
                dp = platform_state(disk, args.platform)
                dp["published"][_name] = _rec
                mem_topics = platform_state(state, args.platform).get("topics")
                if mem_topics:
                    dp.setdefault("topics", {}).update(mem_topics)
            state = atomic_update(_merge)
            published = platform_state(state, args.platform)["published"]
            ident = record.get("bvid") or record.get("id") or ""
            sched = f"（定时 {record['scheduled']}）" if record.get("scheduled") else ""
            print(f"    ✅ 投稿成功 {ident}{sched}")
        if i < len(targets) - 1 and not args.dry_run:
            lo, hi = resolve_interval(args)
            if hi > 0:
                wait = random.randint(lo, hi)
                m, s = divmod(wait, 60)
                print(f"    …随机等待 {f'{m}分{s}秒' if m else f'{s}秒'}（拟人化间隔，避免规律节奏被风控）")
                time.sleep(wait)

    print(f"\n完成：成功 {len(targets) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败列表：", " ".join(failed))
        sys.exit(1)


def cmd_handout(args) -> None:
    """发小红书图文讲义（封面图 + PDF附件），半自动停发布前交人手点。与视频各记各的 state。"""
    if args.platform != "xiaohongshu":
        fail("handout 目前只支持 --platform xiaohongshu")
    from .platforms.xhs_handout import HandoutError, publish_handout
    from .state import handout_state

    mats = M.scan(args.dir)
    state = load_state()
    done = handout_state(state, args.platform)

    if args.all:
        targets = [m for m in mats if m.name not in done]
    elif args.items:
        targets = M.select(mats, args.items)
        already = [m.name for m in targets if m.name in done]
        if already and not args.force:
            fail(f"已发过讲义（--force 可重发）：{', '.join(already)}")
    else:
        fail("请指定素材（序号/范围/--all），如：smu handout <目录> 14")
    if not targets:
        print("没有待发讲义的素材")
        return

    # ---- 预检：展示封面/PDF/文案，缺关键件默认拒绝 ----
    print(f"预检 {len(targets)} 个素材 → 小红书图文讲义（封面图+PDF）")
    print("⚠️ 前置：浏览器需装 XHS Bridge 扩展，且「有且仅有一个」已登录的 creator.xiaohongshu.com 标签页")
    incomplete: list[str] = []
    for m in targets:
        missing = m.missing_for_handout()
        head = "❌" if missing else "✓ "
        print(f"\n{head} {m.name}" + (f"  ⚠️缺{','.join(missing)}" if missing else ""))
        print(f"     封面: {m.cover_vertical.name if m.cover_vertical else '✗'}")
        print(f"     PDF : {m.handout_pdf.name if m.handout_pdf else '✗'}")
        print(f"     文案: {m.copies['xiaohongshu'].name if 'xiaohongshu' in m.copies else '✗'}")
        if missing:
            incomplete.append(m.name)
    if incomplete and not args.allow_incomplete:
        fail(f"{len(incomplete)} 个素材缺关键件（封面/PDF/小红书文案），已全部拒绝。\n"
             f"  缺件：{' '.join(incomplete)}")
    targets = [m for m in targets if not m.missing_for_handout()]

    failed = []
    for i, m in enumerate(targets):
        print(f"\n[{i + 1}/{len(targets)}] {m.name}")
        try:
            rec = publish_handout(m, args)
        except HandoutError as e:
            failed.append(m.name)
            print(f"    ❌ 失败：{e}", file=sys.stderr)
            continue
        if not args.dry_run:
            rec["source"] = "smu"
            def _merge(disk, _rec=rec, _name=m.name):
                from .state import handout_state as _hs
                _hs(disk, args.platform)[_name] = _rec
            atomic_update(_merge)
            print(f"    ✅ 讲义已发布")

    print(f"\n完成：成功 {len(targets) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败列表：", " ".join(failed))
        sys.exit(1)


# 跨平台串行编排的默认顺序（B站快→抖音→视频号）。小红书不在内：它半自动要人手点，单独发。
_PUBLISH_ALL_ORDER = ["bilibili", "douyin", "shipinhao"]


def _make_upload_args(base, platform: str) -> argparse.Namespace:
    """为某平台构造 cmd_upload 用的 args，套用该平台的安全节奏（间隔）。"""
    lim = _PLATFORM_LIMITS.get(platform, {})
    lo, hi = lim.get("interval", (300, 720))
    return argparse.Namespace(
        dir=base.dir, platform=platform,
        items=[] if base.all else [], all=base.all,
        count=getattr(base, "count", None),
        force=False, allow_incomplete=getattr(base, "allow_incomplete", False),
        title_prefix=None, topic=None, ensure_tags=[],
        tid=124, human_type2=1010, ai_statement=True, private=False,
        dtime=None, line=None, account="main", engine=None,
        schedule=None, category=None,
        min_interval=lo, max_interval=hi,
        profile=getattr(base, "profile", None) or "steady",
        no_daily_cap=False, no_verify=False, dry_run=base.dry_run,
    )


def cmd_publish_all(args) -> None:
    """跨平台串行编排：B站→抖音→视频号，各从自己待发开始，复用 cmd_upload。
    串行避免抢焦点/抢 state；某平台失败跳过继续；小红书不含（半自动单独发）。"""
    order = (args.platforms.split(",") if getattr(args, "platforms", None)
             else list(_PUBLISH_ALL_ORDER))
    order = [p for p in order if p != "xiaohongshu"]   # 强制排除小红书
    cnt = getattr(args, "count", None)
    if not args.all and not cnt:
        cnt = 1   # 默认每平台发 1 条
    mats = M.scan(args.dir)

    print(f"📋 跨平台编排：{' → '.join(order)}（小红书不含，半自动单独发）")
    print(f"   每平台发{'全部待发' if args.all else f'前 {cnt} 条'}，串行（一个跑完再下一个）\n")

    results = {}
    for platform in order:
        print(f"\n{'='*56}\n▶ {platform}\n{'='*56}")
        pa = _make_upload_args(args, platform)
        if not args.all:
            # 取该平台自己待发的前 cnt 条序号
            state = load_state()
            done = platform_state(state, platform)["published"]
            pending = [m for m in mats if m.name not in done]
            if not pending:
                print(f"  {platform} 没有待发素材，跳过")
                results[platform] = "无待发"
                continue
            picks = pending[:cnt]
            pa.items = [str(m.order) if m.order is not None else m.name for m in picks]
            pa.all = False
        try:
            cmd_upload(pa)
            results[platform] = "✅ 完成"
        except SystemExit:
            results[platform] = "❌ 失败（已跳过，继续下一平台）"
            print(f"  ⚠️ {platform} 发布失败，跳过继续", file=sys.stderr)
        except Exception as e:
            results[platform] = f"❌ 异常：{e}"
            print(f"  ⚠️ {platform} 异常：{e}，跳过继续", file=sys.stderr)

    print(f"\n{'='*56}\n📊 编排汇总")
    for p in order:
        print(f"   {p}: {results.get(p, '未执行')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="smu", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p, with_dir=True):
        if with_dir:
            p.add_argument("dir", type=Path, help="素材目录")
        p.add_argument("--platform", default="bilibili", help="平台，默认 bilibili")

    p = sub.add_parser("scan", help="扫描素材目录")
    add_common(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("status", help="投稿进度统计")
    add_common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("login", help="扫码登录")
    add_common(p, with_dir=False)
    p.add_argument("--account", help="账号标签（多账号区分，抖音/小红书用），默认 main")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("renew", help="刷新B站登录态")
    p.set_defaults(func=cmd_renew)

    p = sub.add_parser("sync", help="拉取已发布稿件自动对账")
    add_common(p)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("stats", help="采集/查看已发视频数据（播放/赞/评论）")
    p.add_argument("action", choices=["pull", "show"], help="pull=采集一次  show=看最近快照")
    p.add_argument("--platform", default="douyin", help="平台（douyin/bilibili 已支持）")
    p.add_argument("--account", default="main", help="账号标签")
    p.add_argument("--top", type=int, default=10, help="show 时显示播放 Top N")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("mark", help="手动标记已投稿/取消标记")
    add_common(p)
    p.add_argument("items", nargs="+", help="序号/范围/文件夹名，如 1-10")
    p.add_argument("--unmark", action="store_true")
    p.add_argument("--handout", action="store_true",
                   help="标记图文讲义维度(handout_published)而非视频，校准小红书讲义用")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("upload", help="投稿")
    add_common(p)
    p.add_argument("items", nargs="*", help="序号/范围/文件夹名，如 11 或 11-20")
    p.add_argument("--all", action="store_true", help="投全部未投稿素材")
    p.add_argument("--force", action="store_true", help="允许重投已标记素材")
    p.add_argument("--allow-incomplete", action="store_true",
                   help="允许缺封面/文案的素材降级上传（默认拒绝）")
    p.add_argument("--title-prefix", default=None, help="标题前缀（默认读目录 smu.json 或内置默认）")
    p.add_argument("--topic", default=None, help="参与话题（默认 bilibili法考季），传空串禁用")
    p.add_argument("--tid", type=int, default=124, help="旧分区 tid，默认 124 社科·法律·心理")
    p.add_argument("--human-type2", type=int, default=1010, help="新分区，默认 1010 知识")
    p.add_argument("--ai-statement", action=argparse.BooleanOptionalAction, default=True,
                   help="创作声明「含AI生成内容」（默认开）")
    p.add_argument("--private", action="store_true", help="仅自己可见（测试，仅B站）")
    p.add_argument("--dtime", type=int, help="B站定时发布：10位时间戳，距提交>4小时")
    p.add_argument("--line", help="B站上传线路 bda2/ws/qn 等")
    # 抖音/小红书等浏览器平台
    p.add_argument("--account", default="main", help="账号标签（抖音/小红书多账号区分），默认 main")
    p.add_argument("--engine", choices=["sau", "extension"], default=None,
                   help="发布引擎：sau(patchright新浏览器) / extension(日常浏览器扩展，风控低)。"
                        "小红书默认 extension，其它默认 sau")
    p.add_argument("--schedule", help="抖音/小红书定时发布：格式 'YYYY-MM-DD HH:MM'")
    p.add_argument("--category", help="视频号原创声明的原创类型（如 知识/教育），可选")
    # 拟人化随机间隔（不传则按平台默认：B站30~90s，抖音/小红书300~720s）
    p.add_argument("--min-interval", type=int, default=None, help="视频间最小间隔秒数")
    p.add_argument("--max-interval", type=int, default=None, help="视频间最大间隔秒数")
    # 发布档位（激进/稳健/保守）：一档配间隔 + 每日上限，仅作用于浏览器平台
    p.add_argument("--profile", choices=["aggressive", "steady", "conservative"], default="steady",
                   help="发布档位：aggressive(2~5分/日20) / steady(5~12分/日10,默认) / conservative(10~20分/日5)")
    p.add_argument("--no-daily-cap", action="store_true", help="解除每日上限")
    p.add_argument("--count", type=int, default=None,
                   help="发该平台自己待发的前 N 条（各平台进度独立，编排/连发用）")
    p.add_argument("--no-verify", action="store_true",
                   help="跳过发布前去平台查重（默认会查重，命中则跳过避免重复发）")
    p.add_argument("--dry-run", action="store_true", help="只打印命令不上传")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("publish-all", help="跨平台串行编排：B站→抖音→视频号（不含小红书）")
    p.add_argument("dir", type=Path, help="素材目录")
    p.add_argument("--count", type=int, default=None, help="每平台各发自己待发的前 N 条（默认 1）")
    p.add_argument("--all", action="store_true", help="每平台各发全部待发（受每日上限截断）")
    p.add_argument("--platforms", default=None,
                   help="覆盖默认顺序，逗号分隔（默认 bilibili,douyin,shipinhao；小红书强制排除）")
    p.add_argument("--profile", choices=["aggressive", "steady", "conservative"], default="steady",
                   help="浏览器平台发布档位（默认 steady）")
    p.add_argument("--allow-incomplete", action="store_true", help="允许缺件素材降级")
    p.add_argument("--dry-run", action="store_true", help="只预演不发")
    p.set_defaults(func=cmd_publish_all)

    p = sub.add_parser("handout", help="发小红书图文讲义（封面图+PDF），半自动手点发布")
    p.add_argument("dir", type=Path, help="素材目录")
    p.add_argument("items", nargs="*", help="序号/范围/文件夹名，如 14 或 14-20")
    p.add_argument("--platform", default="xiaohongshu", help="目前仅 xiaohongshu")
    p.add_argument("--all", action="store_true", help="发全部未发讲义的素材")
    p.add_argument("--force", action="store_true", help="允许重发已记录讲义的素材")
    p.add_argument("--allow-incomplete", action="store_true",
                   help="允许缺封面/PDF/文案的素材（默认拒绝）")
    p.add_argument("--dry-run", action="store_true", help="只打印将提交的内容不发布")
    p.set_defaults(func=cmd_handout)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
