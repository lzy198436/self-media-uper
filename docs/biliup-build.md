# biliup 编译说明

B站上传内核用 [biliup/biliup](https://github.com/biliup/biliup) 仓库里的 Rust CLI（biliup-cli）。
**不要用旧的 biliup-rs v0.2.4 预编译包**——它只走 APP 接口，不支持 `--submit web`/`--is-only-self`，
而双封面（cover43）、创作声明（creation_statement）、话题等字段需要 Web v3 接口。

```bash
# 需要 Rust ≥1.88（let-chains），用 rustup 而不是 homebrew 的旧 rust
rustup update stable

git clone --depth 1 https://github.com/biliup/biliup.git
cd biliup
mkdir -p out   # biliup-cli 的 RustEmbed 需要 webui 产物目录存在，空目录即可
cargo build --release -p biliup-cli

cp target/release/biliup ~/.local/bin/biliup
biliup --version   # biliup-cli 1.2.1（2026-06 验证）
```

登录态文件由 smu 管理（`~/.self-media-uper/bilibili.cookies.json`），
smu 调 biliup 时统一传 `-u` 指定，不依赖当前目录的 cookies.json。
