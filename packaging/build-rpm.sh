#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.2.0}"
rm -rf "$ROOT/.rpmbuild"
mkdir -p "$ROOT/dist"
rm -f "$ROOT/dist"/transkun-*.rpm
find "$ROOT/transkun" "$ROOT/transkun_app" "$ROOT/packaging" \
  -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$ROOT/.rpmbuild"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "$ROOT/.rpmbuild/SOURCES/transkun"
cp -a "$ROOT/transkun" "$ROOT/transkun_app" "$ROOT/packaging" \
  "$ROOT/setup.py" "$ROOT/README.md" "$ROOT/LICENSE" \
  "$ROOT/.rpmbuild/SOURCES/transkun/"
printf '%s\n' "$VERSION" > "$ROOT/.rpmbuild/SOURCES/transkun/VERSION"
TCLTK_DIR="${TCLTK_DIR:-}"
if [ -n "$TCLTK_DIR" ] && [ -d "$TCLTK_DIR/lib" ]; then
  cp -a "$TCLTK_DIR" "$ROOT/.rpmbuild/SOURCES/transkun/tcltk"
else
  echo "warning: TCLTK_DIR 未设置；包内将不含 Xft Tcl/Tk（0.2.0 的 Tk 界面中文可能空白）。" >&2
fi
runtime_src="${RUNTIME_TARBALL:-}"
if [ -z "$runtime_src" ]; then
  runtime_src="$(ls "$ROOT"/dist/transkun-runtime*.tar.zst 2>/dev/null | head -1 || true)"
fi
if [ -n "$runtime_src" ] && [ -f "$runtime_src" ]; then
  cp "$runtime_src" "$ROOT/.rpmbuild/SOURCES/transkun/runtime.tar.zst"
  echo "runtime: $(du -h "$runtime_src" | cut -f1)  源: $runtime_src"
else
  echo "warning: 未找到 runtime.tar.zst；包内不含离线运行时。" >&2
fi
find "$ROOT/.rpmbuild/SOURCES/transkun" -type d -exec chmod 755 {} +
tar -C "$ROOT/.rpmbuild/SOURCES" --use-compress-program='gzip -1' \
  -cf "$ROOT/.rpmbuild/SOURCES/transkun.tar.gz" transkun
rm -rf "$ROOT/.rpmbuild/SOURCES/transkun"
cp "$ROOT/packaging/transkun.spec" "$ROOT/.rpmbuild/SPECS/"
rpmbuild --define "_topdir $ROOT/.rpmbuild" --define "version $VERSION" \
  -ba "$ROOT/.rpmbuild/SPECS/transkun.spec"
mkdir -p "$ROOT/dist"
find "$ROOT/.rpmbuild/RPMS" -name '*.rpm' -exec cp {} "$ROOT/dist/" \;
echo "Built RPMs in $ROOT/dist"
rm -rf "$ROOT/.rpmbuild/BUILD" "$ROOT/.rpmbuild/SOURCES/transkun.tar.gz"
