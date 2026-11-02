"""music21 记谱：和弦块 → MusicXML / score MIDI。"""

from __future__ import annotations

from pathlib import Path

from transkun_app.score.layout import ChordEvent


def _make_notation(score):
    """``makeNotation`` 时静音 music21 的 beam-pair 警告。

    这些警告来自 ``partial-right`` + ``stop`` 的不完整组合；输出前
    ``_sanitize_beams`` 会把它们清掉，所以警告本身没有可操作信息。
    """
    from music21 import beam

    original_warn = beam.environLocal.warn

    def quiet_warn(msg, header=None):
        if 'messed up beam pair' in str(msg):
            return
        original_warn(msg, header)

    beam.environLocal.warn = quiet_warn
    try:
        return score.makeNotation()
    finally:
        beam.environLocal.warn = original_warn


def _sanitize_beams(score) -> int:
    """清掉 music21 偶尔写出的悬空 beam（例如只有 end 没有 begin）。

    music21 在混合三连音/附点时值上做 beam 时可能产生 ``partial-right`` +
    ``stop`` 这类不完整组合；MusicXML 里会变成只有 ``<beam>end</beam>``
    的非法二级 beam。这里按小节、声部检查 begin/continue/end 是否配对，
    不配对的一律移除。partial（符尾钩）可以独立存在，保留。
    """
    removed = 0
    for part in score.parts:
        for measure in part.getElementsByClass('Measure'):
            events = []
            for obj in measure.recurse().notesAndRests:
                if obj.isRest:
                    continue
                offset = obj.getOffsetInHierarchy(measure)
                staff = getattr(obj, 'staffNumber', 1) or 1
                voice = getattr(obj, 'voice', 1) or 1
                events.append((offset, staff, voice, obj))
            events.sort(key=lambda item: item[0])
            groups: dict[tuple, list] = {}
            for _, staff, voice, obj in events:
                groups.setdefault((staff, voice), []).append(obj)

            for objects in groups.values():
                open_beams: dict[int, list] = {}
                for obj in objects:
                    beams = getattr(obj, 'beams', None)
                    if beams is None:
                        continue
                    for beam in list(beams.beamsList):
                        number = beam.number
                        kind = beam.type
                        if kind == 'start':
                            if number in open_beams:
                                beams.beamsList.remove(beam)
                                removed += 1
                            else:
                                open_beams[number] = [(obj, beam)]
                        elif kind == 'continue':
                            if number not in open_beams:
                                beams.beamsList.remove(beam)
                                removed += 1
                            else:
                                open_beams[number].append((obj, beam))
                        elif kind == 'stop':
                            if number not in open_beams:
                                beams.beamsList.remove(beam)
                                removed += 1
                            else:
                                del open_beams[number]
                        # partial（forward/backward hook）单独存在是合法的
                for items in open_beams.values():
                    for obj, beam in items:
                        try:
                            obj.beams.beamsList.remove(beam)
                            removed += 1
                        except ValueError:
                            pass
    return removed


def build_score(chords: list[ChordEvent], *, title: str = '',
                composer: str = '', key_obj=None, time_signature: str = '4/4',
                bpm: float = 120.0):
    from music21 import chord as m21chord
    from music21 import clef, duration, expressions, key, layout, metadata, meter
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
        if item.tuplet:
            ch.duration.appendTuplet(duration.Tuplet(*item.tuplet))
        if item.arpeggio:
            ch.expressions.append(expressions.ArpeggioMark('normal'))
        part.insert(item.qstart, ch)

    group = layout.StaffGroup([rh, lh], symbol='brace', name='Piano')
    score.insert(0, group)
    score.insert(0, rh)
    score.insert(0, lh)

    score = _make_notation(score)
    try:
        score.makeTies(inPlace=True)
    except Exception:
        pass
    _sanitize_beams(score)
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
