"""后端单元测试：不加载模型（除显式的权重缺失用例）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from transkun_app import backend
from transkun_app.selftest import make_arpeggio


def test_model_info_available():
    info = backend.model_info()
    assert info['available'] is True
    assert info['weight_bytes'] and info['weight_bytes'] > 10 * 2 ** 20
    assert info['sample_rate'] == 44100


def test_load_audio_wav(tmp_path: Path):
    path = make_arpeggio(tmp_path / 'a.wav', duration=1.0)
    audio = backend.load_audio(path, 44100)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert abs(len(audio) - 44100) <= 2
    assert np.abs(audio).max() > 0.01


def test_load_audio_resamples(tmp_path: Path):
    path = make_arpeggio(tmp_path / 'a.wav', duration=1.0, sample_rate=22050)
    audio = backend.load_audio(path, 44100)
    assert abs(len(audio) - 44100) <= 16


def test_load_audio_missing():
    with pytest.raises(FileNotFoundError):
        backend.load_audio('/nonexistent/nope.wav')


def test_count_notes_and_pedals():
    events = [
        SimpleNamespace(pitch=60),
        SimpleNamespace(pitch=64),
        SimpleNamespace(pitch=-64),
        SimpleNamespace(pitch=-67),
    ]
    assert backend.note_count(events) == 2
    assert backend.pedal_count(events) == 2


def test_resolve_device_cpu():
    assert backend.resolve_device('cpu') == 'cpu'


def test_resolve_device_rejects_unknown():
    with pytest.raises(ValueError):
        backend.resolve_device('tpu')


def test_missing_weight_is_reported(tmp_path: Path):
    transcriber = backend.Transcriber(
        weight=tmp_path / 'missing.pt', device='cpu')
    with pytest.raises(FileNotFoundError, match='找不到模型权重'):
        transcriber.load()
