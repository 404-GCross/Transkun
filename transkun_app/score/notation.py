"""music21 记谱：和弦块 → MusicXML / score MIDI。"""

from __future__ import annotations

from pathlib import Path

from transkun_app.score.layout import ChordEvent


def build_score(chords: list[ChordEvent], *, title: str = '',
                composer: str = '', key_obj=None, time_signature: str = '4/4',
                bpm: float = 120.0):
    from music21 import chord as m21chord
    from music21 import clef, expressions, key, layout, metadata, meter
    from music21 import stream

    score = stream.Score()
    score.metadata = metadata.Metadata()
    score.metadata.title = title or 'Transkun Score'
    if composer:
        score.metadata.composer = composer

    rh = stream.PartStaff()
    lh = stream.PartStaff()
    rh.partName = 'Piano'
    lh.partName = 'Piano'

    for part, part_clef in ((rh, clef.TrebleClef()), (lh, clef.BassClef())):
        part.append(part_clef)
        part.append(meter.TimeSignature(time_signature))
        if key_obj is not None:
            part.append(key.KeySignature(key_obj.sharps))

    # 用文本而不是 MetronomeMark 的音符字形：cairosvg 不认 SMuFL 文本字体。
    tempo_text = expressions.TextExpression(f'M.M. {int(round(bpm))}')
    tempo_text.placement = 'above'
    rh.insert(0, tempo_text)

    for item in chords:
        part = rh if item.hand == 'R' else lh
        ch = m21chord.Chord(item.pitches, quarterLength=item.qdur)
        ch.velocity = max(1, min(127, item.velocity))
        if item.arpeggio:
            ch.expressions.append(expressions.ArpeggioMark('normal'))
        part.insert(item.qstart, ch)

    group = layout.StaffGroup([rh, lh], symbol='brace', name='Piano')
    score.insert(0, group)
    score.insert(0, rh)
    score.insert(0, lh)

    score = score.makeNotation()
    try:
        score.makeTies(inPlace=True)
    except Exception:
        pass
    return score


def count_measures(score) -> int:
    try:
        measures = list(score.recurse().getElementsByClass('Measure'))
        return len(measures)
    except Exception:
        return 0


def write_musicxml(score, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    score.write('musicxml', fp=str(path))
    return path


def write_score_midi(score, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    score.write('midi', fp=str(path))
    return path
