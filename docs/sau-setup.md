# social-auto-upload(sau)引擎安装

抖音/小红书/快手走 [social-auto-upload](https://github.com/dreammis/social-auto-upload)（Python，
底层 patchright 隐身浏览器）。smu 通过适配器调它，并用运行时补丁加拟人化延迟、修 macOS 兼容。

## 安装（持久目录）

```bash
mkdir -p ~/.self-media-uper/engines
git clone https://github.com/dreammis/social-auto-upload.git \
  ~/.self-media-uper/engines/social-auto-upload
cd ~/.self-media-uper/engines/social-auto-upload
uv venv --python 3.12 .venv          # 要求 Python <3.13
source .venv/bin/activate
uv pip install -e .
patchright install chromium          # 首次下载 chromium（~150MB）
cp conf.example.py conf.py
```

smu 默认从 `~/.self-media-uper/engines/social-auto-upload` 找它；装别处用环境变量
`SMU_SAU_DIR` 指定。

## 登录

```bash
smu login --platform douyin --account 小号名     # 扫码（真终端运行，会弹真实Chrome）
```

cookie 存到 `<sau目录>/cookies/douyin_<account>.json`，长期复用。

## 拟人化与兼容补丁（smu/platforms/_sau_humanize.py）

不改 sau 源码，运行时补 patchright：

- **wait_for_timeout 随机化**（默认开）：把 sau 的固定 1~2s 等待按 1.2~2.2 倍 + 随机抖动拉长，
  让步骤节奏像人。
- **slow_mo**（默认**关**）：给每个微操作加延迟。太激进会搅乱抖音的动态 UI（下拉框/日期选择器），
  默认不开；需要时设 `SMU_SLOWMO_MIN/MAX`（毫秒）。
- **macOS 键盘兼容**：sau 用 `Control+A` 全选（Win/Linux 写法），macOS 上换成 `Meta+A`，
  否则设定时会因清空失败报「定时<2小时」。

视频之间的间隔由 smu 控制（抖音/小红书默认随机 5~12 分钟），是防封的主要杠杆。

## 已知限制 / 待办

- **创作声明**：sau 抖音目前自动选「内容为个人观点或见解」，无法选「含AI生成内容」。
  需要 AI 声明的话要在 sau 侧加处理（待办）。
- **封面槽顺序**：sau 的 `set_thumbnail` 标签页顺序与当前抖音 UI 相反，smu 用
  `_DOUYIN_COVER_SWAP` 反向传参修正；若 sau 修了顺序，把该常量改 False。
- 封面按**真实宽高比检测**选图（竖 3:4 / 横 16:9），不靠文件名，比例对不上的不传。
