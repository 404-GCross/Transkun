#!/usr/bin/env bash
# 启动 Transkun（应用 CLI / WebUI）。
#
# 用法:
#   transkun-app song.mp3 -o song.mid
#   transkun-app --web --lan
#
# deb/rpm 包内置离线运行时（runtime.tar.zst）：首次启动解压到
# ~/.local/share/transkun，之后完全离线。源码目录直接使用 .venv 或系统 Python。
#
# 上游模型 CLI 由 /usr/bin/transkun 调用本脚本的 --upstream-cli 分支。

set -euo pipefail
[ "${TRANSKUN_DEBUG:-0}" = 1 ] && set -x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${TRANSKUN_VENV_DIR:-$REPO_DIR/.venv}"

IS_DARWIN=0
[ "$(uname -s)" = "Darwin" ] && IS_DARWIN=1

INSTALLED=0
[ "$SCRIPT_DIR" = "/usr/share/transkun" ] && INSTALLED=1

APP_RES=""
case "$SCRIPT_DIR" in
  # macOS .app: run.sh 在 Contents/Resources/gui/ 下
  */Contents/Resources/gui) APP_RES="${SCRIPT_DIR%/gui}" ;;
  */Contents/Resources) APP_RES="$SCRIPT_DIR" ;;
esac
[ -n "$APP_RES" ] && INSTALLED=1

# 固定使用本包内的代码（避免 CWD 里的同名模块抢先）。
export PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"

if [ "$IS_DARWIN" = 1 ]; then
  TRANSKUN_USER_HOME="${TRANSKUN_USER_HOME:-$HOME/Library/Application Support/Transkun}"
else
  TRANSKUN_USER_HOME="${TRANSKUN_USER_HOME:-$HOME/.local/share/transkun}"
fi

RUNTIME_TAR="${TRANSKUN_RUNTIME_TARBALL:-}"
if [ -n "$APP_RES" ]; then
  TRANSKUN_HOME="${TRANSKUN_HOME:-$APP_RES/runtime}"
  VENV_DIR="${TRANSKUN_VENV_DIR:-$TRANSKUN_HOME/venv}"
elif [ "$INSTALLED" = 1 ] || [ -n "$RUNTIME_TAR" ]; then
  TRANSKUN_HOME="${TRANSKUN_HOME:-$TRANSKUN_USER_HOME}"
  if [ -z "$RUNTIME_TAR" ]; then
    RUNTIME_TAR="$REPO_DIR/runtime.tar.zst"
  fi
  VENV_DIR="${TRANSKUN_VENV_DIR:-$TRANSKUN_HOME/venv}"
  if [ -f "$RUNTIME_TAR" ] && [ "$VENV_DIR" != "$TRANSKUN_HOME/venv" ]; then
    echo "提示: 内置运行时固定展开到 $TRANSKUN_HOME/venv（忽略 TRANSKUN_VENV_DIR=${VENV_DIR}）" >&2
    VENV_DIR="$TRANSKUN_HOME/venv"
  fi
fi

PKG_VERSION=""
if [ -f "$REPO_DIR/VERSION" ]; then
  PKG_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || true)"
fi

runtime_is_current() {
  [ -x "$VENV_DIR/bin/python" ] || return 1
  [ -e "$VENV_DIR/.transkun-installed" ] || return 1
  if [ -n "$PKG_VERSION" ] && [ -f "$VENV_DIR/.transkun-runtime-version" ]; then
    [ "$(cat "$VENV_DIR/.transkun-runtime-version")" = "$PKG_VERSION" ] || return 1
  fi
  return 0
}

if [ -n "$APP_RES" ]; then
  export PYTHONDONTWRITEBYTECODE=1
fi

if [ "${TRANSKUN_FORCE_RUNTIME:-0}" = 1 ] || ! runtime_is_current; then
  if [ -f "$RUNTIME_TAR" ]; then
    if ! tar --zstd -tf "$RUNTIME_TAR" >/dev/null 2>&1; then
      echo "无法读取内置运行时（需要 zstd）：$RUNTIME_TAR" >&2
      echo "  Fedora: sudo dnf install zstd    Debian/Ubuntu: sudo apt install zstd" >&2
      exit 1
    fi
    echo "正在展开内置运行时（离线，约 1GB，需要 1~2 分钟）…" >&2
    mkdir -p "$TRANSKUN_HOME"
    avail_kb="$(df -Pk "$TRANSKUN_HOME" 2>/dev/null | awk 'NR==2{print $4}')"
    if [ -n "$avail_kb" ] && [ "$avail_kb" -lt 2000000 ]; then
      echo "磁盘空间不足：$TRANSKUN_HOME 需要约 1.5GB，当前只剩 $((avail_kb / 1024))MB。" >&2
      exit 1
    fi
    case "${VENV_DIR##*/}" in
      venv) rm -rf "$VENV_DIR" ;;
      *) echo "拒绝删除非 venv 目录: ${VENV_DIR}" >&2; exit 1 ;;
    esac
    tar --zstd -xf "$RUNTIME_TAR" -C "$TRANSKUN_HOME"
    if "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
      [ -n "$PKG_VERSION" ] && printf '%s\n' "$PKG_VERSION" \
        > "$VENV_DIR/.transkun-runtime-version"
      touch "$VENV_DIR/.transkun-installed"
      echo "内置运行时已就绪: $VENV_DIR" >&2
    else
      echo "内置运行时展开后不可用。" >&2
      exit 1
    fi
  fi
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  if [ -n "$APP_RES" ]; then
    echo "应用包内的运行时不可用: $VENV_DIR" >&2
    echo "请重新下载安装 Transkun。" >&2
    exit 1
  fi
  # 源码目录：优先用 .venv，其次系统 Python。
  if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    VENV_DIR="$REPO_DIR/.venv"
  elif command -v python3 >/dev/null 2>&1; then
    if [ "${1:-}" = "--upstream-cli" ]; then
      shift
      exec python3 -c 'import sys; sys.argv[0]="transkun"; from transkun.transcribe import main; main()' "$@"
    fi
    exec python3 -m transkun_app.cli "$@"
  else
    echo "未找到可用的 Python 环境: $VENV_DIR" >&2
    exit 1
  fi
fi

# 带 Xft 的 Tcl/Tk（deb/rpm 安装到 /usr/share/transkun/tcltk）。uv 自带的 Tk
# 没有 Xft，中文会显示成空白；macOS 的 Tk 走 CoreText，不需要这套。
TRANSKUN_TCLTK_DIR="${TRANSKUN_TCLTK_DIR:-/usr/share/transkun/tcltk}"
if [ "$IS_DARWIN" = 0 ]; then
  if [ -z "${TCL_LIBRARY:-}" ] && [ -d "$TRANSKUN_TCLTK_DIR/lib/tcl9.0" ]; then
    export TCL_LIBRARY="$TRANSKUN_TCLTK_DIR/lib/tcl9.0"
  fi
  if [ -z "${TK_LIBRARY:-}" ] && [ -d "$TRANSKUN_TCLTK_DIR/lib/tk9.0" ]; then
    export TK_LIBRARY="$TRANSKUN_TCLTK_DIR/lib/tk9.0"
  fi
  if [ -d "$TRANSKUN_TCLTK_DIR/lib" ] && [ -f "$SCRIPT_DIR/fix_tk_system.py" ]; then
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/fix_tk_system.py" \
      --python "$VENV_DIR/bin/python" --source "$TRANSKUN_TCLTK_DIR" --quiet || true
  fi
fi

# 没有随包 Tcl/Tk 时，退回 venv 自带的脚本目录。
if [ -z "${TCL_LIBRARY:-}" ] || [ -z "${TK_LIBRARY:-}" ]; then
  read -r TCL_DIR TK_DIR < <("$VENV_DIR/bin/python" - <<'PY'
import os
import sys
tcl = tk = ''
for name in ('tcl8.6', 'tcl8.7', 'tcl9.0'):
    candidate = os.path.join(sys.base_prefix, 'lib', name)
    if os.path.isdir(candidate):
        tcl = candidate
        break
for name in ('tk8.6', 'tk8.7', 'tk9.0'):
    candidate = os.path.join(sys.base_prefix, 'lib', name)
    if os.path.isdir(candidate):
        tk = candidate
        break
print(tcl, tk)
PY
)
  [ -n "${TCL_LIBRARY:-}" ] || { [ -n "${TCL_DIR:-}" ] && export TCL_LIBRARY="$TCL_DIR"; }
  [ -n "${TK_LIBRARY:-}" ] || { [ -n "${TK_DIR:-}" ] && export TK_LIBRARY="$TK_DIR"; }
fi

# 用 `python -m` 而不是 venv/bin/transkun-app：后者的 shebang 指向构建机路径，
# 运行时解压到 ~/.local/share/transkun 后会失效。
if [ "${1:-}" = "--upstream-cli" ]; then
  shift
  exec "$VENV_DIR/bin/python" -c \
    'import sys; sys.argv[0]="transkun"; from transkun.transcribe import main; main()' "$@"
fi

exec "$VENV_DIR/bin/python" -m transkun_app.cli "$@"
