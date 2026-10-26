"""Transkun 推理封装：加载权重、解码音频、转谱、写 MIDI。

只依赖上游 ``transkun`` 包（模型定义 + 自带权重），不复制模型代码。

与上游 CLI 的差别：

- 音频解码走 soundfile，失败再用 ffmpeg 解码成 44.1 kHz 单声道 float32。
  上游的 ``readAudio`` 强制按 mp3 解复用，m4a/opus/aac 会失败；这里全部支持。
- 单声道下混。上游把立体声当成两个“通道”塞进 batch 维，结果依赖实现细节。
- 进度回调（``ModelTransformer.transcribe`` 新增的可选参数），同时用于取消。
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from transkun_app import UPSTREAM_VERSION

MODEL_NAME = "Transkun 2.0"
MODEL_ID = "2.0"

# (done, total)；total 为 None 表示总片段数未知。
ProgressCallback = Callable[[int, Optional[int]], None]
CancelCallback = Callable[[], None]


def _resource_path(name: str) -> Path:
    """上游包内权重/配置的路径（源码树和 wheel 都适用）。"""
    try:
        from importlib.resources import files
        return Path(str(files("transkun").joinpath("pretrained", name)))
    except Exception:  # pragma: no cover - 老 Python/setuptools 兜底
        import pkg_resources
        return Path(pkg_resources.resource_filename(
            "transkun", "pretrained/" + name))


DEFAULT_WEIGHT = _resource_path("2.0.pt")
DEFAULT_CONF = _resource_path("2.0.conf")


# --------------------------------------------------------------------- 设备


def resolve_device(device: str = "auto") -> str:
    """把 auto 解析成 cuda/cpu；显式请求 cuda 但不可用时给出明确错误。"""
    if device not in ("auto", "cuda", "cpu"):
        raise ValueError(f"未知设备: {device}")
    import torch

    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "请求了 CUDA，但 PyTorch 没有检测到可用的 NVIDIA GPU。\n"
            "请安装带 CUDA 的 PyTorch（见 packaging/install_cuda.sh），"
            "或改用 --device cpu / auto。")
    return device


def device_summary(device: Optional[str] = None) -> str:
    """人类可读的推理设备描述。"""
    try:
        import torch
        if device is None or device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return f"CUDA · {name}"
        threads = torch.get_num_threads()
        return f"CPU · {threads} 线程"
    except Exception as exc:  # pragma: no cover - 只用于展示
        return f"未知设备（{exc}）"


# --------------------------------------------------------------------- 音频


class AudioDecodeError(RuntimeError):
    pass


def _decode_with_soundfile(path: Path):
    import soundfile as sf

    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if data.size == 0:
        raise AudioDecodeError(f"音频为空: {path}")
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return mono, int(sample_rate)


def _decode_with_ffmpeg(path: Path, sample_rate: int) -> np.ndarray:
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "1", "-ar", str(sample_rate), "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)
    except FileNotFoundError as exc:
        raise AudioDecodeError(
            f"无法解码 {path.name}：soundfile 不支持该格式，而且系统里没有 ffmpeg。\n"
            "Debian/Ubuntu: sudo apt install ffmpeg\n"
            "Fedora: sudo dnf install ffmpeg") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AudioDecodeError(
            f"无法解码 {path.name}：ffmpeg 退出码 {proc.returncode}"
            + (f"\n{detail[-1]}" if detail else ""))
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size == 0:
        raise AudioDecodeError(f"ffmpeg 没有解出音频: {path}")
    return audio


def load_audio(path, sample_rate: int = 44100) -> np.ndarray:
    """读成单声道 float32，采样率 ``sample_rate``。

    先用 soundfile（wav/flac/ogg，新版 libsndfile 也能读 mp3），失败再用
    ffmpeg（mp3/m4a/aac/opus 等）。两条路都不通时抛 :class:`AudioDecodeError`。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"音频文件不存在: {path}")

    try:
        audio, native_rate = _decode_with_soundfile(path)
    except AudioDecodeError:
        raise
    except Exception:
        # soundfile 不认识这个容器（m4a/aac/opus…），交给 ffmpeg。
        return _decode_with_ffmpeg(path, sample_rate)

    if native_rate != sample_rate:
        import soxr
        audio = soxr.resample(audio, native_rate, sample_rate, quality="HQ")
    return np.ascontiguousarray(audio, dtype=np.float32)


def probe_duration_minutes(path) -> Optional[float]:
    """只读文件头拿时长（分钟）；失败返回 None，交给 worker 报错。"""
    path = Path(path)
    try:
        import soundfile as sf
        info = sf.info(str(path))
        if info.samplerate:
            return info.frames / float(info.samplerate) / 60.0
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        text = proc.stdout.decode("utf-8", "replace").strip()
        if text:
            return float(text) / 60.0
    except Exception:
        pass
    return None


# --------------------------------------------------------------------- 转谱


class Transcriber:
    """加载一次模型并复用；线程安全（WebUI 只有一个 worker，但 CLI 也会用）。"""

    def __init__(self, weight=None, conf=None, device: str = "auto",
                 threads: Optional[int] = None):
        self.weight_path = Path(weight) if weight else DEFAULT_WEIGHT
        self.conf_path = Path(conf) if conf else DEFAULT_CONF
        self.device = resolve_device(device)
        self.threads = threads
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def sample_rate(self) -> int:
        if self._model is not None:
            return int(self._model.fs)
        return 44100

    def load(self):
        """加载权重（首次较慢，约 1~5 秒）。"""
        with self._lock:
            if self._model is not None:
                return self._model

            import torch
            import moduleconf

            if not self.weight_path.exists():
                raise FileNotFoundError(
                    f"找不到模型权重: {self.weight_path}\n"
                    "源码安装时权重在 transkun/pretrained/2.0.pt；"
                    "发行包请确认已随包安装。")
            if not self.conf_path.exists():
                raise FileNotFoundError(f"找不到模型配置: {self.conf_path}")

            if self.threads:
                torch.set_num_threads(max(1, int(self.threads)))
            torch.set_grad_enabled(False)

            manager = moduleconf.parseFromFile(str(self.conf_path))
            TransKun = manager["Model"].module.TransKun
            conf = manager["Model"].config

            checkpoint = torch.load(str(self.weight_path),
                                    map_location=self.device)
            model = TransKun(conf=conf).to(self.device)
            if "best_state_dict" in checkpoint:
                state = checkpoint["best_state_dict"]
            else:
                state = checkpoint.get("state_dict")
            if state is None:
                raise RuntimeError(
                    f"权重文件里没有 state_dict/best_state_dict: {self.weight_path}")
            model.load_state_dict(state, strict=False)
            model.eval()
            self._model = model
            return model

    def transcribe_file(self, audio_path, progress_cb: Optional[ProgressCallback] = None,
                        cancel_cb: Optional[CancelCallback] = None):
        """音频 → 事件列表（负 pitch 表示踏板 CC）。"""
        model = self.load()
        audio = load_audio(audio_path, int(model.fs))

        import torch
        # 上游 transcribe 期待 (frames, channels)，内部再 transpose 成 (channels, frames)。
        x = torch.from_numpy(audio[:, None]).to(self.device)

        def on_progress(done, total):
            if progress_cb is not None:
                progress_cb(done, total)
            if cancel_cb is not None:
                cancel_cb()

        return model.transcribe(x, progressCallback=on_progress)


def note_count(events) -> int:
    return sum(1 for event in events if event.pitch > 0)


def pedal_count(events) -> int:
    """踏板事件对数（每个事件写 CC on + CC off）。"""
    return sum(1 for event in events if event.pitch < 0)


def save_midi(events, path) -> str:
    """用上游 ``writeMidi`` 写标准 MIDI（含 CC64/CC67 踏板）。"""
    from transkun.Data import writeMidi

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi = writeMidi(events)
    midi.write(str(path))
    return str(path)


def model_info() -> dict:
    size = None
    try:
        size = DEFAULT_WEIGHT.stat().st_size
    except OSError:
        pass
    return {
        "id": MODEL_ID,
        "name": MODEL_NAME,
        "upstream": UPSTREAM_VERSION,
        "weight": str(DEFAULT_WEIGHT),
        "weight_bytes": size,
        "available": DEFAULT_WEIGHT.exists() and DEFAULT_CONF.exists(),
        "sample_rate": 44100,
    }


# --------------------------------------------------------------------- 单例

_TRANSCRIBERS = {}
_TRANSCRIBERS_LOCK = threading.Lock()


def get_transcriber(weight=None, conf=None, device: str = "auto",
                    threads: Optional[int] = None) -> Transcriber:
    """按（权重, 配置, 设备）缓存 Transcriber。"""
    key = (str(Path(weight) if weight else DEFAULT_WEIGHT),
           str(Path(conf) if conf else DEFAULT_CONF),
           device, threads)
    with _TRANSCRIBERS_LOCK:
        transcriber = _TRANSCRIBERS.get(key)
        if transcriber is None:
            transcriber = Transcriber(weight=weight, conf=conf,
                                      device=device, threads=threads)
            _TRANSCRIBERS[key] = transcriber
        return transcriber
