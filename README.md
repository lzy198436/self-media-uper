# self-media-uper

自媒体批量投稿工具：读取素材目录（视频 + 封面 + 文案），批量投稿到 B 站（微博/抖音/小红书/视频号在路线图上）。
核心是 `smu` CLI，外加一份 Hermes/OpenClaw/Claude Code 通用的 agent skill（`skills/self-media-uper/`）。

## 安装

```bash
# 1. biliup（B站上传内核），见 docs/biliup-build.md
# 2. smu 命令
cat > ~/.local/bin/smu <<'EOF'
#!/bin/sh
export PYTHONPATH="<本仓库绝对路径>"
exec python3 -m smu "$@"
EOF
chmod +x ~/.local/bin/smu
# 3. agent skill（Hermes）
ln -s <本仓库绝对路径>/skills/self-media-uper ~/.hermes/skills/self-media-uper
```

## 使用

```bash
# B站（默认平台，biliup 引擎）
smu login                 # 扫码登录（一次）
smu scan   <素材目录>     # 素材完整性检查
smu sync   <素材目录>     # 与B站已发布稿件对账（识别手动投过的）
smu status <素材目录>     # 进度
smu upload <素材目录> 11-20        # 投序号11到20
smu upload <素材目录> --all        # 投全部未投的
smu upload <素材目录> 11 --private # 仅自己可见测试

# 抖音 / 小红书 / 快手（social-auto-upload 引擎，见 docs/sau-setup.md）
smu login  --platform douyin --account 小号名               # 扫码登录抖音
smu upload <素材目录> 11 --platform douyin --account 小号名 \
           --schedule "2026-06-13 12:00"                    # 定时发布
```

平台引擎：B站 = biliup（API）；抖音/小红书/快手 = social-auto-upload（patchright 隐身浏览器）。
都藏在 `platforms/` 适配器后面，smu 一套命令通吃，引擎可换。

## 素材目录约定（请尽量按此整理素材）

**标准约定**：每个素材一个子文件夹，文件名 = `序号_标题` + 用途后缀。完全符合时零配置、
扫描零警告，所有信息精确取自你准备的文件：

```
<素材目录>/
└── 11_在线诉讼适用规则及庭审故障处理/
    ├── 11_在线诉讼适用规则及庭审故障处理.mp4              ← 横版主视频（B站用）
    ├── 11_…_竖屏.mp4                                      ← 竖版视频（抖音等预留）
    ├── 11_…_封面_B站16比9.jpg                             ← B站主封面
    ├── 11_…_封面_B站首页4比3.jpg                          ← B站首页推荐封面
    ├── 11_…_封面_竖版3比4.jpg                             ← 竖版封面（预留）
    ├── 11_…_文案_B站.txt                                  ← 正文=简介，#标签 行=标签
    └── 11_…_文案_抖音.txt / _小红书.txt / _视频号.txt     ← 多平台预留
```

要点：**文件夹名以数字序号开头**（才能用 `11-20` 这种范围选择）；封面命名带
`16比9`/`4比3` 关键词；文案命名带平台名；标签写在文案末尾的 `#xx #yy` 行。

**宽容规则**（目录不完全符合约定时自动兜底，扫描结果里会用 ↳ 标注识别依据）：

- 视频直接平铺在素材目录里（不分文件夹）→ 每个视频算一个素材，按同名前缀找封面/文案
- 封面文件名没有比例关键词 → 用 ffprobe 读图片实际宽高比分类 16:9 / 4:3 / 竖版
- 文案文件名没有平台名、但文件夹里只有一个 txt/md → 当作B站文案
- 格式宽容：视频 mp4/mov/mkv/webm，封面 jpg/jpeg/png/webp，文案 txt/md

**带病上传保护**：`upload` 前会逐素材预检（展示将用的视频/封面/标题/简介/标签），
缺 16:9 封面、4:3 封面或B站文案的素材**默认拒绝上传**，确认接受降级
（无封面=B站自动截帧、无文案=空简介）再加 `--allow-incomplete` 放行。

目录下可放 `smu.json` 覆盖默认参数（标题前缀、话题、固定标签），换科目时改前缀用它。

## B站投稿能力（2026-06 实测）

- Web v3 投稿接口（biliup `--submit web`）
- 双封面：16:9 主封面 + 4:3 首页推荐封面（`cover43`）
- 创作声明「含AI生成内容」（`creation_statement: {"id": 1}`）
- 新分区「知识」（`human_type2: 1010`）+ 旧分区 124
- 话题/活动（`topic_id`/`mission_id`，按名称搜索缓存）
- 仅自己可见（`--private`）、定时发布（`--dtime`）、批量限速、断点续投、防重复

## 防封：拟人化与节奏控制

抖音/小红书是真实账号浏览器操作，防封靠两层：

- **视频间随机大间隔**（smu 控制，主要杠杆）：抖音/小红书默认随机 5~12 分钟一条，
  `--min-interval/--max-interval` 可调。
- **发布内随机延迟**（运行时补丁，不改 sau）：把 sau 的固定等待按 1.2~2.2 倍 + 抖动拉长。
  详见 docs/sau-setup.md。

封面**按真实宽高比检测**（ffprobe）选图，不靠文件名，比例对不上的不传。
建议：小号试水、低频、定时、隔开时间，养几天确认稳再放量。

## 架构

```
smu/
├── cli.py            # scan/status/login/renew/sync/mark/upload + 随机间隔
├── materials.py      # 素材扫描配对（宽容识别）+ 文案解析 + ffprobe 比例
├── state.py          # ~/.self-media-uper/ 状态与凭据
└── platforms/
    ├── base.py             # PlatformAdapter 接口
    ├── bilibili.py         # biliup + member API
    ├── sau.py              # 抖音/小红书/快手（social-auto-upload）
    └── _sau_humanize.py    # sau 运行时补丁：随机延迟 + macOS 兼容
```

新平台 = 在 `platforms/` 实现 `PlatformAdapter`（login/publish/sync）并在 `__init__.py` 注册。
视频号/微博待接入。
