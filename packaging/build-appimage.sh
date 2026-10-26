#!/usr/bin/env bash
# 构建 Transkun 的独立 AppImage。
#
# 内置：uv 提供的 Python 3.12 + 程序代码 + CPU 版 PyTorch + 应用依赖。
# 权重在 transkun/pretrained/2.0.pt（54 MB），随包内置。
#
# 用法:
#   VERSION=0.1.0 ./packaging/build-appimage.sh
#
# 需要联网（uv + Python + pip 依赖）。建议在较老的发行版（如 ubuntu:22.04）里
# 构建，以获得更宽的 glibc 兼容性。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-x86_64}"
OUT="$ROOT/dist"
WORK="$ROOT/.appimage"
APPDIR="$WORK/Transkun.AppDir"
UV_PYTHON_INSTALL_DIR="$APPDIR/usr/python"
UV_CACHE_DIR="$WORK/uv-cache"
UV_BIN="$WORK/uv"
export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR

log() { printf '\033[1;32m[appimage]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[appimage]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[appimage]\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

rm -rf "$WORK"
mkdir -p "$APPDIR/usr/share/transkun" "$APPDIR/usr/bin" "$OUT"

# ---------------------------------------------------------------- 程序代码
log "复制程序代码…"
find "$ROOT/transkun" "$ROOT/transkun_app" -type d -name __pycache__ \
  -prune -exec rm -rf {} + 2>/dev/null || true
cp -a "$ROOT/transkun" "$ROOT/transkun_app" "$ROOT/packaging" \
  "$APPDIR/usr/share/transkun/"
for f in setup.py LICENSE README.md VERSION; do
  [ -e "$ROOT/$f" ] && cp -a "$ROOT/$f" "$APPDIR/usr/share/transkun/"
done
SOURCE_DIR="$APPDIR/usr/share/transkun"

# ---------------------------------------------------------------- uv + Python
log "下载 uv…"
uv_arch="$ARCH-unknown-linux-gnu"
fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-${uv_arch}.tar.gz" "$WORK/uv.tar.gz"
tar -xzf "$WORK/uv.tar.gz" -C "$WORK"
found="$(find "$WORK" -maxdepth 2 -type f -name uv | head -1 || true)"
[ -n "$found" ] || die "uv 解压失败"
cp "$found" "$UV_BIN"
chmod 755 "$UV_BIN"

log "安装 Python 3.12（随 AppImage 分发的独立解释器）…"
"$UV_BIN" python install 3.12

log "创建可重定位虚拟环境并安装依赖（CPU）…"
"$UV_BIN" venv --relocatable --python 3.12 --python-preference only-managed "$APPDIR/usr/venv"
VENV_PY="$APPDIR/usr/venv/bin/python"
"$UV_BIN" pip install --python "$VENV_PY" torch torchaudio \
  --index-url https://download.pytorch.org/whl/cpu
"$UV_BIN" pip install --python "$VENV_PY" "$SOURCE_DIR[app]"
# 清掉 setuptools 就地构建留下的产物，别打进 AppImage。
rm -rf "$SOURCE_DIR/build" "$SOURCE_DIR"/*.egg-info

# ---------------------------------------------------------------- 运行时文件
log "写入 AppRun / desktop / 图标…"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export TRANSKUN_USER_CACHE="${TRANSKUN_USER_CACHE:-$HOME/.cache/transkun}"
export TRANSKUN_USER_HOME="${TRANSKUN_USER_HOME:-$HOME/.local/share/transkun}"
# 包内 venv 用的是绝对路径；AppImage 挂载点固定，可以直接用。
if [ "${1:-}" = "--upstream-cli" ]; then
  shift
  exec "$HERE/usr/venv/bin/transkun" "$@"
fi
exec "$HERE/usr/venv/bin/transkun-app" "$@"
EOF
chmod 755 "$APPDIR/AppRun"

cat > "$APPDIR/transkun.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Transkun
Comment=Piano audio to MIDI
Exec=transkun-app
Icon=transkun
Terminal=false
Categories=AudioVideo;Audio;Music;
EOF

cp "$ROOT/packaging/transkun.png" "$APPDIR/transkun.png"

# ---------------------------------------------------------------- 打包
log "下载 appimagetool…"
fetch "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage" \
  "$WORK/appimagetool"
chmod 755 "$WORK/appimagetool"

TARGET="$OUT/transkun-${VERSION}-${ARCH}.AppImage"
log "生成 $TARGET"
ARCH="$ARCH" "$WORK/appimagetool" --appimage-extract-and-run "$APPDIR" "$TARGET"

log "完成: $TARGET"
ls -lh "$TARGET"
