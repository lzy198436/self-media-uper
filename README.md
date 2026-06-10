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
smu login                 # 扫码登录（一次）
smu scan   <素材目录>     # 素材完整性检查
smu sync   <素材目录>     # 与B站已发布稿件对账（识别手动投过的）
smu status <素材目录>     # 进度
smu upload <素材目录> 11-20        # 投序号11到20
smu upload <素材目录> --all        # 投全部未投的
smu upload <素材目录> 11 --private # 仅自己可见测试
```

## 素材目录约定

每个素材一个子文件夹（`NN_标题/`），按文件名后缀自动配对：
`NN_标题.mp4`（横版主视频）、`*_封面_B站16比9.jpg`、`*_封面_B站首页4比3.jpg`、
`*_文案_B站.txt`（正文=简介，`#xx` 行=标签）；竖屏视频与抖音/小红书/视频号文案为多平台预留。

目录下可放 `smu.json` 覆盖默认参数（标题前缀、话题、固定标签）。

## B站投稿能力（2026-06 实测）

- Web v3 投稿接口（biliup `--submit web`）
- 双封面：16:9 主封面 + 4:3 首页推荐封面（`cover43`）
- 创作声明「含AI生成内容」（`creation_statement: {"id": 1}`）
- 新分区「知识」（`human_type2: 1010`）+ 旧分区 124
- 话题/活动（`topic_id`/`mission_id`，按名称搜索缓存）
- 仅自己可见（`--private`）、定时发布（`--dtime`）、批量限速、断点续投、防重复

## 架构

```
smu/
├── cli.py            # scan/status/login/renew/sync/mark/upload
├── materials.py      # 素材扫描配对 + 文案解析
├── state.py          # ~/.self-media-uper/ 状态与凭据
└── platforms/
    ├── base.py       # PlatformAdapter 接口
    └── bilibili.py   # biliup + member API
```

新平台 = 在 `platforms/` 实现 `PlatformAdapter`（login/publish/sync）并在 `__init__.py` 注册。
抖音/小红书/视频号/微博无公开 API，规划用 Playwright 浏览器自动化实现。
