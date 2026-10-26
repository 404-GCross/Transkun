#!/usr/bin/env bash
# 把已安装的 Transkun 运行时换成 CUDA 版 PyTorch（只改用户目录，不需要 sudo）。
#
# 用法:
#   install_cuda.sh                 # 默认 cu124
#   TORCH_CUDA=cu126 install_cuda.sh
#   TORCH_CUDA=cu128 install_cuda.sh
#
# 装完用 `transkun-app --check` 或界面里的“设备”确认；推理时加 --device cuda。

set -euo pipefail

TRANSKUN_HOME="${TRANSKUN_HOME:-$HOME/.local/share/transkun}"
VENV_DIR="${TRANSKUN_VENV_DIR:-$TRANSKUN_HOME/venv}"
UV_BIN="${UV_BIN:-$TRANSKUN_HOME/uv/uv}"
TORCH_CUDA="${TORCH_CUDA:-cu124}"
PIP_INDEX="${TRANSKUN_PIP_INDEX:-}"

case "$TORCH_CUDA" in
  cu124 | cu126 | cu128) ;;
  *)
    echo "TORCH_CUDA 只能是 cu124 / cu126 / cu128（当前 ${TORCH_CUDA}）" >&2
    exit 1
    ;;
esac

[ -x "$VENV_DIR/bin/python" ] || {
  echo "找不到运行时: $VENV_DIR" >&2
  echo "先运行一次 transkun-app --version 展开运行时。" >&2
  exit 1
}
[ -x "$UV_BIN" ] || {
  echo "找不到 uv: $UV_BIN" >&2
  exit 1
}

echo "把 PyTorch 换成 ${TORCH_CUDA}（下载约 2~3GB）…"
args=(pip install --python "$VENV_DIR/bin/python" --force-reinstall
      torch torchaudio
      --index-url "https://download.pytorch.org/whl/${TORCH_CUDA}")
if [ -n "$PIP_INDEX" ]; then
  args+=(--index-strategy unsafe-best-match --extra-index-url "$PIP_INDEX")
fi
"$UV_BIN" "${args[@]}"

echo
echo "验证："
"$VENV_DIR/bin/python" - <<'PY'
import torch
print('torch', torch.__version__)
print('cuda available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('gpu:', torch.cuda.get_device_name(0))
PY
echo
echo "之后用: transkun-app --device cuda song.mp3 -o song.mid"
