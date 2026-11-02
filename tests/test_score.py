"""打谱版测试：不需要模型。"""

from __future__ import annotations

from types import SimpleNamespace

from transkun_app.score.analysis import NoteEvent, quantize_notes
from transkun_app.score.layout import assign_hands, build_chords
from transkun_app.score.model import ScoreOptions
from transkun_app.score.validate import (
    tuplet_details, tuplet_report as _tuplet_report, tuplet_span_ok)


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

    from pypdf import PdfReader
    assert len(PdfReader(str(result.pdf)).pages) >= 1


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


def test_tuplets_are_grouped_and_legal(tmp_path):
    from transkun_app.score import generate_score_from_events

    events = []
    for index, pitch in enumerate((60, 64, 67)):
        events.append(SimpleNamespace(
            pitch=pitch, start=index / 6.0,
            end=index / 6.0 + 1 / 6.0, velocity=80))
    for index, pitch in enumerate((72, 71, 69, 67, 65, 64)):
        events.append(SimpleNamespace(
            pitch=pitch, start=0.5 + index / 12.0,
            end=0.5 + index / 12.0 + 1 / 12.0, velocity=80))
    events.append(SimpleNamespace(pitch=60, start=2.0, end=2.0 + 1 / 6.0,
                                  velocity=80))
    events.append(SimpleNamespace(pitch=62, start=3.0, end=3.5,
                                  velocity=80))

    result = generate_score_from_events(
        events, tmp_path / 'tuplets.score',
        ScoreOptions(bpm=120, time_signature='4/4', pdf=False, svg=False,
                     score_midi=False))
    ratios, groups = _tuplet_report(result.musicxml)
    assert ratios == {(3, 2)}
    assert groups, '没有生成任何 tuplet 组'
    assert all(size >= 2 for _, size in groups), groups
    assert all(size == 3 for _, size in groups), groups
    _, details, divisions = tuplet_details(result.musicxml)
    assert all(item['start_measure'] == item['stop_measure']
               for item in details), details
    assert all(tuplet_span_ok(item, divisions) for item in details), details
    assert result.stats['tuplet_groups'] >= 2


def test_tuplet_mode_none_has_no_tuplets(tmp_path):
    from transkun_app.score import generate_score_from_events

    events = [
        SimpleNamespace(pitch=60, start=0.0, end=1 / 6.0, velocity=80),
        SimpleNamespace(pitch=64, start=1 / 6.0, end=2 / 6.0, velocity=80),
        SimpleNamespace(pitch=67, start=2 / 6.0, end=3 / 6.0, velocity=80),
    ]
    result = generate_score_from_events(
        events, tmp_path / 'none.score',
        ScoreOptions(bpm=120, time_signature='4/4', tuplet_mode='none',
                     pdf=False, svg=False, score_midi=False))
    ratios, groups = _tuplet_report(result.musicxml)
    assert ratios == set()
    assert groups == []


def test_random_mixed_grid_stays_legal(tmp_path):
    """混合网格的随机音符不应再产生 17/48、5/16 这类无法记谱的时值。"""
    import random

    from music21 import converter

    from transkun_app.score import generate_score_from_events

    random.seed(4)
    events = []
    now = 0.0
    for _ in range(120):
        now += random.choice([0.08, 0.12, 0.16, 0.25, 0.33, 0.5])
        duration = random.choice([0.08, 0.12, 0.16, 0.25, 0.33, 0.5, 0.75])
        events.append(SimpleNamespace(
            pitch=random.randint(40, 84), start=now, end=now + duration,
            velocity=random.randint(20, 110)))

    result = generate_score_from_events(
        events, tmp_path / 'random.score',
        ScoreOptions(bpm=120, time_signature='4/4', pdf=False, svg=False,
                     score_midi=False))
    ratios, groups = _tuplet_report(result.musicxml)
    assert ratios <= {(3, 2)}
    assert all(size == 3 for _, size in groups), groups
    _, details, divisions = tuplet_details(result.musicxml)
    assert all(tuplet_span_ok(item, divisions) for item in details), details
    assert converter.parse(str(result.musicxml)) is not None


def test_triplet_beams_are_balanced(tmp_path):
    """music21 在纯三连音上会写出悬空 beam；输出前必须清理。"""
    import xml.etree.ElementTree as ET

    from transkun_app.score import generate_score_from_events

    events = []
    beat = 0.5
    now = 0.0
    for measure in range(12):
        for beat_index in range(4):
            for index in range(3):
                start = now + index * (beat / 3)
                events.append(SimpleNamespace(
                    pitch=60 + ((measure * 4 + beat_index + index) % 12),
                    start=start, end=start + beat / 3, velocity=80))
            now += beat

    result = generate_score_from_events(
        events, tmp_path / 'beams.score',
        ScoreOptions(bpm=120, time_signature='4/4', pdf=False, svg=False,
                     score_midi=False))
    root = ET.parse(str(result.musicxml)).getroot()
    for measure in root.iter('measure'):
        open_beams = {}
        for note in measure.findall('note'):
            if note.find('chord') is not None:
                continue
            staff = note.findtext('staff', '1')
            voice = note.findtext('voice', '1')
            for beam in note.findall('beam'):
                key = (staff, voice, beam.get('number'))
                value = beam.text
                if value == 'begin':
                    assert key not in open_beams
                    open_beams[key] = True
                elif value == 'continue':
                    assert key in open_beams
                elif value == 'end':
                    assert key in open_beams
                    del open_beams[key]
        assert not open_beams


def test_musicxml_has_sound_tempo(tmp_path):
    """M.M. 文本方向必须同时带 <sound tempo>，否则播放器按 120 BPM。"""
    import xml.etree.ElementTree as ET

    from transkun_app.score import generate_score_from_events

    events = [SimpleNamespace(pitch=60, start=0.0, end=0.5, velocity=80)]
    result = generate_score_from_events(
        events, tmp_path / 'tempo.score',
        ScoreOptions(bpm=95, time_signature='4/4', pdf=False, svg=False,
                     score_midi=False))
    root = ET.parse(str(result.musicxml)).getroot()
    tempos = [float(item.get('tempo')) for item in root.iter('sound')
              if item.get('tempo')]
    words = [item.text or '' for item in root.iter('words')]
    assert any(abs(value - 95.0) < 0.01 for value in tempos), tempos
    assert any('M.M. 95' in value for value in words), words


def test_score_midi_tempo_matches_bpm(tmp_path):
    import mido

    from transkun_app.score import generate_score_from_events

    events = [SimpleNamespace(pitch=60, start=0.0, end=0.5, velocity=80)]
    result = generate_score_from_events(
        events, tmp_path / 'tempo.score',
        ScoreOptions(bpm=95, time_signature='4/4', pdf=False, svg=False,
                     score_midi=True))
    midi = mido.MidiFile(str(result.midi))
    tempos = [mido.tempo2bpm(message.tempo)
              for track in midi.tracks for message in track
              if message.type == 'set_tempo']
    assert tempos, 'score MIDI 没有 set_tempo'
    assert all(abs(value - 95.0) < 0.01 for value in tempos), tempos


def test_score_midi_length_close_to_input(tmp_path):
    import mido

    from transkun_app.score import generate_score_from_events

    bpm = 95.0
    beat = 60.0 / bpm
    events = [SimpleNamespace(pitch=60, start=index * beat,
                              end=(index + 1) * beat, velocity=80)
              for index in range(64)]
    result = generate_score_from_events(
        events, tmp_path / 'length.score',
        ScoreOptions(bpm=bpm, time_signature='4/4', pdf=False, svg=False,
                     score_midi=True))
    midi = mido.MidiFile(str(result.midi))
    raw_length = 64 * beat
    assert abs(midi.length - raw_length) / raw_length < 0.02, (
        midi.length, raw_length)


def test_mixed_duration_triplet_group(tmp_path):
    """同一起音上不同时值的和弦不应阻止三连音聚组。"""
    from transkun_app.score.layout import ChordEvent
    from transkun_app.score.rhythm import normalize_rhythm

    chords = [
        ChordEvent('R', 0.0, 1 / 6, [60], 80),
        ChordEvent('R', 1 / 3, 1 / 3, [64], 80),
        ChordEvent('R', 2 / 3, 1 / 6, [67], 80),
    ]
    result = normalize_rhythm(chords, '4/4', 'auto')
    assert result.stats['tuplet_groups'] == 1
    assert all(chord.tuplet == (3, 2) for chord in result.chords)
    assert all(abs(chord.qdur - 1 / 3) < 1e-9
               for chord in result.chords)
