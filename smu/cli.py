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
import time
from datetime import datetime, timezone
from pathlib import Path

from . import materials as M
from .platforms import get_platform
from .state import STATE_FILE, load_state, platform_state, save_state

DEFAULTS = {
    "title_prefix": "【2026法考邪修流（民诉）】",
    "topic": "bilibili法考季",
    "ensure_tags": ["法考邪修流", "2026法考备考"],
}


def fail(msg: str):
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
    for m in mats:
        mark = "✅" if m.name in published else "· "
        missing = m.missing_for_bilibili()
        note = f"  ⚠️缺{','.join(missing)}" if missing else ""
        bvid = published.get(m.name, {}).get("bvid", "")
        print(f"  {mark} {m.name}{note}  {bvid}")


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
    targets = [m for m in targets if m.video]
    if not targets:
        print("没有待投稿的素材")
        return

    print(f"待投稿 {len(targets)} 个 → {args.platform}"
          + ("（仅自己可见）" if args.private else "")
          + f"，标题前缀：{args.title_prefix}")
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
            print(f"    ✅ 投稿成功 {record.get('bvid', '')}")
        if i < len(targets) - 1 and not args.dry_run and args.interval > 0:
            print(f"    …等待 {args.interval}s（避免触发风控）")
            time.sleep(args.interval)

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
    p.add_argument("--title-prefix", default=None, help="标题前缀（默认读目录 smu.json 或内置默认）")
    p.add_argument("--topic", default=None, help="参与话题（默认 bilibili法考季），传空串禁用")
    p.add_argument("--tid", type=int, default=124, help="旧分区 tid，默认 124 社科·法律·心理")
    p.add_argument("--human-type2", type=int, default=1010, help="新分区，默认 1010 知识")
    p.add_argument("--ai-statement", action=argparse.BooleanOptionalAction, default=True,
                   help="创作声明「含AI生成内容」（默认开）")
    p.add_argument("--private", action="store_true", help="仅自己可见（测试）")
    p.add_argument("--dtime", type=int, help="定时发布：10位时间戳，距提交>4小时")
    p.add_argument("--line", help="上传线路 bda2/ws/qn 等")
    p.add_argument("--interval", type=int, default=30, help="批量间隔秒数，默认 30")
    p.add_argument("--dry-run", action="store_true", help="只打印命令不上传")
    p.set_defaults(func=cmd_upload)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
