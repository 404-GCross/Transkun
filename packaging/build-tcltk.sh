#!/usr/bin/env bash
# 构建一份带 Xft 的 Tcl/Tk 9.0.x。
#
# uv / python-build-standalone 自带的 Tcl/Tk 是**不带 Xft/fontconfig** 编译的，
# 因此 Tk 看不到系统字体，中文等非 ASCII 文字会显示为空白（只剩 core X 字体）。
# 参见 https://github.com/astral-sh/python-build-standalone/issues/740
#
# 这个脚本编出一份与自带 Tcl 主版本一致的、启用 Xft 的 Tcl/Tk，供
#   * AppImage 内置（packaging/fix_tk_system.py --source ... --mode copy）
#   * deb/rpm 内置（安装到 /usr/share/transkun/tcltk）
# 使用。
#
# 用法:
#   ./packaging/build-tcltk.sh [PREFIX]
#
# PREFIX 默认 $ROOT/.tcltk；产物为 PREFIX/lib/{libtcl9.0.so,libtcl9tk9.0.so,
# tcl9.0/,tk9.0/}。环境变量:
#   TCL_VERSION / TK_VERSION  默认 9.0.4（主版本需与自带 Tcl 一致）
#   JOBS                      并行编译数（默认 nproc）
#
# 构建机依赖（例如 ubuntu:22.04）:
#   build-essential pkg-config zlib1g-dev libx11-dev libxss-dev libxext-dev \
#   libxft-dev libfontconfig1-dev libxrender-dev
#
# 建议在较老的发行版上构建，以获得更宽的 glibc 兼容性。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${1:-${TCLTK_OUT:-$ROOT/.tcltk}}"
TCL_VERSION="${TCL_VERSION:-9.0.4}"
TK_VERSION="${TK_VERSION:-$TCL_VERSION}"
WORK="${TCLTK_WORK:-$ROOT/.tcltk-build}"
if [ -z "${JOBS:-}" ]; then
  if command -v nproc >/dev/null 2>&1; then JOBS="$(nproc)"; else JOBS=4; fi
fi

mkdir -p "$WORK" "$PREFIX"
PREFIX="$(cd "$PREFIX" && pwd)"

log()  { printf '\033[1;32m[tcltk]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[tcltk]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[tcltk]\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

fetch_src() {  # fetch_src FILE DEST  (tries several SourceForge mirrors)
  local file="$1" dest="$2" url
  for url in \
    "https://downloads.sourceforge.net/project/tcl/Tcl/${TCL_VERSION}/${file}" \
    "https://master.dl.sourceforge.net/project/tcl/Tcl/${TCL_VERSION}/${file}" \
    "https://sourceforge.net/projects/tcl/files/Tcl/${TCL_VERSION}/${file}/download"; do
    log "下载 $url"
    if fetch "$url" "$dest"; then return 0; fi
  done
  die "下载失败: $file"
}

# ---------------------------------------------------------------- Tcl
if [ ! -d "$WORK/tcl${TCL_VERSION}" ]; then
  fetch_src "tcl${TCL_VERSION}-src.tar.gz" "$WORK/tcl${TCL_VERSION}-src.tar.gz"
  tar -C "$WORK" -xf "$WORK/tcl${TCL_VERSION}-src.tar.gz"
fi
log "编译安装 Tcl ${TCL_VERSION} -> $PREFIX"
(
  cd "$WORK/tcl${TCL_VERSION}/unix"
  # Tcl 的 make 会用刚编出的 tclsh 配置 in-tree 包；tclsh 动态链接 libtcl9.0.so，
  # 需要能从构建目录找到它。
  export LD_LIBRARY_PATH="$(pwd):$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  ./configure --prefix="$PREFIX" --enable-shared --enable-threads --disable-zipfs
  make -j"$JOBS"
  make install
)

# ---------------------------------------------------------------- Tk（启用 Xft）
if [ ! -d "$WORK/tk${TK_VERSION}" ]; then
  fetch_src "tk${TK_VERSION}-src.tar.gz" "$WORK/tk${TK_VERSION}-src.tar.gz"
  tar -C "$WORK" -xf "$WORK/tk${TK_VERSION}-src.tar.gz"
fi
log "编译安装 Tk ${TK_VERSION}（--enable-xft）-> $PREFIX"
(
  cd "$WORK/tk${TK_VERSION}/unix"
  # Tk 的 configure 会运行刚编译出的 tclsh；它动态链接 libtcl9.0.so，需要能从
  # 构建目录/安装前缀找到该库，否则报 "cannot find a usable native Tcl 9 tclsh"。
  export LD_LIBRARY_PATH="$PREFIX/lib:$WORK/tcl${TCL_VERSION}/unix${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  ./configure --prefix="$PREFIX" --with-tcl="$PREFIX/lib" \
    --enable-shared --enable-threads --disable-zipfs --enable-xft
  make -j"$JOBS"
  make install
)

# ---------------------------------------------------------------- 校验
if [ ! -f "$PREFIX/lib/libtcl9.0.so" ]; then
  die "未生成 libtcl9.0.so；Tcl 构建可能失败。"
fi
if ! ls "$PREFIX/lib"/libtcl*tk*.so >/dev/null 2>&1; then
  warn "未找到 Tk 9 的共享库（libtcl9tk9.0.so），请检查构建输出。"
fi
log "完成: $PREFIX"
ls -1 "$PREFIX/lib" | grep -E '^lib(tcl|tk)' || true
