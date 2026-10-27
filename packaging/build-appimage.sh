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
VERSION="${VERSION:-0.2.0}"
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
# 跳过 torch/torchaudio，避免 PyPI 的默认 CUDA 轮子覆盖 CPU 版。
grep -v -E '^(torch|torchaudio)[[:space:]]*$' "$ROOT/requirements.txt" \
  > "$WORK/requirements-no-torch.txt"
"$UV_BIN" pip install --python "$VENV_PY" -r "$WORK/requirements-no-torch.txt"
"$UV_BIN" pip install --python "$VENV_PY" \
  fastapi uvicorn python-multipart soundfile mido
"$UV_BIN" pip install --python "$VENV_PY" --no-deps "$SOURCE_DIR[app]"
# 清掉 setuptools 就地构建留下的产物，别打进 AppImage。
rm -rf "$SOURCE_DIR/build" "$SOURCE_DIR"/*.egg-info

# ---------------------------------------------------------------- Tcl/Tk + 字体
# uv 自带的 Tcl/Tk 没启用 Xft，中文会显示成空白；内置一份带 Xft 的，并把
# fontconfig/freetype 等依赖复制进来，做到目标机零依赖。
log "内置带 Xft 的 Tcl/Tk（字体支持）…"
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -z "$TCLTK_DIR" ] || [ ! -d "$TCLTK_DIR/lib" ]; then
  TCLTK_DIR="$WORK/tcltk"
  bash "$ROOT/packaging/build-tcltk.sh" "$TCLTK_DIR"
fi
TCLTK_DEST="$APPDIR/usr/share/transkun/tcltk"
mkdir -p "$(dirname "$TCLTK_DEST")"
cp -a "$TCLTK_DIR" "$TCLTK_DEST"
"$VENV_PY" "$ROOT/packaging/fix_tk_system.py" --python "$VENV_PY" \
  --mode copy --source "$TCLTK_DEST" --deps-dir "$TCLTK_DEST/lib" \
  || warn "内置 Tcl/Tk 失败；AppImage 的中文可能显示为空白。"

log "内置中文字体（Noto Sans CJK）…"
font_src=""
for candidate in \
  /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
  /usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf \
  /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc; do
  if [ -e "$candidate" ]; then font_src="$candidate"; break; fi
done
mkdir -p "$APPDIR/usr/share/fonts"
if [ -n "$font_src" ]; then
  cp "$font_src" "$APPDIR/usr/share/fonts/"
  log "字体: $(basename "$font_src")"
else
  warn "未找到 Noto CJK 字体；AppImage 的中文可能显示为空白。"
  warn "构建机请先安装: sudo apt-get install fonts-noto-cjk"
fi

# ---------------------------------------------------------------- ffmpeg
# m4a/aac/opus 解码需要 ffmpeg；AppImage 要做到目标机零依赖，所以内置静态版。
log "内置 ffmpeg（静态版）…"
fetch "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" \
  "$WORK/ffmpeg.tar.xz"
mkdir -p "$WORK/ffmpeg" "$APPDIR/usr/bin"
tar -xJf "$WORK/ffmpeg.tar.xz" -C "$WORK/ffmpeg"
ffmpeg_bin="$(find "$WORK/ffmpeg" -type f -name ffmpeg | head -1 || true)"
if [ -n "$ffmpeg_bin" ]; then
  cp "$ffmpeg_bin" "$APPDIR/usr/bin/ffmpeg"
  chmod 755 "$APPDIR/usr/bin/ffmpeg"
  log "ffmpeg: $("$APPDIR/usr/bin/ffmpeg" -version 2>/dev/null | head -1)"
else
  warn "ffmpeg 解压失败；m4a/aac 需要目标机自行安装 ffmpeg。"
fi

# ---------------------------------------------------------------- 运行时文件
log "写入 AppRun / desktop / 图标…"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export TRANSKUN_USER_CACHE="${TRANSKUN_USER_CACHE:-$HOME/.cache/transkun}"
export TRANSKUN_USER_HOME="${TRANSKUN_USER_HOME:-$HOME/.local/share/transkun}"
export PATH="$HERE/usr/bin:${PATH}"

# 让 fontconfig 扫描包内字体（fontconfig 不读 XDG_DATA_DIRS，需要一份配置）。
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
if [ -z "${FONTCONFIG_FILE:-}" ] && [ -d "$HERE/usr/share/fonts" ]; then
  font_conf="${XDG_CACHE_HOME}/transkun/fonts.conf"
  mkdir -p "$(dirname "$font_conf")"
  cat > "$font_conf" <<FC
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>${HERE}/usr/share/fonts</dir>
  <cachedir>${XDG_CACHE_HOME}/fontconfig</cachedir>
</fontconfig>
FC
  export FONTCONFIG_FILE="$font_conf"
fi

# 包内带 Xft 的 Tcl/Tk 及其字体栈依赖。
TCLTK_LIB="$HERE/usr/share/transkun/tcltk/lib"
if [ -d "$TCLTK_LIB" ]; then
  export LD_LIBRARY_PATH="$TCLTK_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
for d in "$HERE"/usr/python/*/lib; do
  if [ -d "$d" ]; then
    export LD_LIBRARY_PATH="$d${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
done
if [ -z "${TCL_LIBRARY:-}" ] && [ -d "$TCLTK_LIB/tcl9.0" ]; then
  export TCL_LIBRARY="$TCLTK_LIB/tcl9.0"
fi
if [ -z "${TK_LIBRARY:-}" ] && [ -d "$TCLTK_LIB/tk9.0" ]; then
  export TK_LIBRARY="$TCLTK_LIB/tk9.0"
fi

# 包内 venv 用的是绝对路径；AppImage 挂载点固定，可以直接用。
# 固定使用包内代码（venv 里也装了一份，但显式指定更稳）。
export PYTHONPATH="$HERE/usr/share/transkun${PYTHONPATH:+:$PYTHONPATH}"

# AppImage 挂载点与构建路径不同，不能直接 exec venv/bin/transkun-app
# （shebang 指向构建机）。用 venv 的 python -m。
if [ "${1:-}" = "--upstream-cli" ]; then
  shift
  exec "$HERE/usr/venv/bin/python" -c \
    'import sys; sys.argv[0]="transkun"; from transkun.transcribe import main; main()' "$@"
fi
exec "$HERE/usr/venv/bin/python" -m transkun_app.cli "$@"
EOF
chmod 755 "$APPDIR/AppRun"

cat > "$APPDIR/transkun.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Transkun
Comment=Piano audio to MIDI
Exec=transkun-app --gui
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
