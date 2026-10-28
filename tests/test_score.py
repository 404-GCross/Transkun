"""打谱版测试：不需要模型。"""

from __future__ import annotations

from types import SimpleNamespace

from transkun_app.score.analysis import NoteEvent, quantize_notes
from transkun_app.score.layout import assign_hands, build_chords
from transkun_app.score.model import ScoreOptions


def _events():
    events = []
    for bar in range(3):
        base = bar * 2.0
        for pitch in (62, 66, 69, 74):
            events.append(SimpleNamespace(pitch=pitch, start=base,
                                          end=base + 1.5, velocity=80))
        events.append(SimpleNamespace(pitch=78, start=base + 0.5,
                                      end=base + 1.0, velocity=90))
        events.append(SimpleNamespace(pitch=50, start=base,
                                      end=base + 1.0, velocity=80))
        events.append(SimpleNamespace(pitch=45, start=base + 1.0,
                                      end=base + 2.0, velocity=80))
    return events


def test_quantize_supports_triplets():
    notes = [
        NoteEvent(60, 0.0, 0.25),
        NoteEvent(62, 1 / 6, 0.4),      # 120 BPM 下的三连音位置
        NoteEvent(64, 0.5, 0.75),
    ]
    out = quantize_notes(notes, bpm=120)
    assert abs(out[1].qstart - 1 / 3) < 1e-6


def test_hand_assignment_splits_wide_chord():
    notes = quantize_notes([
        NoteEvent(40, 0.0, 1.0),
        NoteEvent(60, 0.0, 1.0),
        NoteEvent(80, 0.0, 1.0),
    ], bpm=120)
    assign_hands(notes, split=60, max_span=19)
    hands = {n.hand for n in notes}
    assert 'L' in hands and 'R' in hands


def test_arpeggio_mark():
    notes = quantize_notes([
        NoteEvent(60, 0.0, 1.0),
        NoteEvent(64, 0.01, 1.0),
        NoteEvent(67, 0.02, 1.0),
    ], bpm=120)
    assign_hands(notes, split=60, max_span=19)
    result = build_chords(notes, arpeggio_ms=10)
    assert result.chords and result.chords[0].arpeggio is True


def test_generate_score_files(tmp_path):
    from transkun_app.score import generate_score_from_events

    result = generate_score_from_events(
        _events(), tmp_path / 'song.score',
        ScoreOptions(title='测试 · 中文标题', composer='Transkun'))
    assert result.musicxml and result.musicxml.exists()
    assert result.pdf and result.pdf.exists()
    assert result.midi and result.midi.exists()
    assert result.stats['measures'] > 0
    assert result.metadata['key']['name']

    from music21 import converter
    score = converter.parse(str(result.musicxml))
    assert score is not None

    import pikepdf
    with pikepdf.open(str(result.pdf)) as pdf:
        assert len(pdf.pages) >= 1


def test_score_does_not_overwrite_performance_midi(tmp_path):
    import mido
    from transkun_app.score import generate_score

    source = tmp_path / 'song.mid'
    mid = mido.MidiFile(type=0, ticks_per_beat=96)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message('note_on', note=60, velocity=80, time=0))
    track.append(mido.Message('note_off', note=60, velocity=0, time=96))
    mid.save(str(source))
    before = source.read_bytes()

    result = generate_score(source, options=ScoreOptions(pdf=False, svg=False))
    assert source.read_bytes() == before
    assert result.midi and result.midi.name == 'song.score.mid'
    assert result.musicxml and result.musicxml.name == 'song.score.musicxml'
