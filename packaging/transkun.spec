Name: transkun
Version: %{?version}%{!?version:0.1.0}
Release: 1%{?dist}
Summary: Transkun piano transcription (CLI, WebUI, desktop app)
License: MIT
BuildArch: x86_64
Requires: zstd, libsndfile, libX11, libXext, libXft, fontconfig, libXrender, libXScrnSaver
Recommends: ffmpeg, git, curl
Source0: transkun.tar.gz

%description
Piano audio to MIDI with the Transkun 2.0 model, a browser WebUI for LAN use,
and optional rule-based MIDI cleanup.

%prep
%setup -q -n transkun

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}%{_prefix}/share/transkun
mkdir -p %{buildroot}%{_bindir} %{buildroot}%{_datadir}/applications
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
cp -a transkun transkun_app packaging setup.py README.md LICENSE VERSION \
  %{buildroot}%{_prefix}/share/transkun/
cp packaging/run.sh packaging/install_cuda.sh packaging/fix_tk_system.py \
  %{buildroot}%{_prefix}/share/transkun/
# 离线运行时（build-rpm.sh 从 RUNTIME_TARBALL 拷进来）；权重在 transkun/pretrained。
if [ -f runtime.tar.zst ]; then
  cp runtime.tar.zst %{buildroot}%{_prefix}/share/transkun/
fi
if [ -d tcltk ]; then
  cp -a tcltk %{buildroot}%{_prefix}/share/transkun/
fi
cat > %{buildroot}%{_bindir}/transkun-app <<'EOF'
#!/usr/bin/env bash
exec /usr/share/transkun/run.sh "$@"
EOF
cat > %{buildroot}%{_bindir}/transkun <<'EOF'
#!/usr/bin/env bash
exec /usr/share/transkun/run.sh --upstream-cli "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/transkun-app %{buildroot}%{_bindir}/transkun \
  %{buildroot}%{_prefix}/share/transkun/run.sh
cp %{buildroot}%{_prefix}/share/transkun/packaging/transkun.desktop \
  %{buildroot}%{_datadir}/applications/
cp %{buildroot}%{_prefix}/share/transkun/packaging/transkun-web.desktop \
  %{buildroot}%{_datadir}/applications/
cp %{buildroot}%{_prefix}/share/transkun/packaging/transkun.png \
  %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/

%files
%{_bindir}/transkun
%{_bindir}/transkun-app
%{_prefix}/share/transkun
%{_datadir}/applications/transkun.desktop
%{_datadir}/applications/transkun-web.desktop
%{_datadir}/icons/hicolor/256x256/apps/transkun.png

%post
echo "Transkun 已安装（含离线运行时与模型权重）。"
echo "  transkun-app song.mp3 -o song.mid"
echo "  transkun-app --web --lan"
echo "首次启动会在 ~/.local/share/transkun 展开运行时，不需要联网。"
