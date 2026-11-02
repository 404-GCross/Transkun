# Transkun 分发与打包

## 一键安装（Linux x86_64 / macOS Apple Silicon）

```bash
curl -fsSL https://gh-proxy.com/https://github.com/404-GCross/Transkun/releases/latest/download/install.sh | bash
```

脚本自己判断系统：Debian/Ubuntu 走 `.deb`、Fedora/RHEL 走 `.rpm`、其他发行版退到
免 root 的 AppImage；macOS 挂载 dmg 复制到 `/Applications`（并去掉隔离标记）。
下载和 SHA256 校验都在普通用户下完成，只有最后一条安装命令用 sudo。

| 参数 | 作用 |
|---|---|
| `--version 0.3.2` | 固定版本（默认取最新 release） |
| `--file ./xxx.deb` | 离线安装本地包（`.deb` / `.rpm` / `.AppImage` / `.dmg`） |
| `--appimage` | Linux 上强制用免 root 的 AppImage |
| `--cuda cu124` | 装完顺手换 CUDA 版 PyTorch（`cu124` / `cu126` / `cu128`） |
| `--mirror URL` | 下载前缀，等价于环境变量 `TRANSKUN_MIRROR` |
| `--dry-run` | 只打印解析到的版本、URL 和将执行的命令 |
| `--uninstall` | 卸载（保留用户数据） |
| `--yes` / `-y` | 不询问 |

- 下载源会先对「GitHub 直连」和 `gh-proxy` 各取样 256 KB 测速，用快的那个；
  下载失败自动换源。`--mirror URL` 显式指定时不再测速。
- 校验和优先直连 GitHub；直连不通才退回镜像并打印提示。
- 卸载不删 `~/.cache/transkun`（任务）和 `~/.config/transkun`（令牌/配置）。
- AppImage 模式装到 `~/.local/bin/transkun-app`，并生成 `transkun` 包装脚本
  （保持上游 `transkun in.wav out.mid` 的参数形式）。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[app]'

.venv/bin/transkun-app                    # 桌面界面（需要 tkinter）
.venv/bin/transkun-app song.mp3 -o out.mid # 命令行
.venv/bin/transkun-app --web --lan        # 浏览器 / 手机
.venv/bin/transkun-app --check            # 依赖 + 权重
```

源码运行不需要打包；`.venv` 里没有 tkinter 时，桌面界面会提示安装
`python3-tk`（Debian）或 `python3-tkinter`（Fedora），CLI/WebUI 不受影响。

## WebUI（浏览器 / 局域网）

```bash
transkun-app --web --lan
```

启动后终端打印带令牌的地址：

```
  本机：    http://127.0.0.1:8765/?t=…
  局域网：  http://192.168.1.201:8765/?t=…
```

把局域网链接发到手机即可。默认端口 8765，被占用会自动往后找。

- **令牌**：`~/.config/transkun/web_token`，`--new-token` 重新生成，
  `--token XXX` 临时指定。没有令牌连本机也打不开（防 CSRF / DNS rebinding）。
- **默认只监听本机**；`--lan` 等价于 `--host 0.0.0.0`。
- **上限**：单文件 1 GiB、时长 30 分钟、队列 8 个（`--max-bytes` /
  `--max-minutes` / `--queue`）；结果保留 24 小时（`--keep-hours`），
  服务重启会清空任务，想要的文件请先下载。
- **设备**是服务端启动参数（`--device auto|cuda|cpu`），页面上只读显示。
- 页面支持多文件排队、取消、重新转谱（复用已上传文件，硬链接）、下载演奏版 MIDI、
  打谱文件，以及钢琴卷预览。
- 防火墙：

  ```bash
  # Debian / Ubuntu
  sudo ufw allow 8765/tcp
  # Fedora / RHEL
  sudo firewall-cmd --add-port=8765/tcp --permanent && sudo firewall-cmd --reload
  ```

**安全边界**：明文 HTTP，只应在家里/工作室局域网使用，不要做端口转发或 DMZ。

自检（不需要模型）：`python -m transkun_app.webui.selftest`，53 项。

## 打谱版（MusicXML / PDF，可选）

把演奏 MIDI 降维成可读的钢琴缩编谱；演奏版 `<name>.mid` 保留，不覆盖。

| 输出 | 说明 |
|---|---|
| `<name>.score.musicxml` | 主交付，MuseScore 可直接编辑/打印 |
| `<name>.score.pdf` | Verovio + cairosvg 渲染；缺 libcairo 时只保留 SVG |
| `<name>.score.mid` | 量化后的打谱版 MIDI，用于试听 |
| `<name>.score.json` | 参数、调性、BPM、统计、警告 |

```bash
transkun-app song.mp3 -o song.mid --score
transkun-app song.mp3 --score --score-time-signature 3/4 --score-key D
transkun-score song.mid -o song.score --pdf
transkun-score song.mid -o song.score --tuplet-mode none   # 纯 16 分
transkun-score --selftest
# AppImage 没有单独的 transkun-score 可执行文件：
./transkun-*.AppImage --score-cli song.mid -o song.score --pdf
```

- `block`（默认）同格起音合并为和弦、清理不可演奏内声部、滚奏加琶音记号；
  `faithful` 保留全部音不删内声部。
- 三连音按起音间隔聚组：连续 3 个起音才写成 `3:2`；同一起音上不同时值的
  和弦会统一到三连音单位。孤立或跨手拆开的音回退 16 分，不会生成单音符 tuplet。
  `--tuplet-mode none` 可完全关闭三连音。
- 调性用 music21 估计；拍号自动暂时保守用 4/4，可通过 UI/CLI 指定 3/4、6/8。
- MusicXML 同时写文本 `M.M. 95` 和 `<sound tempo="95"/>`；score MIDI 的
  `set_tempo` 与 BPM 一致，MuseScore/DAW 不再按默认 120 BPM 播放。
- 中文标题使用随包 `NotoSansCJKsc-Regular.otf`（OFL）渲染；MusicXML 始终保留原文。
- PDF 需要 `libcairo2`（Debian）/ `cairo`（Fedora）；AppImage 目标机没有 cairo 时
  只保留 MusicXML/SVG。macOS 的 cairosvg 也依赖 cairo，没装时同样只出
  MusicXML/SVG（macOS 的 CI 自检用 `--no-pdf`）。详见 [SCORE.md](SCORE.md)。

## 构建 deb / rpm（离线包）

包内可以内置**完整离线运行时 + 模型权重**，安装后不联网、不依赖系统 Python。

| 路径 | 内容 | 体积 |
|---|---|---|
| `/usr/share/transkun/runtime.tar.zst` | Python 3.12 + venv（CPU 版 PyTorch、music21、Verovio 等）+ uv | ~384 MB 压缩 / 1.6 GB 展开 |
| `/usr/share/transkun/transkun/pretrained/` | `2.0.pt`（54 MB）+ `2.0.conf` | 54 MB |
| `/usr/share/transkun/tcltk/` | 带 Xft 的 Tcl/Tk（Tk 界面） | ~7 MB |
| `transkun/`、`transkun_app/`、`packaging/`、`VERSION` | 程序与启动脚本 | ~2 MB |

首次启动 `run.sh` 把运行时解压到 `~/.local/share/transkun`（本机实测约 40 秒），之后直接用。
以下情况会重新解压：包内 `VERSION` 与运行时版本戳不一致、环境不完整、或设了
`TRANSKUN_FORCE_RUNTIME=1`。权重在只读的 `/usr/share/transkun/transkun/pretrained`
下直接读取。

```bash
./packaging/build-tcltk.sh .tcltk
VERSION=0.3.2 ./packaging/build-runtime.sh          # → dist/transkun-runtime-0.3.2.tar.zst
TCLTK_DIR="$PWD/.tcltk" VERSION=0.3.2 \
  RUNTIME_TARBALL="$PWD/dist/transkun-runtime-0.3.2.tar.zst" \
  ./packaging/build-deb.sh
# RPM 需要 rpmbuild（Fedora/RHEL 或装了 rpm 的 Debian）
TCLTK_DIR="$PWD/.tcltk" VERSION=0.3.2 \
  RUNTIME_TARBALL="$PWD/dist/transkun-runtime-0.3.2.tar.zst" \
  ./packaging/build-rpm.sh
```

`build-runtime.sh` 默认 `TORCH_VARIANT=cpu`；`cu124/cu126/cu128` 会让包大 2GB+，不建议。
缺 runtime/TclTk 时脚本只告警，包仍可构建（首次启动需联网/中文可能空白）。

安装：

```bash
sudo apt install ./dist/transkun_0.3.2_amd64.deb
sudo dnf install ./dist/transkun-0.3.2-1.x86_64.rpm
transkun-app
```

运行时依赖：`zstd`、`ffmpeg`（m4a/aac 解码）、X11/字体库；Python、Tcl/Tk、PyTorch、
权重都在包内。

## 中文字体 / Tk 显示空白

uv / python-build-standalone 自带的 Tk 不带 Xft/fontconfig，中文会显示为空白。
解决办法是编译一份**启用 Xft 的 Tcl/Tk 9.0** 并随包分发：

```bash
# 需要 build-essential pkg-config zlib1g-dev libx11-dev libxss-dev libxext-dev \
#      libxft-dev libfontconfig1-dev libxrender-dev
./packaging/build-tcltk.sh .tcltk
```

`packaging/fix_tk_system.py` 用它覆盖 venv 自带的 `libtcl9.0.so` / `libtcl9tk9.0.so`；
`run.sh` 每次启动幂等校正，AppImage 在构建时复制依赖库。安装系统字体也可以缓解
（`fonts-noto-cjk`），但不能替代 Xft 版 Tk。

## 构建 AppImage

AppImage 内置 Python 3.12 + 程序 + CPU 依赖 + **带 Xft 的 Tcl/Tk + Noto Sans CJK**，
不依赖系统 Python/Tk/中文字体。权重也内置（54 MB）。

```bash
./packaging/build-tcltk.sh .tcltk
TCLTK_DIR="$PWD/.tcltk" VERSION=0.3.2 ./packaging/build-appimage.sh
# 产物: dist/transkun-0.3.2-x86_64.AppImage
```

- 构建机需要 `fonts-noto-cjk` 和 Xft/fontconfig 运行库；
- 建议在 `ubuntu:22.04` 里构建以获得更宽的 glibc 兼容性；
- 运行需要 FUSE；没有时用 `--appimage-extract-and-run`；
- GPU：AppImage 内 venv 在只读挂载点上，无法原地换 CUDA 版 PyTorch；需要 GPU 用 deb/rpm；
- 中文渲染自检：`./transkun-*.AppImage --check-fonts`（CI 用 Xvfb 跑）。

## 构建 macOS 应用包（Apple Silicon）

```bash
VERSION=0.3.2 ./packaging/build-macos.sh
# 产物: dist/Transkun.app、dist/transkun-0.3.2-arm64.dmg
bash packaging/verify-macos.sh dist/*.dmg
```

- 只支持 Apple Silicon（arm64），macOS 12+，纯 CPU 推理。
- 包内自带 Python 3.12 + PyTorch + 权重，双击即用，不需要终端/联网。
- venv 的 `pyvenv.cfg` 与 `bin/python` 写成相对路径，`.app` 拖到哪、甚至从只读
  dmg 直接启动都能用；启动不写包内文件（`PYTHONDONTWRITEBYTECODE=1`）。
- 中文字体用系统苹方/黑体（Tk 走 CoreText），不需要 Xft 版 Tcl/Tk。
- 未公证的应用首次打开会被 Gatekeeper 拦一次；`verify-macos.sh` 会检查包结构、
  复制后的 `.app`、8 秒端到端转谱和只读 dmg 启动。

## 自检

```bash
python -m py_compile transkun_app/*.py transkun_app/webui/*.py
python -m pytest tests -q                     # 47 项（无显示器时 GUI 用例跳过）
transkun-app --check                          # 依赖 + 权重
transkun-app --score-selftest                 # 打谱版自检
transkun-score --selftest                     # 打谱版自检（含三连音/速度校验）
python -m transkun_app.webui.selftest         # WebUI 53 项
transkun-app --selftest                       # 合成音频 → 模型 → MIDI（约 1 分钟）
transkun-app --check-fonts                    # 需要 tkinter + DISPLAY（CI 用 xvfb）
bash -n packaging/*.sh
python packaging/check-shell.py               # macOS bash 3.2 写法检查
```

## GitHub Actions

- `build-test-packages.yml`：push 到 `main` / PR 时调用完整的构建流程（不发布）。
- `packages.yml`：push `v*` tag 时构建 deb/rpm/AppImage/macOS，跑安装脚本与 WebUI
  探活，然后上传 GitHub Release 和 `SHA256SUMS`。

```bash
git tag v0.3.2
git push origin v0.3.2
```

## 当前验证状态（0.3.2 打谱修复版）

| 项 | 状态 |
|---|---|
| 上游 CLI / 应用 CLI / 批量 / 打谱版 | 本机通过（真实 10 s 音频 → MIDI + MusicXML/PDF） |
| WebUI（令牌、上传、队列、重转谱、下载） | 本机 53 项接口自检 + 真实 10 s 上传含打谱下载通过 |
| pytest | 47 项通过（含三连音 XML / 随机混合网格 / tempo 回归；本机无 tkinter，GUI 用例跳过） |
| score MIDI 速度 | 0.3.2 本机验证：95 BPM 输入 293.02 s → score MIDI 293.68 s（+0.23%）；MusicXML 含 `<sound tempo="95"/>` |
| 离线运行时 | 0.3.1 发布包验证通过（384 MB，zstd -10；展开 1.6 GB）；0.3.2 待 CI |
| deb（含运行时 + 权重） | 0.3.1 发布包 432 MB；Debian 容器内验证 CLI / WebUI / 上游 CLI / 打谱 PDF。0.3.2 待 CI |
| Tk 界面 | 代码与 stub 测试完成；本机无显示器，已确认包内 Tk 9.0 可用、无 DISPLAY 时给出友好提示 |
| macOS | 0.3.1 发布包 773 MB，CI（macos-14）完整通过；0.3.2 待 CI |
| AppImage | 0.3.1 发布包 546 MB，CI 通过；0.3.2 待 CI |
| AppImage 中文 | 本机缺 Xft 开发头文件和 Noto CJK，构建时跳过了带 Xft 的 Tcl/Tk 与字体；CI 会装齐后再构建 |
| rpm | 0.3.1 发布包 437 MB，CI 通过；0.3.2 待 CI |
