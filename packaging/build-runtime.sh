#!/usr/bin/env bash
# 构建 deb/rpm 内置的「离线运行时」压缩包。
#
# 内容 = 一个完整的 ${TRANSKUN_HOME}：
#   python/   uv 提供的独立 Python 3.12（不依赖系统 Python）
#   venv/     私有虚拟环境，含 CPU 版 PyTorch + Transkun + FastAPI/WebUI 依赖
#
# 安装包首次启动时把它解压到 ~/.local/share/transkun，之后完全离线可用。
#
# 产物: dist/transkun-runtime-<version>.tar.zst
#
# 用法:
#   VERSION=0.1.0 ./packaging/build-runtime.sh
#   TORCH_VARIANT=cpu ./packaging/build-runtime.sh   # 默认 cpu；cu124 会让包大 2GB+
#
# 需要联网（uv + Python + pip 依赖）。建议在较老的发行版（如 ubuntu:22.04）里
# 构建，以获得更宽的 glibc 兼容性。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.0.0}"
ARCH="${ARCH:-x86_64}"
OUT="${OUT:-$ROOT/dist}"
WORK="${WORK:-$ROOT/.runtime}"
TORCH_VARIANT="${TORCH_VARIANT:-cpu}"
PYTHON_VERSION="3.12"
ZSTD_LEVEL="${ZSTD_LEVEL:-19}"

RUNTIME_HOME="$WORK/home"        # 打包后即用户的 $TRANSKUN_HOME
VENV_DIR="$RUNTIME_HOME/venv"

log() { printf '\033[1;32m[runtime]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[runtime]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[runtime]\033[0m %s\n' "$*" >&2; exit 1; }

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  else
    wget -O "$2" "$1"
  fi
}

command -v zstd >/dev/null 2>&1 || die "需要 zstd（Debian: apt-get install zstd）"

rm -rf "$WORK"
mkdir -p "$RUNTIME_HOME" "$OUT"

# ---------------------------------------------------------------- uv
UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  else
    log "下载 uv…"
    case "$(uname -m)" in
      x86_64|amd64) uv_arch="x86_64-unknown-linux-gnu" ;;
      aarch64|arm64) uv_arch="aarch64-unknown-linux-gnu" ;;
      *) die "暂不支持的 CPU 架构: $(uname -m)" ;;
    esac
    mkdir -p "$WORK/uv"
    fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-${uv_arch}.tar.gz" "$WORK/uv.tar.gz"
    tar -xzf "$WORK/uv.tar.gz" -C "$WORK/uv"
    UV_BIN="$(find "$WORK/uv" -type f -name uv | head -1 || true)"
    [ -n "$UV_BIN" ] || die "uv 解压失败"
    chmod 755 "$UV_BIN"
  fi
fi
log "uv: $UV_BIN ($("$UV_BIN" --version))"

export UV_PYTHON_INSTALL_DIR="$RUNTIME_HOME/python"
export UV_CACHE_DIR="$WORK/uv-cache"

# ---------------------------------------------------------------- Python + venv
log "安装 Python ${PYTHON_VERSION}（独立解释器，不依赖系统 Python）…"
"$UV_BIN" python install "$PYTHON_VERSION"

log "创建可重定位虚拟环境…"
"$UV_BIN" venv --relocatable --python "$PYTHON_VERSION" \
  --python-preference only-managed "$VENV_DIR"
VENV_PY="$VENV_DIR/bin/python"

case "$TORCH_VARIANT" in
  cpu)
    log "安装 PyTorch（CPU）…"
    "$UV_BIN" pip install --python "$VENV_PY" torch torchaudio \
      --index-url https://download.pytorch.org/whl/cpu
    ;;
  cu124|cu126|cu128)
    log "安装 PyTorch（${TORCH_VARIANT}）…"
    "$UV_BIN" pip install --python "$VENV_PY" torch torchaudio \
      --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}"
    ;;
  *)
    die "TORCH_VARIANT 只能是 cpu / cu124 / cu126 / cu128"
    ;;
esac

log "安装 Transkun + 应用依赖（约 5~15 分钟）…"
# 先按 requirements.txt 装依赖，但跳过 torch/torchaudio：它们已经按 CPU/CUDA
# 变体装好，不能让 PyPI 的默认 CUDA 轮子把它们换掉。
grep -v -E '^(torch|torchaudio)[[:space:]]*$' "$ROOT/requirements.txt" \
  > "$WORK/requirements-no-torch.txt"
"$UV_BIN" pip install --python "$VENV_PY" -r "$WORK/requirements-no-torch.txt"
"$UV_BIN" pip install --python "$VENV_PY" \
  fastapi uvicorn python-multipart soundfile mido
"$UV_BIN" pip install --python "$VENV_PY" --no-deps "$ROOT[app]"
# 清掉 setuptools 就地构建的产物。
rm -rf "$ROOT/build" "$ROOT"/*.egg-info

# ---------------------------------------------------------------- 可重定位化
# uv 会在 python/ 下建一个绝对符号链接（cpython-3.12-linux-x86_64-gnu ->
# cpython-3.12.x-linux-x86_64-gnu）。解压到别的目录后它会变成死链接，改成相对。
for link in "$RUNTIME_HOME"/python/*; do
  [ -L "$link" ] || continue
  target="$(readlink "$link")"
  case "$target" in
    /*) ln -sfn "$(basename "$target")" "$link" ;;
  esac
done
rm -rf "$RUNTIME_HOME/python/.temp" "$RUNTIME_HOME/python/.lock" \
       "$RUNTIME_HOME/python/.gitignore"

# 记下解释器相对位置，run.sh 解压后用它校正 venv（uv 的 venv 存的是构建路径）。
rel_py="$(cd "$RUNTIME_HOME" && ls -d python/*/bin/python3.12 | head -1 || true)"
[ -n "$rel_py" ] || die "未找到运行时解释器"

# 自检：把应用用到的依赖都 import 一遍。
log "运行时自检…"
"$VENV_PY" -m transkun_app.import_check \
  || die "运行时依赖不完整，请检查上面的报错。"
if ! "$VENV_PY" -c 'import tkinter' >/dev/null 2>&1; then
  warn "运行时没有 tkinter：桌面界面不可用（CLI/WebUI 不受影响）。"
fi

printf '%s\n' "$rel_py" > "$VENV_DIR/.transkun-python-relpath"
printf '%s\n' "$VERSION" > "$VENV_DIR/.transkun-runtime-version"
touch "$VENV_DIR/.transkun-installed"

# 随运行时带一份 uv：GPU 用户要在已有 venv 里换装 CUDA 版 PyTorch。
mkdir -p "$RUNTIME_HOME/uv"
cp "$UV_BIN" "$RUNTIME_HOME/uv/uv"
chmod 755 "$RUNTIME_HOME/uv/uv"
printf '%s\n' "$("$UV_BIN" --version 2>/dev/null | awk '{print $2}')" \
  > "$RUNTIME_HOME/uv/VERSION"

# ---------------------------------------------------------------- 打包
target="$OUT/transkun-runtime-${VERSION}.tar.zst"
log "压缩运行时（zstd -${ZSTD_LEVEL}）…"
tar -C "$RUNTIME_HOME" -cf - python venv uv \
  | zstd -q -"$ZSTD_LEVEL" -T0 -o "$target" -f
ls -lh "$target"
log "完成: $target"
