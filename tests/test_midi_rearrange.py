"""MIDI 整理（后处理）测试：合成 MIDI，不需要模型。"""

from __future__ import annotations

import mido

from transkun_app import midi_rearrange


def build_midi():
    mid = mido.MidiFile(type=1, ticks_per_beat=960)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    mid.tracks.append(conductor)

    track = mido.MidiTrack()
    track.append(mido.Message('program_change', channel=0, program=0, time=0))
    # 弱音（应删）
    track.append(mido.Message('note_on', channel=0, note=60, velocity=10, time=0))
    track.append(mido.Message('note_off', channel=0, note=60, velocity=0, time=480))
    # 正常音
    track.append(mido.Message('note_on', channel=0, note=64, velocity=80, time=0))
    track.append(mido.Message('control_change', channel=0, control=64, value=127, time=0))
    track.append(mido.Message('note_off', channel=0, note=64, velocity=0, time=480))
    track.append(mido.Message('control_change', channel=0, control=64, value=0, time=0))
    mid.tracks.append(track)
    return mid


def test_rearrange_drops_weak_note_but_keeps_pedal():
    out, stats = midi_rearrange.rearrange_midi(
        build_midi(), velocity=30, min_ms=30, chord_ms=60, repeat_ms=120)

    assert stats['notes_in'] == 2
    assert stats['notes_out'] == 1
    assert stats['dropped_velocity'] == 1

    controls = [m for track in out.tracks for m in track
                if m.type == 'control_change']
    assert [m.value for m in controls] == [127, 0]
    assert all(m.control == 64 for m in controls)

    programs = [m for track in out.tracks for m in track
                if m.type == 'program_change']
    assert len(programs) == 1


def test_default_output_path():
    assert midi_rearrange.default_output_path('song.mid').name == \
        'song.rearranged.mid'
    assert midi_rearrange.default_output_path('song.rearranged.mid').name == \
        'song.rearranged.mid'


def test_rearrange_file_roundtrip(tmp_path):
    source = tmp_path / 'in.mid'
    build_midi().save(str(source))
    result = midi_rearrange.rearrange_file(source)
    assert result.path.exists()
    assert result.path.name == 'in.rearranged.mid'
    reloaded = mido.MidiFile(str(result.path))
    assert reloaded.ticks_per_beat == 960


def test_selftest_passes():
    assert midi_rearrange.selftest() == 0
