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
from .state import STATE_FILE, load_state, platform_state, save_state

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
_BILIBILI_INTERVAL = (30, 90)   # B站 API 上传，间隔可短


def _profile(args) -> dict:
    return _PROFILES.get(getattr(args, "profile", None) or "steady", _PROFILES["steady"])


def resolve_interval(args) -> tuple[int, int]:
    """返回 (最小秒, 最大秒)。优先命令行 --min/--max，否则 B站用快档、其它用 profile 档位。"""
    lo = getattr(args, "min_interval", None)
    hi = getattr(args, "max_interval", None)
    if lo is None or hi is None:
        if args.platform == "bilibili":
            d_lo, d_hi = _BILIBILI_INTERVAL
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
    print(f"素材 {len(mats)} 个 | 已投稿 {len(done)} | 待投稿 {len(pending)}")
    if pending:
        nxt = pending[0]
        print(f"下一个待投：{'%02d' % nxt.order if nxt.order is not None else ''} {nxt.name}")


def cmd_login(args) -> None:
    if getattr(args, "account", None):
        os.environ["SMU_ACCOUNT"] = args.account
    get_platform(args.platform).login()


def cmd_renew(args) -> None:
    get_platform("bilibili").renew()


def cmd_sync(args) -> None:
    mats = M.scan(args.dir)
    platform = get_platform(args.platform)
    state = load_state()
    matched = platform.sync(mats, state)
    save_state(state)
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
    state = load_state()
    published = platform_state(state, args.platform)["published"]
    for m in picked:
        if args.unmark:
            published.pop(m.name, None)
            print(f"  ↩️ 取消标记 {m.name}")
        else:
            published.setdefault(m.name, {
                "bvid": "", "title": m.name, "source": "manual",
                "at": datetime.now(timezone.utc).isoformat()})
            print(f"  ✅ 标记已投稿 {m.name}")
    save_state(state)


def cmd_upload(args) -> None:
    apply_dir_config(args)
    mats = M.scan(args.dir)
    platform = get_platform(args.platform)
    state = load_state()
    published = platform_state(state, args.platform)["published"]

    if args.all:
        targets = [m for m in mats if m.name not in published]
    elif args.items:
        targets = M.select(mats, args.items)
        already = [m.name for m in targets if m.name in published]
        if already and not args.force:
            fail(f"已投稿过（--force 可重投）：{', '.join(already)}")
    else:
        fail("请指定素材（序号/范围/--all），如：smu upload <目录> 11-20")
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
            published[mat.name] = record
            save_state(state)
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

    p = sub.add_parser("mark", help="手动标记已投稿/取消标记")
    add_common(p)
    p.add_argument("items", nargs="+", help="序号/范围/文件夹名，如 1-10")
    p.add_argument("--unmark", action="store_true")
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
    p.add_argument("--schedule", help="抖音/小红书定时发布：格式 'YYYY-MM-DD HH:MM'")
    # 拟人化随机间隔（不传则按平台默认：B站30~90s，抖音/小红书300~720s）
    p.add_argument("--min-interval", type=int, default=None, help="视频间最小间隔秒数")
    p.add_argument("--max-interval", type=int, default=None, help="视频间最大间隔秒数")
    # 发布档位（激进/稳健/保守）：一档配间隔 + 每日上限，仅作用于浏览器平台
    p.add_argument("--profile", choices=["aggressive", "steady", "conservative"], default="steady",
                   help="发布档位：aggressive(2~5分/日20) / steady(5~12分/日10,默认) / conservative(10~20分/日5)")
    p.add_argument("--no-daily-cap", action="store_true", help="解除每日上限")
    p.add_argument("--dry-run", action="store_true", help="只打印命令不上传")
    p.set_defaults(func=cmd_upload)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
