---
name: self-media-uper
description: |
  当用户要把素材目录里的视频批量投稿/发布到 B站、抖音、小红书、快手、视频号时使用本 skill。
  触发示例：「投稿」「发B站/发抖音/发小红书」「继续投10个」「投稿进度怎么样」「从第11个开始投」。
  本 skill 调用 smu CLI 完成扫描、对账、批量投稿；标题/封面/文案/标签全部取自素材文件夹，
  各项目的标题前缀/话题/固定标签由素材目录下的 smu.json 提供（skill 不写死任何项目配置）。
  不应触发：制作视频/封面/文案（上游生产环节）、查询平台数据。
---

# self-media-uper：素材目录批量投稿

工具：`smu`（已在 PATH）。状态记录在 `~/.self-media-uper/state.json`，按「平台 + 素材文件夹名」防重复投稿。

支持平台：**B站**（biliup，API）、**抖音/小红书/快手/视频号**（social-auto-upload，隐身浏览器）。
平台用 `--platform` 指定，默认 bilibili。视频号平台名是 `shipinhao`（smu 内映射为 sau 的 `tencent`）。

## 第一步：确认素材目录（必须）

**永远先和用户确认素材目录，不要猜、不要用历史目录直接开干。**

- 用户没给目录 → 询问「这次投哪个素材目录？」，可列出候选帮用户选。
- 用户给了模糊名称 → 找到匹配目录后**复述完整路径让用户确认**。
- 目录确认后先跑 `smu scan <目录>` 检查素材完整性（缺封面/文案有 ⚠️，宽容识别依据用 ↳ 标注）。

## 目录不规范时：协商，不要硬投

scan 出现较多 ⚠️/↳、或扫出 0 个素材时，**不要直接 upload**：
1. 把问题归类汇总报给用户（缺件、识别歧义、平铺未分夹），附目录约定要点。
2. 确认处理方式：(a) 用户补齐/改名；(b) 你代为整理（动文件前列改名清单让用户确认）；
   (c) 接受降级 → upload 加 `--allow-incomplete`。
3. upload 的预检会把缺关键件的素材整批拦下，这是兜底，不要当日常流程。

## 第二步：检查登录态 + 对账

**在投稿前，先检查各平台 cookie 文件是否存在：**
```bash
ls ~/.self-media-uper/bilibili.cookies.json                          # B站
ls ~/.self-media-uper/engines/social-auto-upload/cookies/douyin_*.json    # 抖音
ls ~/.self-media-uper/engines/social-auto-upload/cookies/xiaohongshu_*.json # 小红书
ls ~/.self-media-uper/engines/social-auto-upload/cookies/kuaishou_*.json   # 快手
ls ~/.self-media-uper/engines/social-auto-upload/cookies/tencent_*.json    # 视频号
```

cookie 文件存在 ≠ cookie 有效（尤其是视频号，经常文件在但会话失效）。

```bash
smu sync   <目录>   # 仅B站：拉已发布稿件自动标记已投（手动投的也识别）
smu status <目录> --platform <平台>   # 已投/待投统计 + 下一个待投序号
```

## 第三步：投稿（公开发布前必须向用户确认）

```bash
smu upload <目录> 11-20                              # B站，投11到20
smu upload <目录> --all                              # B站，投全部未投的
smu upload <目录> 11 --platform douyin --account <号> \
           --schedule "2026-06-13 12:00"             # 抖音，定时发布
smu upload <目录> 11 --dry-run                       # 只看将提交的内容，不上传
```

规则：
- **公开投稿是对外发布，执行前必须把「平台 + 数量 + 起止序号」报给用户确认**；
  用户已明确说了数量/范围则直接执行。
- **间隔是随机的、按平台自动**：B站 30~90 秒；抖音/小红书/视频号/快手 5~12 分钟（防规律节奏被风控）。
- **单日上限**：B站不超 20 条；抖音/小红书/视频号/快手更严，**不超 10 条**，用户要更多时提醒分天投。
- 用户说"先投10个看看效果"是合理的首日策略，不要试图一次全投完。
- 已投稿自动跳过；用户明确要求重投才用 `--force`。
- 失败的素材列出原因、问用户是否重试，不要无限自动重试。
- 不要替用户删除任何稿件。

## 多平台并行策略

B站走 API（无浏览器），可与其他平台并行。抖音/小红书/视频号/快手都走隐身浏览器：

- **可以并行**：B站 + 任一浏览器平台同时跑，不冲突。
- **多个浏览器平台并行**：抖音、小红书、视频号可以同时跑（各自独立浏览器实例），实测可行。但视频号不稳定时建议单跑。
- **总耗时估算**：浏览器平台每条 5-12 分钟间隔，10 条约 50-120 分钟。并行三个平台也在同一时间窗口内完成。

## 第四步：汇报

报告：成功 N 个（B站列 BV 号）、失败 M 个（列原因）、进度（已投/总数）、下一个待投序号。

## 抖音/小红书注意（真实账号浏览器操作，风控更严）

- 登录：`smu login --platform douyin --account <号>`（真终端扫码，不能用 agent 的 shell）。
- **强烈建议小号试水、定时发布、低频**，先养几天确认账号稳再放量。
- 封面按真实比例自动检测选图（竖 3:4 + 横 4:3），缺合规封面会提示。
- 抖音自主声明默认选「内容由AI生成」（`--no-ai-statement` 可关）。
- 发布后提醒用户去创作中心核对：封面横竖、定时时间、声明是否符合预期。

## 视频号特有坑点（shipinhao / tencent）

视频号是目前最不稳定的平台，以下问题反复出现：

### Cookie 假有效
- cookie 文件存在不代表会话有效。经常出现"文件在但浏览器打开后仍需扫码"的情况。
- 表现：进程启动后长时间（5 分钟+）零输出，大概率是卡在登录页等扫码。
- **对策**：视频号发布时，建议用户在**自己的终端**跑命令，能直接看到浏览器窗口、随时扫码。agent 看不到 GUI，卡住了只能盲杀。

### 封面弹窗 bug
- 视频号发布页的封面设置弹窗（`编辑封面` / `裁剪封面图`）经常处于 `hidden` 状态。
- Patchright 的 `wait_for(visible)` 会持续超时（每次 ~15 秒），但通常最终能降级通过：
  - `4:3 横版封面设置失败，这次先跳过` → 竖版封面仍能设上，视频可以发出。
  - `封面裁剪确认时出错` → 如果卡在此处超过 2 分钟不动，进程大概率已死，杀掉重试。
- **重试建议**：杀掉卡死进程 → `smu mark <目录> <已发序号> --platform shipinhao` 标记已发的 → 从下一个序号继续。

### 活动下拉菜单
- 视频号发布页有一个活动/事件选择下拉框，可用于选择法考相关活动提高曝光。
- 当前版本的 sau tencent uploader **不支持**选择活动，视频只能裸发。
- 如需要选活动，目前只能手动在视频号创作中心操作。

### 建议运行方式
1. 先在其他三个平台跑通后，视频号**单独跑**。
2. 用户在**自己的终端**执行（能看到浏览器 GUI，随时扫码/手动干预）。
3. 如 agent 盲跑：监控前 2 分钟是否有输出，无输出则可能卡登录，果断杀掉通知用户。
4. 卡封面弹窗超过 3 分钟无新日志 → 杀掉，mark 已发，从下一条继续。

## 数据与运营分析（数据中心 / 最佳时间 / 周报）

采集每条已发视频的播放/赞/评论/分享/收藏，存本地时间序列，由你（agent）做分析。

```bash
smu stats pull --platform douyin --account <号>   # 采集一次（追加快照，建议每天定时跑）
smu stats pull --platform bilibili                # B站
smu stats show --platform douyin --top 10         # 看最近快照 + 播放 Top
```

数据落在 `~/.self-media-uper/stats/<平台>.jsonl`，每行一条快照记录：
`{platform, account, fetched_at, video_id, title, published_at(unix秒), play, like, comment, share, collect}`。
同一视频多次 pull = 多条不同 fetched_at 的记录（时间序列）。

用户要**趋势/最佳时间/周报**时，你直接 Read 这个 jsonl 自己算，不要硬塞给某个内置功能：
- **趋势**：同一 video_id 按 fetched_at 排序看播放增长；或按 fetched_at 汇总账号总播放。
- **最佳发布时间**：把每条 published_at 转成「星期几 + 小时」，对播放量分组求平均，找高产时段。
- **周报**：取近 7 天数据，算总播放/互动、增长、Top 视频、最佳时段，写成报告。
- **发布前检查**：你本来就能审标题/标签/钩子；结合上面采集到的历史数据更有据。

（已支持采集：抖音、B站、小红书、视频号。小红书/视频号走 patchright 拦截创作中心已签名响应，采集时会短暂弹窗。）

## 登录态问题

报「未登录」→ 让用户在**自己的终端**（需要扫码）运行 `smu login [--platform 平台 --account 号]`。
B站 cookie 过期 → `smu renew` 可直接代跑。
视频号 cookie 经常假有效 → 参考上文「视频号特有坑点」一节。

## 批量换号 / 重置 cookie

如需更换所有平台的账号（从测试号切到大号）：
```bash
# 删掉所有旧 cookie
rm ~/.self-media-uper/bilibili.cookies.json
rm ~/.self-media-uper/engines/social-auto-upload/cookies/*.json

# 重新登录各平台（用户在终端扫码）
smu login --platform bilibili
smu login --platform douyin --account main
smu login --platform xiaohongshu --account main
smu login --platform kuaishou --account main
smu login --platform shipinhao --account main
```

## 项目配置（不在 skill 里写死）

标题前缀、参与话题、固定标签、分区等**因项目而异**，放在素材目录下的 `smu.json`，例如：

```json
{ "title_prefix": "【某系列（某科目）】", "topic": "某话题", "ensure_tags": ["标签A", "标签B"] }
```

skill 不假设任何具体项目的配置；换项目/科目只改对应素材目录的 smu.json，或用 `--title-prefix` 等参数临时覆盖。
