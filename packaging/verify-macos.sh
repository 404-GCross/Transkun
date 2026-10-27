#!/usr/bin/env bash
# 验证 macOS 应用包（在 macOS 上运行）。
#
# 用法:
#   bash packaging/verify-macos.sh dist/transkun-0.2.0-arm64.dmg
#
# 检查内容：包结构、Mach-O 启动器、Info.plist、可搬动的 venv、随包权重；
# 把 .app 复制到别处真跑 --version / --check / MIDI 整理自检 / 8 秒端到端转谱；
# 最后从只读挂载的 dmg 里直接启动一次。

set -euo pipefail

DMG="${1:-}"
[ -n "$DMG" ] || { echo "用法: $0 <dmg>" >&2; exit 2; }
[ -f "$DMG" ] || { echo "找不到 $DMG" >&2; exit 2; }
[ "$(uname -s)" = "Darwin" ] || { echo "只能在 macOS 上验证" >&2; exit 2; }

log() { printf '\033[1;32m[verify]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[verify]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[verify]\033[0m %s\n' "$*" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/transkun-verify.XXXXXX")"
MNT="$WORK/mnt"
cleanup() {
  hdiutil detach "$MNT" -quiet 2>/dev/null \
    || hdiutil detach "$MNT" -force -quiet 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

log "挂载 $DMG"
mkdir -p "$MNT"
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" >/dev/null \
  || die "挂载失败"
APP="$MNT/Transkun.app"
[ -d "$APP" ] || die "dmg 里没有 Transkun.app"
RES="$APP/Contents/Resources"
LAUNCHER="$APP/Contents/MacOS/transkun"

log "检查包结构"
[ -x "$LAUNCHER" ] || die "缺少可执行启动器"
file "$LAUNCHER" | grep -q 'Mach-O' || die "启动器不是 Mach-O"
[ -f "$RES/runtime/venv/bin/python" ] || die "缺少包内 Python"
[ -f "$RES/transkun/pretrained/2.0.pt" ] || die "缺少 2.0.pt 权重"
[ -f "$RES/transkun/pretrained/2.0.conf" ] || die "缺少 2.0.conf"
[ -f "$RES/gui/run.sh" ] || die "缺少 gui/run.sh"
[ -f "$APP/Contents/Info.plist" ] || die "缺少 Info.plist"

plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$APP/Contents/Info.plist"; }
[ "$(plist CFBundleIdentifier)" = "com.github.gcross.transkun" ] \
  || die "Bundle ID 不对: $(plist CFBundleIdentifier)"
[ "$(plist LSMinimumSystemVersion)" = "12.0" ] || die "最低系统版本不对"

log "检查 venv 可搬动（pyvenv.cfg 用相对 home）"
home_line="$(grep -E '^home *= *\.\.' "$RES/runtime/venv/pyvenv.cfg" || true)"
[ -n "$home_line" ] || die "pyvenv.cfg 的 home 不是相对路径"
[ -L "$RES/runtime/venv/bin/python" ] || die "venv/bin/python 不是符号链接"

show_log() {
  echo "--- ~/Library/Logs/transkun.log ---" >&2
  tail -80 "$HOME/Library/Logs/transkun.log" 2>/dev/null >&2 || true
}

log "先在挂载卷上启动一次（直接跑 run.sh，绕过启动器）"
if ! TRANSKUN_DEBUG=1 bash "$RES/gui/run.sh" --version; then
  echo "--- codesign ---" >&2
  codesign -dv "$APP" 2>&1 >&2 || true
  codesign --verify --deep --strict "$APP" 2>&1 >&2 || true
  echo "--- xattr ---" >&2
  xattr -l "$APP" 2>&1 >&2 || true
  die "run.sh 在只读卷上无法启动"
fi
mkdir -p "$HOME/Library/Logs"
: > "$HOME/Library/Logs/transkun.log"
if ! TRANSKUN_DEBUG=1 "$APP/Contents/MacOS/transkun" --version >/dev/null; then
  show_log
  echo "--- codesign ---" >&2
  codesign --verify --deep --strict "$APP" 2>&1 >&2 || true
  echo "--- xattr ---" >&2
  xattr -l "$APP" 2>&1 >&2 || true
  die "只读卷上的 .app 无法启动"
fi

log "复制到可写目录并启动（模拟用户拖到 /Applications）"
cp -a "$APP" "$WORK/Transkun.app"
COPY="$WORK/Transkun.app"
touch "$WORK/stamp"
sleep 1
: > "$HOME/Library/Logs/transkun.log"
if ! TRANSKUN_DEBUG=1 "$COPY/Contents/MacOS/transkun" --version >/dev/null; then
  show_log
  die "复制后的 .app 无法启动"
fi
if ! "$COPY/Contents/MacOS/transkun" --check >/dev/null; then
  show_log
  die "复制后的 .app 依赖自检失败"
fi
"$COPY/Contents/MacOS/transkun" --check-fonts >/dev/null \
  || warn "Tk 中文字体冒烟测试失败（无窗口会话时可能误报）"
PYTHONDONTWRITEBYTECODE=1 "$COPY/Contents/Resources/runtime/venv/bin/python" \
  -m transkun_app.midi_rearrange --selftest >/dev/null \
  || die "MIDI 整理自检失败"

log "8 秒端到端转谱（包内启动器）"
PYTHONDONTWRITEBYTECODE=1 "$COPY/Contents/Resources/runtime/venv/bin/python" - <<'PY'
from transkun_app.selftest import make_arpeggio
make_arpeggio(__import__('pathlib').Path('/tmp/transkun-verify.wav'), duration=8.0)
PY
if ! "$COPY/Contents/MacOS/transkun" /tmp/transkun-verify.wav \
    -o "$WORK/verify.mid" >/dev/null; then
  show_log
  die "端到端转谱失败"
fi
[ -s "$WORK/verify.mid" ] || die "没有生成 MIDI"

log "检查运行没有改动包内文件"
changed="$(find "$COPY" -type f -newer "$WORK/stamp" | wc -l | tr -d ' ')"
[ "$changed" = "0" ] || die "运行后包内有 $changed 个文件被改动（只读卷上会失败）"

log "从只读 dmg 直接启动"
"$APP/Contents/MacOS/transkun" --version >/dev/null \
  || die "只读卷上的 .app 无法启动"
"$APP/Contents/MacOS/transkun" --check >/dev/null \
  || die "只读卷上的 .app 依赖自检失败"

log "通过：$(basename "$DMG")"
