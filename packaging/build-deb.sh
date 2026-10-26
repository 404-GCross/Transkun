#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-amd64}"
OUT="$ROOT/dist"
PKG="$ROOT/.package/transkun"
rm -rf "$PKG" "$OUT"
find "$ROOT/transkun" "$ROOT/transkun_app" "$ROOT/packaging" \
  -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$PKG/DEBIAN" "$PKG/usr/share/transkun" \
  "$PKG/usr/share/applications" "$PKG/usr/bin" \
  "$PKG/usr/share/icons/hicolor/256x256/apps"
cp -a "$ROOT/transkun" "$ROOT/transkun_app" "$ROOT/packaging" \
  "$PKG/usr/share/transkun/"
# 启动器固定在包根目录（run.sh 用它判断是否已安装）。
cp "$ROOT/packaging/run.sh" "$ROOT/packaging/install_cuda.sh" \
  "$PKG/usr/share/transkun/"
# setup.py 必须在，源码/兜底安装才能 pip install 本包。
cp -a "$ROOT/setup.py" "$ROOT/README.md" "$ROOT/LICENSE" \
  "$PKG/usr/share/transkun/"
printf '%s\n' "$VERSION" > "$PKG/usr/share/transkun/VERSION"
# 随包分发带 Xft 的 Tcl/Tk（0.2.0 的 Tk 界面用；CLI/WebUI 不需要）。
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -n "$TCLTK_DIR" ] && [ -d "$TCLTK_DIR/lib" ]; then
  cp -a "$TCLTK_DIR" "$PKG/usr/share/transkun/tcltk"
else
  echo "warning: TCLTK_DIR 未设置；包内将不含 Xft Tcl/Tk（0.2.0 的 Tk 界面中文可能空白）。" >&2
fi
# 内置「离线运行时」（Python + 依赖，见 packaging/build-runtime.sh）。
runtime_src="${RUNTIME_TARBALL:-}"
if [ -z "$runtime_src" ]; then
  runtime_src="$(ls "$ROOT"/dist/transkun-runtime*.tar.zst 2>/dev/null | head -1 || true)"
fi
if [ -n "$runtime_src" ] && [ -f "$runtime_src" ]; then
  cp "$runtime_src" "$PKG/usr/share/transkun/runtime.tar.zst"
  echo "runtime: $(du -h "$runtime_src" | cut -f1)  源: $runtime_src"
else
  echo "warning: 未找到 runtime.tar.zst；包内不含离线运行时，首次启动需联网安装。" >&2
fi
find "$PKG" -type d -exec chmod 755 {} +
cat > "$PKG/usr/bin/transkun-app" <<'EOF'
#!/usr/bin/env bash
exec /usr/share/transkun/run.sh "$@"
EOF
cat > "$PKG/usr/bin/transkun" <<'EOF'
#!/usr/bin/env bash
exec /usr/share/transkun/run.sh --upstream-cli "$@"
EOF
chmod 755 "$PKG/usr/bin/transkun-app" "$PKG/usr/bin/transkun" \
  "$PKG/usr/share/transkun/run.sh"
cp "$ROOT/packaging/transkun.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/transkun-web.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/transkun.png" \
  "$PKG/usr/share/icons/hicolor/256x256/apps/"
cat > "$PKG/DEBIAN/control" <<EOF
Package: transkun
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Maintainer: 404-GCross
Description: Transkun piano transcription (CLI, WebUI, desktop app)
 Piano audio to MIDI with the Transkun 2.0 model, a browser WebUI for LAN use,
 and optional rule-based MIDI cleanup.
Depends: zstd, ca-certificates, libsndfile1, ffmpeg, libx11-6, libxext6, libxft2, libfontconfig1, libxrender1, libxss1
Recommends: git, curl
EOF
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
echo "Transkun 已安装（含离线运行时与模型权重）。"
echo "  transkun-app song.mp3 -o song.mid    # 命令行"
echo "  transkun-app --web --lan             # 浏览器 / 手机"
echo "首次启动会在 ~/.local/share/transkun 展开运行时，不需要联网。"
EOF
chmod 755 "$PKG/DEBIAN/postinst"
mkdir -p "$OUT"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/transkun_${VERSION}_${ARCH}.deb"
echo "Built $OUT/transkun_${VERSION}_${ARCH}.deb"
rm -rf "$ROOT/.package"
