"""输入解析、调性/速度估计与时间量化。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np


@dataclass
class NoteEvent:
    """演奏事件（秒）。"""

    pitch: int
    start: float
    end: float
    velocity: int = 80


@dataclass
class QuantizedNote:
    pitch: int
    start: float          # 原始秒
    end: float
    velocity: int
    qstart: float         # 量化后的四分音符位置
    qend: float
    hand: str = ''


def notes_from_events(events: Iterable) -> list[NoteEvent]:
    """把 Transkun 的 note 事件转成 NoteEvent（忽略踏板等负 pitch）。"""
    notes = []
    for event in events:
        pitch = int(getattr(event, 'pitch'))
        if pitch <= 0:
            continue
        start = float(getattr(event, 'start'))
        end = float(getattr(event, 'end'))
        if end <= start:
            continue
        notes.append(NoteEvent(pitch, start, end,
                               int(getattr(event, 'velocity', 80))))
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


def load_midi_notes(path) -> list[NoteEvent]:
    """读 MIDI；时间一律用秒，不依赖文件里的 120 BPM 假 tick。"""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(str(path))
    notes = []
    for instrument in midi.instruments:
        for note in instrument.notes:
            if 0 < note.pitch <= 127 and note.end > note.start:
                notes.append(NoteEvent(
                    int(note.pitch), float(note.start), float(note.end),
                    int(note.velocity)))
    notes.sort(key=lambda n: (n.start, n.pitch))
    if not notes:
        raise ValueError(f'MIDI 里没有音符: {path}')
    return notes


# --------------------------------------------------------------------- 调性


def _key_name(key_obj) -> str:
    tonic = key_obj.tonic.name.replace('-', 'b')
    return tonic + ('m' if key_obj.mode == 'minor' else '')


def parse_key(name: str):
    """把 ``D`` / ``Bm`` / ``Bb`` 变成 music21 Key。"""
    from music21 import key

    if not name or name.lower() == 'auto':
        return None
    name = name.strip()
    minor = len(name) > 1 and name.endswith('m')
    tonic = name[:-1] if minor else name
    tonic = tonic.replace('b', '-')
    return key.Key(tonic, 'minor' if minor else 'major')


def estimate_key(notes: list[NoteEvent]):
    """Krumhansl-Schmuckler 调性估计（music21）。"""
    from music21 import note as m21note
    from music21 import stream

    if not notes:
        return None
    s = stream.Stream()
    for n in notes:
        s.insert(n.start, m21note.Note(
            n.pitch, quarterLength=max(0.05, n.end - n.start)))
    try:
        key_obj = s.analyze('key')
    except Exception:
        return None
    # C# major 有 7 个升号，实际乐谱几乎都写成 Db（5 个降号）。
    name = key_obj.tonic.name.replace('-', 'b')
    if key_obj.mode == 'major' and name == 'C#':
        from music21 import key as m21key
        return m21key.Key('D-', 'major')
    if key_obj.mode == 'minor' and name == 'A#':
        from music21 import key as m21key
        return m21key.Key('B-', 'minor')
    return key_obj


def key_to_dict(key_obj) -> Optional[dict]:
    if key_obj is None:
        return None
    return {
        'name': _key_name(key_obj),
        'tonic': key_obj.tonic.name.replace('-', 'b'),
        'mode': key_obj.mode,
        'sharps': int(key_obj.sharps),
    }


# --------------------------------------------------------------------- 速度


def estimate_bpm(notes: list[NoteEvent], low=50.0, high=220.0,
                 step=0.5) -> float:
    """在全局 BPM 上搜索，使起音到 16 分网格的平均误差最小。"""
    if not notes:
        return 120.0
    starts = np.array([n.start for n in notes], dtype=np.float64)
    best_bpm, best_error = 120.0, float('inf')
    for bpm in np.arange(low, high + 1e-9, step):
        beat = 60.0 / bpm
        pos = starts / beat
        error = np.mean(np.abs(pos - np.round(pos * 4.0) / 4.0))
        if error < best_error - 1e-12:
            best_bpm, best_error = float(bpm), float(error)
    return float(round(best_bpm))


# --------------------------------------------------------------------- 量化


def _snap(value: float, divisor: int) -> float:
    return round(value * divisor) / divisor


def quantize_notes(notes: list[NoteEvent], bpm: float,
                   min_units: float = 1 / 16,
                   tuplet_mode: str = 'auto') -> list[QuantizedNote]:
    """起音/音尾吸附到网格。

    关键：**同一个音符只选一种网格**（二进制 16 分或三连音 1/6），
    不能起点用 1/4、终点用 1/3，否则会得到 1/12、5/12 这类非标准时值，
    music21 只能写出 12:11、6:5 之类的非法 tuplet。
    """
    beat = 60.0 / bpm
    out: list[QuantizedNote] = []
    for n in notes:
        start_q = n.start / beat
        end_q = n.end / beat
        if tuplet_mode == 'none':
            grid = 4
        else:
            binary_start, binary_end = _snap(start_q, 4), _snap(end_q, 4)
            ternary_start, ternary_end = _snap(start_q, 6), _snap(end_q, 6)
            binary_error = abs(start_q - binary_start) + abs(end_q - binary_end)
            ternary_error = abs(start_q - ternary_start) + abs(end_q - ternary_end)
            grid = 6 if ternary_error + 1e-9 < binary_error else 4
        qstart = max(0.0, _snap(start_q, grid))
        qend = _snap(end_q, grid)
        minimum = 1 / 6 if grid == 6 else min_units
        qend = max(qstart + minimum, qend)
        out.append(QuantizedNote(
            pitch=n.pitch, start=n.start, end=n.end,
            velocity=n.velocity, qstart=qstart, qend=qend))

    # 同音高、同起点：保留力度大的
    dedup: dict[tuple[int, float], QuantizedNote] = {}
    for n in out:
        key = (n.pitch, round(n.qstart, 6))
        existing = dedup.get(key)
        if existing is None or n.velocity > existing.velocity:
            if existing is not None:
                n.qend = max(n.qend, existing.qend)
            dedup[key] = n
    out = sorted(dedup.values(), key=lambda n: (n.qstart, n.pitch))

    # 同音高前后重叠：前一个截到后一个起点
    last_by_pitch: dict[int, QuantizedNote] = {}
    for n in out:
        previous = last_by_pitch.get(n.pitch)
        if previous is not None and n.qstart < previous.qend:
            previous.qend = max(previous.qstart + min_units, n.qstart)
        last_by_pitch[n.pitch] = n

    for n in out:
        if n.qend <= n.qstart:
            n.qend = n.qstart + min_units
    return out
