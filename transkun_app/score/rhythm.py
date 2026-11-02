"""节奏整理：把量化后的和弦归一化，并把连续三连音显式聚成 3:2 组。

music21 的 ``makeNotation()`` 会为每个带 ``time-modification`` 的音写自己的
``<tuplet>`` 标记，不会自动把连续三个三连音合成一组。因此这里先做两件事：

1. 只允许标准二进制时值，或完整的三连音组（3 个 1/6、1/3、2/3）；
2. 给完整三连音组的每个和弦挂上同一个 ``Tuplet(3, 2)``，让 music21 输出
   start/continue/stop，而不是每个音一个单音符 tuplet。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from transkun_app.score.layout import ChordEvent

TOL = 1e-6

# 允许作为单音时值的二进制时值（四分音符长度）。
BINARY_DURATIONS = (
    1 / 16, 1 / 8, 3 / 16, 1 / 4, 3 / 8, 1 / 2, 3 / 4,
    1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0,
)

# 允许聚组的三连音单音时值。
TERNARY_DURATIONS = (1 / 6, 1 / 3, 2 / 3)


@dataclass
class RhythmResult:
    chords: list
    stats: dict = field(default_factory=dict)


def measure_length(time_signature: str) -> float:
    try:
        numerator, denominator = time_signature.split('/')
        return int(numerator) * 4.0 / int(denominator)
    except Exception:
        return 4.0


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL


def _ternary_duration(value: float):
    for candidate in TERNARY_DURATIONS:
        if _close(value, candidate):
            return candidate
    return None


def _snap_binary(value: float) -> float:
    return min(BINARY_DURATIONS, key=lambda item: abs(item - value))


def _snap_onset(value: float) -> float:
    return round(value * 16.0) / 16.0


def _ceil_onset(value: float) -> float:
    return math.ceil(value * 16.0 - 1e-9) / 16.0


def _largest_binary_fit(max_duration: float) -> float:
    """不超过可用空间的、最大的标准二进制时值。"""
    if max_duration >= BINARY_DURATIONS[-1] - TOL:
        return BINARY_DURATIONS[-1]
    for candidate in reversed(BINARY_DURATIONS):
        if candidate <= max_duration + TOL:
            return candidate
    # 空间不足一个 16 分：仍写 16 分，允许极小的声部重叠，避免非法时值。
    return BINARY_DURATIONS[0]


def _valid_group(group: list, duration: float, mlen: float) -> bool:
    if len(group) != 3:
        return False
    start = group[0].qstart
    end = start + 3 * duration
    # 组起点必须在 16 分网格上；组总时值必须是二进制时值；组不能跨小节。
    if abs(start * 4.0 - round(start * 4.0)) > TOL:
        return False
    if abs((3 * duration) * 4.0 - round((3 * duration) * 4.0)) > TOL:
        return False
    if start < -TOL:
        return False
    measure = int(start / mlen + 1e-9)
    if end > (measure + 1) * mlen + TOL:
        return False
    for expected, chord in zip(
            (start, start + duration, start + 2 * duration), group):
        if not _close(chord.qstart, expected):
            return False
    return True


def normalize_rhythm(chords: list, time_signature: str = '4/4',
                     tuplet_mode: str = 'auto') -> RhythmResult:
    """归一化时值并聚组三连音。"""
    mlen = measure_length(time_signature)
    by_hand: dict[str, list[ChordEvent]] = {}
    for chord in chords:
        chord.tuplet = None
        by_hand.setdefault(chord.hand, []).append(chord)

    tuplet_groups = 0
    for hand, items in by_hand.items():
        items.sort(key=lambda item: item.qstart)
        index = 0
        while index < len(items):
            duration = _ternary_duration(items[index].qdur)
            if duration is None or tuplet_mode == 'none':
                index += 1
                continue

            # 连续、等时值、起音间隔等于时值的三连音 run
            end = index + 1
            while end < len(items):
                candidate = items[end]
                previous = items[end - 1]
                if (_ternary_duration(candidate.qdur) != duration
                        or not _close(candidate.qstart - previous.qstart,
                                      duration)):
                    break
                end += 1

            run = items[index:end]
            cursor = 0
            while cursor + 3 <= len(run):
                group = run[cursor:cursor + 3]
                if _valid_group(group, duration, mlen):
                    for chord in group:
                        chord.tuplet = (3, 2)
                    tuplet_groups += 1
                    cursor += 3
                else:
                    break
            index = end

    # 未进入合法三连音组的和弦：回退到二进制 16 分网格。
    # 关键：截断时只能选 BINARY_DURATIONS 里的标准时值，绝不能直接用
    # ``next_start - qstart``；否则会重新产生 17/48、5/16 这类无法记谱的时值。
    snapped = 0
    for hand, items in by_hand.items():
        items.sort(key=lambda item: item.qstart)
        for chord in items:
            if chord.tuplet is None:
                chord.qstart = max(0.0, _snap_onset(chord.qstart))
                chord.qdur = _snap_binary(chord.qdur)

        cursor = 0.0
        for position, chord in enumerate(items):
            if chord.tuplet is not None:
                # 三连音组的位置和时值已经校验过，保持不动。
                cursor = max(cursor, chord.qstart + chord.qdur)
                continue
            start = chord.qstart
            if start < cursor - TOL:
                start = _ceil_onset(cursor)
            if position + 1 < len(items):
                max_duration = items[position + 1].qstart - start
            else:
                max_duration = chord.qdur
            duration = _largest_binary_fit(max_duration)
            if (not _close(start, chord.qstart)
                    or not _close(duration, chord.qdur)):
                snapped += 1
            chord.qstart = start
            chord.qdur = duration
            cursor = start + duration

    all_chords = [chord for items in by_hand.values() for chord in items]
    all_chords.sort(key=lambda item: (item.qstart, item.hand))
    return RhythmResult(
        chords=all_chords,
        stats={'tuplet_groups': tuplet_groups, 'snapped': snapped},
    )
