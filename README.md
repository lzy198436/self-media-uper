# self-media-uper

**一套素材目录 → 一条命令发全平台 + 采数据。** 读取本地素材目录（视频 + 封面 + 多平台文案），
批量投稿到 B站 / 抖音 / 小红书 / 视频号 / 快手，并采集每条已发视频的播放/互动数据供分析。

核心是 `smu` CLI，外加一份 Hermes / OpenClaw / Claude Code 通用的 agent skill
（`skills/self-media-uper/`）——对 agent 说「把民诉法投到抖音」「分析我的最佳发布时间」即可。

---

## 平台能力总览

| 平台 | 引擎 | 投稿 | 封面 | 创作声明 | 定时 | 数据采集 |
|---|---|:--:|---|---|:--:|:--:|
| **B站** | biliup（Web v3 API） | ✅ | 16:9 + 4:3 双封面 | 含AI生成内容 | ✅ | ✅ cookie 直拉 |
| **抖音** | social-auto-upload（patchright） | ✅ | 竖3:4 + 横4:3 双封面 | 内容由AI生成（校验生效） | ✅ | ✅ cookie 直拉 |
| **小红书** | social-auto-upload | ✅ | 单封面 3:4 | 原创（自动） | ✅ | ✅ 浏览器拦截 |
| **视频号** | social-auto-upload（tencent） | ✅ | 竖3:4 + 横4:3 | 声明原创（自动）+短标题 | ✅ | 🚧 待接入 |
| **快手** | social-auto-upload | ✅ | 单封面 3:4 | — | ✅ | 🚧 待接入 |

> 所有平台都藏在 `platforms/` 适配器后面，`smu` 一套命令通吃，引擎可换。
> B站走官方 API（快、稳、无头）；抖音/小红书/视频号/快手走隐身浏览器（真实账号操作，防检测）。

---

## 安装

```bash
# 1. B站内核 biliup（源码编译，见 docs/biliup-build.md）
# 2. 抖音/小红书等内核 social-auto-upload（见 docs/sau-setup.md）
# 3. smu 命令
cat > ~/.local/bin/smu <<'EOF'
#!/bin/sh
export PYTHONPATH="<本仓库绝对路径>"
exec python3 -m smu "$@"
EOF
chmod +x ~/.local/bin/smu
# 4. agent skill（Hermes）
ln -s <本仓库绝对路径>/skills/self-media-uper ~/.hermes/skills/self-media-uper
```

---

## 命令速查

| 命令 | 作用 |
|---|---|
| `smu login [--platform 平台 --account 号]` | 扫码登录（真终端运行） |
| `smu scan <目录>` | 扫描素材、完整性检查 |
| `smu status <目录> [--platform 平台]` | 已投/待投进度 + 下一个待投 |
| `smu sync <目录>` | 仅B站：拉已发布稿件自动对账（识别手动投的） |
| `smu upload <目录> 11-20 [选项]` | 投序号 11~20 |
| `smu upload <目录> --all` | 投全部未投稿素材 |
| `smu mark <目录> 1-10 [--unmark]` | 手动标记/取消已投 |
| `smu stats pull --platform 平台 --account 号` | 采集每条已发视频数据 |
| `smu stats show --platform 平台 --top 10` | 看最近快照 + 播放 Top |

### upload 常用选项

| 选项 | 说明 |
|---|---|
| `--platform` | bilibili（默认）/ douyin / xiaohongshu / shipinhao / kuaishou |
| `--account` | 账号标签（抖音/小红书多账号区分） |
| `--schedule "YYYY-MM-DD HH:MM"` | 定时发布（抖音/小红书/视频号） |
| `--profile` | 发布档位：`aggressive` / `steady`(默认) / `conservative` |
| `--private` | 仅自己可见（B站，测试用） |
| `--dry-run` | 只打印将提交的标题/封面/标签，不上传 |
| `--allow-incomplete` | 允许缺封面/文案的素材降级上传（默认拒绝） |
| `--force` | 允许重投已标记素材 |
| `--no-daily-cap` | 解除每日上限 |

```bash
# 例：抖音定时发第11条，稳健档
smu upload <目录> 11 --platform douyin --account main --schedule "2026-06-13 12:00"
# 例：B站批量投11~20
smu upload <目录> 11-20
```

---

## 防封：拟人化 + 节奏档位

抖音/小红书是真实账号浏览器操作，防封靠两层 + 三档发布策略：

| 档位 `--profile` | 视频间随机间隔 | 每日上限 |
|---|---|---|
| `aggressive`（激进） | 2~5 分钟 | 20 条 |
| `steady`（稳健，默认） | 5~12 分钟 | 10 条 |
| `conservative`（保守） | 10~20 分钟 | 5 条 |

> B站走 API，间隔单列 30~90 秒、不限每日。
> **发布内随机延迟**：运行时补丁（不改 sau 源码）把固定等待按 1.2~2.2 倍 + 抖动拉长。
> **封面按真实宽高比检测**（ffprobe）选图，不靠文件名，比例对不上的不传。
> 建议：小号试水、低频、定时、隔开时间，养几天确认稳再放量。

---

## 数据采集与运营分析

采集每条已发视频的**播放/赞/评论/分享/收藏**，存本地时间序列（jsonl），由 agent 做分析。

| 平台 | 采集方式 | 模式 |
|---|---|---|
| 抖音 | cookie GET `creator.douyin.com/.../aweme/post/` → 每条 statistics | 快、无头 |
| B站 | cookie GET `member.bilibili.com/x/web/archives` → 每稿 stat | 快、无头 |
| 小红书 | patchright 打开创作中心，拦截页面**自己发的已签名响应**（免写 x-s 签名） | 有头、会弹窗 |

```bash
smu stats pull --platform douyin --account main   # 采集一次（建议每天定时跑，攒成时间序列）
smu stats show --platform douyin --top 10
```

数据落在 `~/.self-media-uper/stats/<平台>.jsonl`，每行一条快照：
`{platform, account, fetched_at, video_id, title, published_at, play, like, comment, share, collect}`。
对 agent 说「分析我抖音的最佳发布时间 / 写个周报」，它读 jsonl 自己算趋势、最佳时段、Top 内容。

> 小红书签名说明：`window._webmsxyw` 已被移除、纯算法库易失效；我们不复刻签名，而是**让登录态浏览器
> 自己签名调接口、我们拦截响应**——小红书改算法也不受影响。

---

## 素材目录约定

**标准约定**：每个素材一个子文件夹，文件名 = `序号_标题` + 用途后缀。完全符合时零配置、扫描零警告。

```
<素材目录>/
└── 11_在线诉讼适用规则及庭审故障处理/
    ├── 11_….mp4                        ← 横版主视频（B站）
    ├── 11_…_竖屏.mp4                    ← 竖版视频（抖音/小红书/视频号）
    ├── 11_…_封面_B站16比9.jpg           ← 16:9 封面
    ├── 11_…_封面_B站首页4比3.jpg        ← 4:3 封面（抖音横封面也用它）
    ├── 11_…_封面_竖版3比4.jpg           ← 3:4 竖封面（抖音/小红书/视频号）
    ├── 11_…_文案_B站.txt                ← 正文=简介，#标签 行=标签
    └── 11_…_文案_抖音.txt / _小红书.txt / _视频号.txt
```

| 命名要点 | 说明 |
|---|---|
| 文件夹名以**数字序号开头** | 才能用 `11-20` 范围选择 |
| 封面带 `16比9`/`4比3`/`3比4` 关键词 | 也会用 ffprobe 按真实比例兜底识别 |
| 文案带平台名 `_文案_抖音` 等 | 标题=首行、正文=简介、`#xx` 行=标签 |
| 目录下可放 `smu.json` | 覆盖标题前缀/话题/固定标签，换科目改它 |

**宽容规则**（不完全符合约定时自动兜底，扫描用 ↳ 标注）：平铺目录、ffprobe 比例分类封面、
单文案兜底、格式宽容（视频 mp4/mov/mkv/webm，封面 jpg/png/webp，文案 txt/md）。

**带病上传保护**：`upload` 前逐素材预检，缺关键件的素材**默认拒绝**，`--allow-incomplete` 才放行。

---

## B站投稿能力（2026-06 实测）

| 能力 | 实现 |
|---|---|
| 投稿接口 | Web v3（biliup `--submit web`） |
| 双封面 | 16:9 主封面 + 4:3 首页推荐封面（`cover43`） |
| 创作声明 | 含AI生成内容（`creation_statement: {"id": 1}`） |
| 分区 | 新分区「知识」`human_type2: 1010` + 旧分区 124 |
| 话题/活动 | `topic_id`/`mission_id`，按名称搜索缓存 |
| 其它 | 仅自己可见、定时发布、批量限速、断点续投、防重复、自动对账 |

---

## 架构

```
smu/
├── cli.py                 # scan/status/login/sync/mark/upload/stats + 随机间隔 + 档位
├── materials.py           # 素材扫描配对（宽容识别）+ 文案解析 + ffprobe 比例
├── state.py               # ~/.self-media-uper/ 状态与凭据
├── stats.py               # 数据采集（抖音/B站 cookie 直拉，小红书拦截）
├── _xhs_collect.py        # 小红书采集脚本（patchright，跑 sau venv）
└── platforms/
    ├── base.py            # PlatformAdapter 接口（login/publish/sync）
    ├── bilibili.py        # biliup + member API
    ├── sau.py             # 抖音/小红书/视频号/快手（platform-aware 封面/标签/声明）
    └── _sau_humanize.py   # sau 运行时补丁：随机延迟 + macOS 兼容 + 抖音AI声明校验
```

新平台 = 在 `platforms/` 实现 `PlatformAdapter` 并在 `__init__.py` 注册。微博待接入。

---

## 文档

| 文档 | 内容 |
|---|---|
| `docs/biliup-build.md` | B站内核 biliup 源码编译 |
| `docs/sau-setup.md` | 抖音/小红书内核 social-auto-upload 安装 + 拟人化补丁 |
| `skills/self-media-uper/SKILL.md` | agent skill 工作流 |
