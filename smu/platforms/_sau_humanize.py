"""sau 拟人化运行时补丁（猴子补丁，不改 social-auto-upload 源码）。

在 sau 进程启动时 import 本模块即可生效——它补的是 sau 底层的 patchright，
不碰 sau 任何代码，所以 sau 升级不受影响。

两处补丁：
  1. BrowserType.launch / launch_persistent_context → 注入随机 slow_mo
     （每个浏览器动作前加 N 毫秒延迟，N 每次运行随机），让微操作不再机械等距。
  2. Page.wait_for_timeout(ms) → 把固定等待改成 ms*factor + 随机抖动，
     让步骤之间的停顿像人一样长短不一。

通过环境变量调参（毫秒/倍数）：
  SMU_SLOWMO_MIN / SMU_SLOWMO_MAX   每次运行的 slow_mo 取值区间，默认 0（关闭）
                                    —— slow_mo 会给每个微操作加延迟，可能搅乱动态 UI
                                       （下拉框/日期选择器），默认不开；需要时再调大。
  SMU_WAIT_FACTOR_MIN / _MAX        wait_for_timeout 的乘数区间，默认 1.2~2.2（主要拟人手段）
  SMU_WAIT_JITTER_MS                额外随机抖动上限（毫秒），默认 1200
关闭全部：SMU_HUMANIZE=0
"""

from __future__ import annotations

import os
import random
import sys


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def _install_tencent_storage_state(pa) -> None:
    """补丁 7：视频号 storage_state 保存时带上 indexed_db，避免会话信息漏存。

    实测视频号登录态主要在 localStorage(storage_state 默认就存)，少数情况设备令牌在
    IndexedDB。引擎原生 storage_state(path=...) 不带 indexed_db → 万一令牌在 IDB 会漏。
    这里让视频号进程的 storage_state 默认带 indexed_db=True，存全。恢复(new_context)时
    Playwright 见到文件里有 indexedDB 字段会自动灌回，无需改 new_context。

    注意：不再用持久化 profile —— 实测普通独立浏览器 + new_context(storage_state) 就能
    完整恢复会话，且没有「同 profile 目录并发 launch」的崩溃问题。
    """
    if os.environ.get("SMU_PLATFORM") != "shipinhao":
        return
    orig_ss = pa.BrowserContext.storage_state

    async def ss_with_idb(self, *args, **kwargs):
        kwargs.setdefault("indexed_db", True)
        return await orig_ss(self, *args, **kwargs)

    pa.BrowserContext.storage_state = ss_with_idb


def _install_xhs_semi_auto(pa) -> None:
    """补丁 6：小红书「半自动」——填好全部内容后停在发布按钮前，交人手点。

    小红书风控盯的是「行为模式」(只发不逛、精确守时)，不是浏览器指纹。所以全自动
    点发布最易触发「疑似脚本运营」预警。半自动：脚本把视频/标题/正文/话题/封面全
    准备好，最后一步发布留给真人——你顺手刷两下、亲手点发布，最像真人。

    仅当 SMU_PLATFORM=xiaohongshu 且即时发布(非定时)时生效。需在真终端运行(能读
    键盘)；非交互环境(cron)下如实抛错，绝不假装成功。
    """
    if os.environ.get("SMU_PLATFORM") != "xiaohongshu":
        return
    try:
        from uploader.xiaohongshu_uploader.main import (
            XiaoHongShuVideo, xiaohongshu_logger,
            XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED, XHS_PUBLISH_VIDEO_URL,
        )
    except Exception:
        return

    orig_upload_content = XiaoHongShuVideo.upload_video_content

    async def semi_auto_upload_content(self, page):
        import asyncio
        # 定时发布走原逻辑(无需真人在场)；只半自动化「即时发布」
        if getattr(self, "publish_strategy", None) == XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED:
            return await orig_upload_content(self, page)

        xiaohongshu_logger.info("🧍 [半自动] 上传视频 + 填好标题/正文/话题/封面，发布留给你手点")
        await page.goto(XHS_PUBLISH_VIDEO_URL)
        await page.wait_for_url(XHS_PUBLISH_VIDEO_URL)
        await page.locator("div[class^='upload-content'] input[class='upload-input']").set_input_files(self.file_path)

        # 等视频上传完(标题框出现即可编辑)
        for _ in range(120):
            title_box = page.locator('input[placeholder*="填写标题"]')
            if await title_box.count() and await title_box.is_visible():
                break
            await asyncio.sleep(2)
        xiaohongshu_logger.success("🥳 [半自动] 视频已传完，开始填内容")

        await self.fill_meta(page)
        await self.set_thumbnail(page, self.thumbnail_path)
        await self.check_original_declaration(page)

        # —— 停在发布按钮前，交人手点 ——
        if not sys.stdin or not sys.stdin.isatty():
            raise RuntimeError(
                "小红书半自动需要真终端手动点发布。当前不是交互终端(疑似 cron)，"
                "已中止——请在终端手动运行 smu upload 发小红书，别用定时任务。")
        print("\n" + "=" * 56, file=sys.stderr)
        print("🖐  小红书半自动：内容已填好，请在浏览器里——", file=sys.stderr)
        print("    1) 随手刷两下信息流/看下通知(降低脚本特征)", file=sys.stderr)
        print("    2) 检查标题/正文/封面无误", file=sys.stderr)
        print("    3) 亲手点「发布」按钮", file=sys.stderr)
        print("    完成后回到这里按【回车】，我来核对结果并收尾。", file=sys.stderr)
        print("=" * 56, file=sys.stderr)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sys.stdin.readline)

        # 核对是否真发布成功：跳到 publish/success 或发布页已消失才算数
        for _ in range(10):
            if "publish/success" in page.url:
                xiaohongshu_logger.success("🥳 [半自动] 检测到发布成功页")
                return
            await asyncio.sleep(1)
        # 没跳成功页：再给一次机会
        print("⚠️ 还没检测到发布成功页。若已发布请忽略；否则点完发布再按回车。", file=sys.stderr)
        await loop.run_in_executor(None, sys.stdin.readline)
        if "publish/success" in page.url or "/publish/publish" not in page.url:
            xiaohongshu_logger.success("🥳 [半自动] 已离开发布页，按成功处理")
            return
        raise RuntimeError("小红书半自动：仍停留在发布页，未检测到发布成功，按失败处理")

    XiaoHongShuVideo.upload_video_content = semi_auto_upload_content


def install() -> None:
    if os.environ.get("SMU_HUMANIZE", "1") == "0":
        return
    try:
        import patchright.async_api as pa
    except Exception:
        return

    # slow_mo 默认关（0）：太激进会搅乱动态 UI。需要时设环境变量开启。
    slowmo = random.uniform(_envf("SMU_SLOWMO_MIN", 0), _envf("SMU_SLOWMO_MAX", 0))
    wf_min = _envf("SMU_WAIT_FACTOR_MIN", 1.2)
    wf_max = _envf("SMU_WAIT_FACTOR_MAX", 2.2)
    jitter = _envf("SMU_WAIT_JITTER_MS", 1200)

    # ---- 补丁 1：launch / launch_persistent_context 注入 slow_mo（仅当 >0）----
    if slowmo > 0:
        for meth in ("launch", "launch_persistent_context"):
            orig = getattr(pa.BrowserType, meth, None)
            if orig is None:
                continue

            def make(orig_fn):
                async def wrapper(self, *args, **kwargs):
                    kwargs.setdefault("slow_mo", slowmo)
                    return await orig_fn(self, *args, **kwargs)
                return wrapper

            setattr(pa.BrowserType, meth, make(orig))

    # ---- 补丁 2：wait_for_timeout 随机化 ----
    orig_wait = pa.Page.wait_for_timeout

    async def humanized_wait(self, timeout):
        factor = random.uniform(wf_min, wf_max)
        extra = random.uniform(0, jitter)
        return await orig_wait(self, timeout * factor + extra)

    pa.Page.wait_for_timeout = humanized_wait

    # ---- 补丁 4：抖音自主声明（健壮版，可配类型，默认 sau 写死「个人观点」）----
    # 抖音发布必选「自主声明」。sau 原版点 .semi-radio 后直接点「确定」，但：
    #   ① Semi 单选项点外层常选不中；② 抖音「确定」未选时是 CSS 置灰、并非真 disabled，
    #   仍可点 → 弹窗关闭但声明没存上，sau 却记成功（实测草稿显示「请选择自主声明」）。
    # 这里整体替换：点选后用 input.is_checked() 校验真的选中，再点「确定」，
    # 关闭后还校验发布页那行已变成所选声明，否则如实报失败。
    decl = os.environ.get("SMU_DOUYIN_DECLARATION")
    if decl:
        try:
            from uploader.douyin_uploader.main import DouYinBaseUploader

            async def _dismiss_guide_overlay(page):
                """关掉抖音创作页的新功能引导浮层。每次开浏览器都可能弹，且会拦截 pointer
                events 挡住后续点击（实测把「自主声明」点击卡到 30s 超时）。无害幂等：
                找到「我知道了/知道了/下一步」这类引导按钮就点掉，找不到就跳过。"""
                labels = ["我知道了", "知道了", "我知道啦", "下一步", "跳过", "完成引导", "开始使用"]
                for _ in range(6):  # 引导可能多步,连点几轮直到没有
                    clicked = False
                    for lab in labels:
                        try:
                            btn = page.get_by_text(lab, exact=True).first
                            if await btn.count() and await btn.is_visible():
                                await btn.click(timeout=1500, force=True)
                                clicked = True
                                await page.wait_for_timeout(400)
                        except Exception:
                            continue
                    if not clicked:
                        break

            async def robust_declaration(self, page, declaration=decl):
                try:
                    await _dismiss_guide_overlay(page)   # 先清掉引导浮层,避免拦截点击
                    entry = page.get_by_text("请选择自主声明").first
                    await entry.wait_for(state="visible", timeout=8000)
                    await entry.click()
                    dialog = page.locator(".semi-modal-content").filter(
                        has_text="对作品内容添加声明").first
                    await dialog.wait_for(state="visible", timeout=8000)

                    row = dialog.locator(".semi-radio").filter(has_text=declaration).first
                    await row.wait_for(state="visible", timeout=6000)
                    radio_input = row.locator("input").first

                    checked = False
                    for _ in range(4):
                        try:
                            checked = await radio_input.is_checked()
                        except Exception:
                            checked = False
                        if checked:
                            break
                        # 轮流尝试几种点选方式
                        for target in (row,
                                       row.locator(".semi-radio-inner").first,
                                       dialog.get_by_text(declaration, exact=True).first):
                            try:
                                await target.click(timeout=2500, force=True)
                                if await radio_input.is_checked():
                                    checked = True
                                    break
                            except Exception:
                                continue
                        if checked:
                            break
                    if not checked:
                        raise RuntimeError(f"单选项未选中：{declaration}")

                    ok = dialog.get_by_role("button", name="确定")
                    await ok.click(timeout=6000)
                    await dialog.wait_for(state="hidden", timeout=6000)
                    # 校验发布页那行已变为所选声明（不再是占位「请选择自主声明」）
                    await page.get_by_text(declaration, exact=True).first.wait_for(timeout=5000)
                    print(f"[smu] 自主声明已选并校验「{declaration}」")
                except Exception as exc:
                    print(f"[smu] 自主声明设置失败：{exc}")

            DouYinBaseUploader.set_self_declaration = robust_declaration

            # 封面步骤(set_thumbnail)在声明之前,引导浮层最早在这一步挡住点击
            # (用户实测:浮层弹出→封面「完成」点不动)。给封面也包一层:先关浮层再走原逻辑。
            _orig_set_thumbnail = getattr(DouYinBaseUploader, "set_thumbnail", None)
            if _orig_set_thumbnail is not None:
                async def set_thumbnail_with_dismiss(self, page, _orig=_orig_set_thumbnail):
                    try:
                        await _dismiss_guide_overlay(page)
                    except Exception:
                        pass
                    return await _orig(self, page)
                DouYinBaseUploader.set_thumbnail = set_thumbnail_with_dismiss
        except Exception:
            pass

    # ---- 补丁 3（macOS 兼容）：Control+A 全选 → Meta+A ----
    # sau 是按 Windows/Linux 写的，用 Control+KeyA 全选输入框（比如设定时时清空日期）。
    # macOS 全选是 Cmd(Meta)+A，否则选不中，会导致「定时<2小时」等 bug。
    if sys.platform == "darwin":
        orig_press = pa.Keyboard.press

        async def mac_press(self, key, *args, **kwargs):
            if isinstance(key, str) and key.lower().replace("control", "ctrl") in ("ctrl+a", "ctrl+keya"):
                key = "Meta+KeyA"
            return await orig_press(self, key, *args, **kwargs)

        pa.Keyboard.press = mac_press

    # ---- 补丁 5：小红书跳过 stealth.min.js 注入 ----
    # sau 给每个 context 注入 2024 版 puppeteer-extra stealth.min.js，但它和 Patchright
    # 自带的 CDP 层反检测「互相拆台」：add_init_script 的注入方式本身会重新引入可检测痕迹，
    # 且这套老 stealth 的特征早被小红书风控指纹化 → 注入比不注入更易被标。
    # 只对小红书跳过（SMU_PLATFORM=xiaohongshu），抖音/视频号维持原状不动。
    if os.environ.get("SMU_PLATFORM") == "xiaohongshu":
        orig_add_init = pa.BrowserContext.add_init_script

        async def skip_stealth_init(self, script=None, *, path=None, **kwargs):
            p = str(path or "")
            if "stealth" in p.lower():
                print("[smu] 小红书：跳过 stealth.min.js 注入（与 Patchright 反检测冲突）")
                return None
            return await orig_add_init(self, script, path=path, **kwargs)

        pa.BrowserContext.add_init_script = skip_stealth_init

    # ---- 补丁 6 + 7 见下（小红书半自动 / 视频号 storage_state 存全）----
    _install_xhs_semi_auto(pa)
    _install_tencent_storage_state(pa)


install()
