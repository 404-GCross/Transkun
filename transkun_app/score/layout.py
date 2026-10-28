"""左右手分配、和弦化与可演奏性清理。"""

from __future__ import annotations

from dataclasses import dataclass, field

from transkun_app.score.analysis import QuantizedNote


@dataclass
class ChordEvent:
    hand: str
    qstart: float
    qdur: float
    pitches: list
    velocity: int
    arpeggio: bool = False


@dataclass
class LayoutResult:
    chords: list
    stats: dict = field(default_factory=dict)


def _span(pitches) -> int:
    return max(pitches) - min(pitches) if pitches else 0


def assign_hands(notes: list[QuantizedNote], split: int = 60,
                 max_span: int = 19) -> list[QuantizedNote]:
    """按起音组做“从一个音高缝隙切成左右手”的搜索。

    比固定 C4 分界好：宽和弦会按可演奏的缝隙拆开，跨 C4 的单音也会跟随
    前一只手的位置。
    """
    groups: dict[float, list[QuantizedNote]] = {}
    for n in notes:
        groups.setdefault(round(n.qstart, 6), []).append(n)

    previous = {'L': None, 'R': None}

    for qstart in sorted(groups):
        group = sorted(groups[qstart], key=lambda n: n.pitch)
        n = len(group)
        best = None
        for k in range(n + 1):
            left = group[:k]
            right = group[k:]
            cost = 0.0
            for hand, items in (('L', left), ('R', right)):
                if not items:
                    continue
                pitches = [x.pitch for x in items]
                span = _span(pitches)
                if span > max_span:
                    cost += (span - max_span) * 100.0
                if hand == 'R':
                    cost += sum(max(0, split - p) for p in pitches) * 0.6
                else:
                    cost += sum(max(0, p - split) for p in pitches) * 0.6
                centre = sum(pitches) / len(pitches)
                if previous[hand] is not None:
                    cost += abs(centre - previous[hand]) * 0.1
            if left and right and max(x.pitch for x in left) > min(
                    x.pitch for x in right):
                cost += 100.0
            if best is None or cost < best[0]:
                best = (cost, k)

        k = best[1] if best else n // 2
        for item in group[:k]:
            item.hand = 'L'
        for item in group[k:]:
            item.hand = 'R'
        for hand, items in (('L', group[:k]), ('R', group[k:])):
            if items:
                previous[hand] = sum(x.pitch for x in items) / len(items)
    return notes


def build_chords(notes: list[QuantizedNote], mode: str = 'block',
                 max_span: int = 19, max_notes: int = 5,
                 arpeggio_ms: float = 70.0,
                 max_chord_beats: float = 4.0) -> LayoutResult:
    """按（手, 量化起音）合成和弦，并清理不可演奏的音。"""
    # 先把同一只手里、原始起音相距很近且量化后相邻的滚奏合成一个起音组，
    # 这样跨 16 分边界的 rolled chord 也能记成“和弦 + 琶音记号”。
    per_hand: dict[str, list[QuantizedNote]] = {}
    for n in notes:
        per_hand.setdefault(n.hand, []).append(n)

    raw_groups: list[tuple[str, list[QuantizedNote]]] = []
    for hand, items in per_hand.items():
        items.sort(key=lambda n: (n.qstart, n.pitch))
        current: list[QuantizedNote] = []
        first_start = 0.0
        for item in items:
            if current:
                combined = [x.pitch for x in current] + [item.pitch]
                close = (item.start - first_start) * 1000.0 <= arpeggio_ms
                near = item.qstart - current[0].qstart <= 0.5
                if close and near and _span(combined) <= max_span \
                        and len(set(combined)) <= max_notes:
                    current.append(item)
                    continue
            if current:
                raw_groups.append((hand, current))
            current = [item]
            first_start = item.start
        if current:
            raw_groups.append((hand, current))

    # 同一手、同一量化起音可能被“滚奏阈值”切成几组，合并回一个和弦，
    # 否则后面的组会覆盖前面的组（音符会莫名消失）。
    groups: dict[tuple[str, float], list[QuantizedNote]] = {}
    for hand, group in raw_groups:
        key = (hand, round(group[0].qstart, 6))
        groups.setdefault(key, []).extend(group)

    by_hand: dict[str, list[float]] = {}
    for hand, qstart in groups:
        by_hand.setdefault(hand, []).append(qstart)
    next_start: dict[tuple[str, float], float] = {}
    for hand, starts in by_hand.items():
        starts.sort()
        for index, qstart in enumerate(starts[:-1]):
            next_start[(hand, qstart)] = starts[index + 1]

    chords: list[ChordEvent] = []
    stats = {'notes_in': len(notes), 'notes_out': 0, 'dropped': 0,
             'arpeggios': 0, 'chords': 0}

    for (hand, qstart), items in sorted(groups.items()):
        items = sorted(items, key=lambda n: n.pitch)
        # 同音高去重，保留力度大的
        unique: dict[int, QuantizedNote] = {}
        for item in items:
            existing = unique.get(item.pitch)
            if existing is None or item.velocity > existing.velocity:
                unique[item.pitch] = item
        items = sorted(unique.values(), key=lambda n: n.pitch)
        if not items:
            continue

        spread = max(n.start for n in items) - min(n.start for n in items)
        arpeggio = len(items) > 1 and spread * 1000.0 >= arpeggio_ms

        end = max(n.qend for n in items)
        next_q = next_start.get((hand, qstart))
        duration = max(1 / 16, end - qstart)
        if next_q is not None:
            duration = min(duration, max(1 / 16, next_q - qstart))
        duration = min(duration, max_chord_beats)

        pitches = [n.pitch for n in items]
        original_count = len(pitches)

        if mode == 'block':
            # 超宽：从最低的内声部开始删，保留最低音与最高音
            while len(pitches) > 2 and _span(pitches) > max_span:
                pitches.pop(1)
            # 超多音：保留最低、最高，再保留最高的内声部
            if len(pitches) > max_notes:
                keep = pitches[:1] + pitches[-(max_notes - 1):]
                pitches = sorted(set(keep))

        stats['dropped'] += original_count - len(pitches)
        if not pitches:
            continue
        velocity = max(n.velocity for n in items)
        chords.append(ChordEvent(
            hand=hand, qstart=qstart, qdur=duration,
            pitches=pitches, velocity=velocity, arpeggio=arpeggio))
        stats['chords'] += 1
        stats['notes_out'] += len(pitches)
        if arpeggio:
            stats['arpeggios'] += 1

    stats['dropped'] = max(0, stats['dropped'])
    return LayoutResult(chords=chords, stats=stats)
