#!/usr/bin/env bash
# Transkun 一键安装（Linux x86_64 / macOS Apple Silicon）
#
# 用法：
#   curl -fsSL https://gh-proxy.com/https://github.com/404-GCross/transkun/releases/latest/download/install.sh | bash
#   bash install.sh --dry-run                 # 只打印解析到的版本、URL 和将执行的命令
#   bash install.sh --version 0.3.1           # 固定版本
#   bash install.sh --file ./xxx.deb --yes    # 离线：装本地包
#   bash install.sh --cuda cu124              # 装完顺手换成 CUDA 版 PyTorch
#   bash install.sh --uninstall
#
# 设计原则：
#   - 下载和校验都在普通用户下完成，只有最后一条安装命令用 sudo。
#   - 校验和对不上、架构不对、系统不认识——一律停下，不猜。
#   - 不静默开服务、不改防火墙、不删用户数据。

set -euo pipefail

REPO="404-GCross/Transkun"
BUNDLE_ID="com.github.gcross.transkun"

VERSION=""
LOCAL_FILE=""
DRY_RUN=0
ASSUME_YES=0
UNINSTALL=0
FORCE_MODE=""
CUDA=""
MIRROR="${TRANSKUN_MIRROR:-}"
MIRRORS="${TRANSKUN_MIRRORS:-}"
NO_PROBE=0
INSTALL_PATH=""

say() { printf '\033[1;32m[transkun]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[transkun]\033[0m %s\n' "$*" >&2; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m[transkun] %s\033[0m\n' "$*" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '   %s\n' "$*"
    return 0
  fi
  "$@"
}

usage() {
  cat <<'EOF'
Transkun 一键安装（Linux x86_64 / macOS Apple Silicon）

  --version X        指定版本（默认取最新 release）
  --file PATH        安装本地包（.deb / .rpm / .AppImage / .dmg），不下载
  --appimage         Linux 上强制用免 root 的 AppImage
  --cuda WHICH       cpu | cu124 | cu126 | cu128（默认不装，只打印命令）
  --mirror URL       下载前缀，例如 https://gh-proxy.com/（单个，显式指定，不测速）
  --no-probe         不测速，直接用 GitHub 直连
  --dry-run          只打印会做什么，不下载、不安装
  --yes, -y          不询问（非交互环境本来就不问）
  --uninstall        卸载（保留用户数据）
  -h, --help         看这个

环境变量：
  TRANSKUN_MIRROR    单个镜像前缀，等价于 --mirror（不测速）
  TRANSKUN_MIRRORS   多个前缀，逗号分隔，会一起测速，例如
                TRANSKUN_MIRRORS="https://a.example/,https://b.example/"

也可以把它们写进 ~/.config/transkun/mirror.conf（TRANSKUN_MIRRORS 或
TRANSKUN_GH_PROXY，和 install_cuda.sh 共用同一个文件）。

下载源都连不上、而你是在终端里跑的时候，脚本会直接问你要不要填一个。
EOF
}

# ------------------------------------------------------------------ 系统识别

detect_os() {
  local os
  os="${TRANSKUN_TEST_OS:-$(uname -s)}"
  case "$os" in
    Darwin) OS=macos ;;
    Linux) OS=linux ;;
    *) die "只支持 Linux 和 macOS（当前 ${os}）" ;;
  esac
}

detect_arch() {
  local arch
  arch="${TRANSKUN_TEST_ARCH:-$(uname -m)}"
  case "${OS}:${arch}" in
    linux:x86_64 | linux:amd64) ARCH=x86_64 ;;
    macos:arm64 | macos:aarch64) ARCH=arm64 ;;
    linux:aarch64 | linux:arm64)
      die "Linux 只发 x86_64 包；arm64 服务器请用 Docker 镜像或源码安装" ;;
    macos:x86_64)
      die "macOS 只支持 Apple Silicon（M 系列），Intel Mac 没有可用的 TensorFlow 轮子" ;;
    *) die "不支持的架构：${arch}" ;;
  esac
}

detect_distro() {
  if [ "$OS" = macos ]; then
    DISTRO=macos
    PACKAGE=""
    return
  fi
  local distro
  distro="${TRANSKUN_TEST_DISTRO:-}"
  if [ -z "$distro" ] && [ -r /etc/os-release ]; then
    # 在子 shell 里 source，避免覆盖本脚本的 VERSION。
    distro="$(. /etc/os-release >/dev/null 2>&1
              printf '%s %s' "${ID:-}" "${ID_LIKE:-}")"
  fi
  distro="$(printf '%s' "$distro" | tr 'A-Z' 'a-z')"
  DISTRO="${distro:-unknown}"
  case " ${distro} " in
    *" debian "* | *" ubuntu "* | *" linuxmint "* | *" pop "*) PACKAGE=deb ;;
    *" fedora "* | *" rhel "* | *" centos "* | *" rocky "* | *" almalinux "* | *" nobara "*)
      PACKAGE=rpm ;;
    *) PACKAGE="" ;;
  esac
}

check_macos_version() {
  local version major
  version="${TRANSKUN_TEST_MACOS:-$(sw_vers -productVersion 2>/dev/null || true)}"
  [ -n "$version" ] || die "读不到 macOS 版本"
  major="${version%%.*}"
  case "$major" in
    '' | *[!0-9]*) die "读不懂 macOS 版本：${version}" ;;
  esac
  [ "$major" -ge 12 ] || die "需要 macOS 12 (Monterey) 或更新（当前 ${version}）"
}

# ------------------------------------------------------------------ 参数

while [ $# -gt 0 ]; do
  case "$1" in
    --version)
      [ $# -ge 2 ] || die "--version 需要一个版本号"
      VERSION="${2#v}"
      shift 2
      ;;
    --file)
      [ $# -ge 2 ] || die "--file 需要一个路径"
      LOCAL_FILE="$2"
      shift 2
      ;;
    --appimage)
      FORCE_MODE=appimage
      shift
      ;;
    --cuda)
      [ $# -ge 2 ] || die "--cuda 需要 cpu / cu124 / cu126 / cu128"
      CUDA="$2"
      shift 2
      ;;
    --mirror)
      [ $# -ge 2 ] || die "--mirror 需要一个前缀 URL"
      MIRROR="$2"
      shift 2
      ;;
    --no-probe)
      NO_PROBE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --yes | -y)
      ASSUME_YES=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "未知参数：$1（--help 看用法）"
      ;;
  esac
done

case "$CUDA" in
  '' | cpu | cu124 | cu126 | cu128) ;;
  *) die "--cuda 只能是 cpu / cu124 / cu126 / cu128" ;;
esac

load_mirror_conf() {
  local conf="${XDG_CONFIG_HOME:-$HOME/.config}/transkun/mirror.conf"
  [ -f "$conf" ] || return 0
  [ -n "$MIRROR" ] && return 0
  [ -n "$MIRRORS" ] && return 0
  local from_conf=""
  # 在子 shell 里读，避免配置文件改到本脚本的变量。
  # 兼容 install_cuda.sh 用的 TRANSKUN_GH_PROXY，也支持多条 TRANSKUN_MIRRORS。
  from_conf="$(. "$conf" >/dev/null 2>&1
               printf '%s' "${TRANSKUN_MIRRORS:-${TRANSKUN_GH_PROXY:-}}")"
  if [ -n "$from_conf" ]; then
    MIRRORS="$from_conf"
    say "镜像配置：${conf}"
  fi
}

# ------------------------------------------------------------------ 检测

step "检查系统"
detect_os
detect_arch
detect_distro
if [ "$OS" = macos ]; then
  check_macos_version
fi

SUDO=""
SUDO_KEEPALIVE=""

if [ -n "$LOCAL_FILE" ]; then
  [ -f "$LOCAL_FILE" ] || die "文件不存在：${LOCAL_FILE}"
  LOCAL_FILE="$(cd "$(dirname "$LOCAL_FILE")" && pwd)/$(basename "$LOCAL_FILE")"
  case "$LOCAL_FILE" in
    *.deb) MODE=deb ;;
    *.rpm) MODE=rpm ;;
    *.AppImage) MODE=appimage ;;
    *.dmg) MODE=dmg ;;
    *) die "认不出的包类型：${LOCAL_FILE}" ;;
  esac
elif [ "$OS" = macos ]; then
  MODE=dmg
elif [ -n "$FORCE_MODE" ]; then
  MODE="$FORCE_MODE"
elif [ "$PACKAGE" = deb ] || [ "$PACKAGE" = rpm ]; then
  MODE="$PACKAGE"
else
  MODE=appimage
  warn "没认出发行版（${DISTRO}），改用免 root 的 AppImage。"
fi

if [ "$OS" = macos ] && [ "$MODE" != dmg ]; then
  die "macOS 只能装 .dmg（当前 ${MODE}）"
fi
if [ "$OS" = linux ] && [ "$MODE" = dmg ]; then
  die "Linux 不能装 .dmg"
fi

say "系统：${OS} / ${ARCH}（${DISTRO}）"
say "方式：${MODE}"

need_root() {
  case "$MODE" in
    deb | rpm) [ "$(id -u)" -ne 0 ] ;;
    *) return 1 ;;
  esac
}

ensure_root_access() {
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    warn "当前是 root，安装会直接写系统目录。"
    return 0
  fi
  SUDO=""
  need_root || return 0

  SUDO="sudo"
  command -v sudo >/dev/null 2>&1 \
    || die "装 .${MODE} 需要 root，但系统里没有 sudo。用 root 跑，或改用 --appimage。"
  if [ "$DRY_RUN" = 1 ]; then
    say "（dry-run）真跑时会在下载之前先要一次 sudo 密码。"
    return 0
  fi

  # 一开始就要密码：用户可能把安装挂后台，等到安装那一刻再问会卡住。
  say "需要管理员权限安装系统包：先确认一次密码，之后不再打断。"
  if ! sudo -v; then
    die "没有拿到 sudo 权限（挂后台时 sudo 也没法问密码）。已中止，什么都没改。
       也可以先 sudo -v，或者用 --appimage 免 root 安装。"
  fi
  # sudo 默认 5 分钟就过期；1GB 的包下载+安装可能更久，定期续期，
  # 免得到安装那一步又弹出密码提示。
  (
    while sleep 50; do
      sudo -n true 2>/dev/null || exit 0
    done
  ) &
  SUDO_KEEPALIVE=$!
}

ensure_root_access

# ------------------------------------------------------------------ 卸载

detach() {
  hdiutil detach "$1" -quiet 2>/dev/null \
    || hdiutil detach "$1" -force -quiet 2>/dev/null || true
}

do_uninstall() {
  if [ "$OS" = macos ]; then
    local target="/Applications/Transkun.app"
    [ -d "$target" ] || target="${HOME}/Applications/Transkun.app"
    if [ -d "$target" ]; then
      run rm -rf "$target"
    else
      warn "没找到已安装的 Transkun.app"
    fi
  elif [ "$MODE" = appimage ]; then
    run rm -f "${HOME}/.local/bin/transkun-app" \
      "${HOME}/.local/bin/transkun" \
      "${HOME}/.local/share/applications/transkun.desktop" \
      "${HOME}/.local/share/icons/hicolor/256x256/apps/transkun.png"
  elif [ "$MODE" = deb ]; then
    if command -v apt-get >/dev/null 2>&1 || [ "$DRY_RUN" = 1 ]; then
      run $SUDO apt-get remove -y transkun
    else
      die "找不到 apt-get，无法卸载 .deb"
    fi
  elif [ "$MODE" = rpm ]; then
    if command -v dnf >/dev/null 2>&1; then
      run $SUDO dnf remove -y transkun
    elif command -v yum >/dev/null 2>&1; then
      run $SUDO yum remove -y transkun
    elif [ "$DRY_RUN" = 1 ]; then
      run $SUDO dnf remove -y transkun
    else
      die "找不到 dnf / yum，无法卸载 .rpm"
    fi
  fi
  say "用户数据没动：~/.cache/transkun、~/.config/transkun（要清就手动 rm）。"
}

if [ "$UNINSTALL" = 1 ]; then
  step "卸载"
  do_uninstall
  exit 0
fi

# ------------------------------------------------------------------ 版本与下载

resolve_version() {
  [ -n "$VERSION" ] && return 0
  local tag=""
  tag="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)" || true
  if [ -z "$tag" ]; then
    local location=""
    location="$(curl -fsSI "https://github.com/${REPO}/releases/latest" 2>/dev/null \
      | awk 'tolower($1) == "location:" { print $2 }' | tr -d '\r' | head -1)" || true
    tag="${location##*/}"
  fi
  case "$tag" in
    v[0-9]*) VERSION="${tag#v}" ;;
    *)
      die "解析不到最新版本（直连 GitHub 不通？）。
       加 --version X.Y.Z（例如 0.3.3）跳过这一步，或用 --mirror 指定下载前缀。
       注意：版本号必须自己给——api.github.com 走不了 gh-proxy 这类前缀。" ;;

  esac
}

asset_name() {
  case "$MODE" in
    deb) printf 'transkun_%s_amd64.deb\n' "$VERSION" ;;
    rpm) printf 'transkun-%s-1.x86_64.rpm\n' "$VERSION" ;;
    appimage) printf 'transkun-%s-x86_64.AppImage\n' "$VERSION" ;;
    dmg) printf 'transkun-%s-arm64.dmg\n' "$VERSION" ;;
  esac
}

sha256_check() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c -
  else
    shasum -a 256 -c -
  fi
}

format_speed() {  # bytes/s -> 人能读的速度
  awk -v bps="${1:-0}" 'BEGIN {
    if (bps >= 1048576) printf "%.1f MB/s", bps / 1048576;
    else if (bps >= 1024) printf "%.0f KB/s", bps / 1024;
    else printf "%.0f B/s", bps;
  }'
}

probe_speed() {  # URL -> bytes/s（0 = 不可用/太慢）
  local url="$1" speed=""
  # Range 取样 256 KB；支持 Range 的源（GitHub）几乎瞬间完成，不支持的
  # （某些镜像）最多也只采样 5 秒——相对 1GB 的下载可以忽略。
  # 注意不要用 --max-filesize：不认 Range 的源会因 Content-Length 超限
  # 直接退 63、一个字节都不收，测出来永远是 0。
  speed="$(curl -fsSL --connect-timeout 4 --max-time 5 \
    --range 0-262143 -o /dev/null -w '%{speed_download}' "$url" 2>/dev/null)" \
    || true
  case "$speed" in
    '' | *[!0-9.]*) printf '0\n' ;;
    *) printf '%s\n' "$speed" ;;
  esac
}

build_candidates() {  # 填 CANDIDATES（每项是 "标签|前缀"）
  local raw="" item prefix index=0
  CANDIDATES=()

  if [ -n "$MIRROR" ]; then
    # --mirror / TRANSKUN_MIRROR：单个，显式，不测速；直连留作兜底。
    CANDIDATES=("自定义|${MIRROR%/}/" "GitHub 直连|")
    return 0
  fi

  if [ -n "$MIRRORS" ]; then
    raw="$(printf '%s' "$MIRRORS" | tr ',' ' ')"
    for item in $raw; do
      prefix="${item%/}/"
      case "$prefix" in
        http://* | https://*) ;;
        *)
          warn "忽略不是 http(s) 的镜像：${item}"
          continue
          ;;
      esac
      index=$((index + 1))
      CANDIDATES+=("自定义${index}|${prefix}")
      if [ "$index" -ge 5 ]; then
        warn "一次最多测 5 个自定义镜像，其余忽略。"
        break
      fi
    done
    # 自定义源之外，直连永远留一条兜底。
    CANDIDATES+=("GitHub 直连|")
    return 0
  fi

  # 没配置就用内置的两个：直连 + 文档里的 gh-proxy。
  CANDIDATES=("GitHub 直连|" "gh-proxy|https://gh-proxy.com/")
}

select_source() {  # 资产 URL -> 设置 CHOSEN_PREFIX
  local url="$1"
  CHOSEN_PREFIX=""
  build_candidates

  if [ -n "$MIRROR" ]; then
    CHOSEN_PREFIX="${CANDIDATES[0]#*|}"
    say "下载源：${CHOSEN_PREFIX}（--mirror，不测速）"
    return 0
  fi
  if [ "$NO_PROBE" = 1 ]; then
    CHOSEN_PREFIX=""
    say "下载源：GitHub 直连（--no-probe）"
    return 0
  fi

  local best_prefix="" best_speed=0
  local item label prefix speed

  say "测速（各取 256 KB）："
  for item in "${CANDIDATES[@]}"; do
    label="${item%%|*}"
    prefix="${item#*|}"
    speed="$(probe_speed "${prefix}${url}")"
    if awk -v value="$speed" 'BEGIN { exit !(value > 0) }'; then
      printf '       %s: %s\n' "$label" "$(format_speed "$speed")"
    else
      printf '       %s: 不可用\n' "$label"
    fi
    if awk -v value="$speed" -v best="$best_speed" \
      'BEGIN { exit !(value > best) }'; then
      best_speed="$speed"
      best_prefix="$prefix"
    fi
  done

  if awk -v value="$best_speed" 'BEGIN { exit !(value > 0) }'; then
    CHOSEN_PREFIX="$best_prefix"
    if [ -z "$best_prefix" ]; then
      say "选中最快：GitHub 直连（$(format_speed "$best_speed")）"
    else
      say "选中最快：${best_prefix}（$(format_speed "$best_speed")）"
    fi
    return 0
  fi

  # 都测不出来：在终端里跑就直接问一句，让用户自己填镜像。
  if [ -c /dev/tty ] && { : >/dev/tty; } 2>/dev/null; then
    printf '       都连不上。还有别的镜像前缀吗？（直接回车放弃）: ' >/dev/tty
    local typed=""
    read -r typed </dev/tty || true
    if [ -n "$typed" ]; then
      CHOSEN_PREFIX="${typed%/}/"
      say "使用你填的源：${CHOSEN_PREFIX}"
      return 0
    fi
  fi

  # 测速只是参考：都测不出来时先用直连，下载阶段失败会自动换镜像。
  CHOSEN_PREFIX=""
  warn "测速没有结果（网络慢或被挡）；先用直连，失败会自动换镜像。"
  warn "也可以自己指定：--mirror https://your-mirror/ 或 TRANSKUN_MIRRORS=\"a/,b/\"。"
}

verify_checksum() {  # 资产文件名
  local name="$1"
  local sums_url="https://github.com/${REPO}/releases/download/v${VERSION}/SHA256SUMS"

  say "校验 SHA256"
  # 校验和优先直连 GitHub：镜像就算作恶也改不了包（直连不通才退回镜像）。
  if ! curl -fsSL -o "${WORK}/SHA256SUMS" "$sums_url"; then
    if [ -z "$CHOSEN_PREFIX" ] \
      || ! curl -fsSL -o "${WORK}/SHA256SUMS" "${CHOSEN_PREFIX}${sums_url}"; then
      die "下载 SHA256SUMS 失败"
    fi
    warn "校验和来自镜像（${CHOSEN_PREFIX}），无法独立验证。"
  fi
  ( cd "${WORK}" && grep -F "dist/${name}" SHA256SUMS | sha256_check ) \
    || die "SHA256 校验失败，已中止：${name}"
}

download_asset() {  # 资产文件名
  local name="$1"
  local url="https://github.com/${REPO}/releases/download/v${VERSION}/${name}"

  say "下载 ${name}"
  select_source "$url"
  if [ "$DRY_RUN" = 1 ]; then
    say "   ${CHOSEN_PREFIX}${url}"
    say "   SHA256SUMS 优先直连 GitHub（取不到才用镜像）"
    return 0
  fi

  mkdir -p "${WORK}/dist"
  # 首选失败就按候选顺序换下一个源重试。
  local order=("$CHOSEN_PREFIX")
  local item prefix
  for item in "${CANDIDATES[@]}"; do
    prefix="${item#*|}"
    if [ "$prefix" = "$CHOSEN_PREFIX" ]; then
      continue
    fi
    order+=("$prefix")
  done

  local prefix ok=0
  for prefix in "${order[@]}"; do
    # --connect-timeout：直连被墙时别干等 curl 默认的几分钟才换源。
    if curl -fL --connect-timeout 15 --retry 2 --progress-bar \
      -o "${WORK}/dist/${name}" "${prefix}${url}"; then
      ok=1
      break
    fi
    warn "从 ${prefix:-GitHub 直连} 下载失败，换一个源重试…"
  done
  [ "$ok" = 1 ] || die "下载失败：${url}（可用 --mirror 指定前缀）"

  verify_checksum "$name"
}

WORK=""
cleanup() {
  if [ -n "$SUDO_KEEPALIVE" ]; then
    kill "$SUDO_KEEPALIVE" 2>/dev/null || true
  fi
  if [ -n "$WORK" ]; then
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT
WORK="$(mktemp -d "${TMPDIR:-/tmp}/transkun-install.XXXXXX")"

load_mirror_conf

step "确定版本"
if [ -n "$LOCAL_FILE" ]; then
  say "本地文件：${LOCAL_FILE}"
else
  resolve_version
  say "版本：${VERSION}"
fi

if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
  printf '[transkun] 继续？[Y/n] '
  read -r answer
  case "$answer" in
    n | N | no | NO) say "已取消"; exit 0 ;;
  esac
fi

if [ -n "$LOCAL_FILE" ]; then
  PACKAGE_PATH="$LOCAL_FILE"
else
  NAME="$(asset_name)"
  download_asset "$NAME"
  PACKAGE_PATH="${WORK}/dist/${NAME}"
  if [ "$DRY_RUN" = 0 ]; then
    say "已下载并校验：${NAME}"
  fi
fi

# ------------------------------------------------------------------ 安装

install_deb_or_rpm() {
  local path="$1"
  if [ "$MODE" = deb ]; then
    if command -v apt-get >/dev/null 2>&1; then
      run $SUDO apt-get install -y "$path"
    elif command -v apt >/dev/null 2>&1; then
      run $SUDO apt install -y "$path"
    elif [ "$DRY_RUN" = 1 ]; then
      # dry-run 可能在别的发行版上跑：只打印，不要求本机真的装了 apt。
      run $SUDO apt-get install -y "$path"
    else
      die "找不到 apt / apt-get，无法安装 .deb"
    fi
  else
    if command -v dnf >/dev/null 2>&1; then
      run $SUDO dnf install -y "$path"
    elif command -v yum >/dev/null 2>&1; then
      run $SUDO yum install -y "$path"
    elif [ "$DRY_RUN" = 1 ]; then
      run $SUDO dnf install -y "$path"
    else
      die "找不到 dnf / yum，无法安装 .rpm"
    fi
  fi
}

install_appimage() {
  local path="$1"
  local bin="${HOME}/.local/bin"
  local target="${bin}/transkun-app"
  local upstream="${bin}/transkun"
  local icon_dir="${HOME}/.local/share/icons/hicolor/256x256/apps"
  local desktop="${HOME}/.local/share/applications/transkun.desktop"

  say "安装到 ${target}（不需要 root）"
  run mkdir -p "$bin"
  run cp "$path" "$target"
  run chmod 755 "$target"
  # 上游 CLI 的参数形式（transkun in.wav out.mid）保持不变。
  if [ "$DRY_RUN" = 1 ]; then
    printf '   %s\n' "cat > ${upstream} <<'EOF' ... exec ${target} --upstream-cli \"\$@\""
  else
    cat > "$upstream" <<EOF
#!/bin/sh
exec "${target}" --upstream-cli "\$@"
EOF
    chmod 755 "$upstream"
  fi
  INSTALL_PATH="$target"
  if [ "$DRY_RUN" = 1 ]; then
    return 0
  fi

  # 图标从 AppImage 里取；失败也不影响命令行使用。
  (
    cd "$WORK"
    "$target" --appimage-extract transkun.png \
      && mkdir -p "$icon_dir" \
      && cp squashfs-root/transkun.png "$icon_dir/transkun.png"
  ) >/dev/null 2>&1 || true

  if [ -f "${icon_dir}/transkun.png" ]; then
    mkdir -p "$(dirname "$desktop")"
    cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Transkun
Name[zh_CN]=Transkun
Comment=AI music transcription to MIDI
Comment[zh_CN]=把音频转写成 MIDI
Exec=${target} %F
Icon=transkun
Terminal=false
Categories=AudioVideo;Audio;Music;
MimeType=audio/mpeg;audio/x-wav;audio/flac;audio/ogg;audio/mp4;
EOF
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database "$(dirname "$desktop")" >/dev/null 2>&1 || true
    fi
  fi
  case ":${PATH}:" in
    *":${bin}:"*) ;;
    *) warn "${bin} 不在 PATH 里；加进 PATH 或直接用完整路径运行。" ;;
  esac
}

install_macos_dmg() {
  local dmg="$1"
  local mount="${WORK}/mnt"
  local target="/Applications/Transkun.app"

  say "挂载 ${dmg##*/} 并复制到 /Applications"
  run mkdir -p "$mount"
  if [ "$DRY_RUN" = 1 ]; then
    say "   hdiutil attach ... ; cp -a \"Transkun.app\" /Applications/"
    INSTALL_PATH="$target"
    return 0
  fi

  hdiutil attach "$dmg" -nobrowse -readonly -mountpoint "$mount" >/dev/null \
    || die "挂载 dmg 失败"
  local source="${mount}/Transkun.app"
  if [ ! -d "$source" ]; then
    detach "$mount"
    die "dmg 里没有 Transkun.app"
  fi

  if [ -e "$target" ]; then
    local existing=""
    existing="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
      "${target}/Contents/Info.plist" 2>/dev/null || true)"
    if [ "$existing" != "$BUNDLE_ID" ]; then
      detach "$mount"
      die "${target} 已存在且不是 Transkun，先手动处理它"
    fi
    rm -rf "$target"
  fi

  if ! cp -a "$source" /Applications/ 2>/dev/null; then
    target="${HOME}/Applications/Transkun.app"
    mkdir -p "${HOME}/Applications"
    rm -rf "$target"
    cp -a "$source" "${HOME}/Applications/" || { detach "$mount"; die "复制 .app 失败"; }
    warn "没有写 /Applications 的权限，装到了 ${target}"
  fi
  detach "$mount"
  xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
  INSTALL_PATH="$target"
}

step "安装"
case "$MODE" in
  deb | rpm) install_deb_or_rpm "$PACKAGE_PATH" ;;
  appimage) install_appimage "$PACKAGE_PATH" ;;
  dmg) install_macos_dmg "$PACKAGE_PATH" ;;
  *) die "内部错误：未知安装方式 ${MODE}" ;;
esac

# ------------------------------------------------------------------ 收尾

step "完成"
if [ "$OS" = macos ]; then
  say "打开应用：open -a \"${INSTALL_PATH}\""
  say "（也可以双击「应用程序」里的 Transkun）"
else
  say "启动应用：transkun-app"
fi
say "局域网服务：transkun-app --web --lan（会在终端打印带令牌的链接）"

if [ "$OS" = linux ] && command -v nvidia-smi >/dev/null 2>&1; then
  if [ "$MODE" = appimage ]; then
    warn "检测到 NVIDIA 显卡，但 AppImage 不支持原地换 CUDA 版 PyTorch；需要 GPU 请改用 deb/rpm。"
  elif [ -n "$CUDA" ] && [ "$CUDA" != cpu ]; then
    step "换装 ${CUDA} 版 PyTorch"
    # 先把运行时按当前版本展开：全新安装时 venv 还不存在，脚本会说找不到环境；
    # 升级时旧 venv 还在，装进去的 CUDA 版会被下次启动的重新解压覆盖。
    if [ "$DRY_RUN" = 1 ]; then
      printf '   %s\n' "transkun --help >/dev/null   # 展开运行时"
    else
      transkun --help >/dev/null 2>&1 || true
    fi
    # 这个脚本只改你自己的 ~/.local/share/transkun，不要 sudo
    # （sudo 会把 HOME 换成 /root，反而找不到运行时）。
    if [ "$DRY_RUN" = 1 ] || [ -x /usr/share/transkun/install_cuda.sh ]; then
      run env "TORCH_CUDA=${CUDA}" \
        /usr/share/transkun/install_cuda.sh
    else
      warn "找不到 install_cuda.sh；手动运行："
      warn "  TORCH_CUDA=${CUDA} /usr/share/transkun/install_cuda.sh"
    fi
  else
    warn "检测到 NVIDIA 显卡；包内默认是 CPU 版 PyTorch。要 GPU 加速（不需要 sudo）："
    warn "  TORCH_CUDA=cu124 /usr/share/transkun/install_cuda.sh"
  fi
fi

if [ "$DRY_RUN" = 1 ]; then
  say "（--dry-run：以上命令都没有真正执行）"
fi
