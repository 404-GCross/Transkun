"""打谱流水线：分析 → 量化 → 左右手 → 和弦 → MusicXML → SVG/PDF。"""

from __future__ import annotations

import json
from pathlib import Path

from transkun_app.score.analysis import (
    NoteEvent, estimate_bpm, estimate_key, key_to_dict, load_midi_notes,
    notes_from_events, parse_key, quantize_notes)
from transkun_app.score.layout import assign_hands, build_chords
from transkun_app.score.rhythm import normalize_rhythm
from transkun_app.score.model import ScoreOptions, ScoreResult
from transkun_app.score.notation import (
    build_score, count_measures, write_musicxml, write_score_midi)
from transkun_app.score.render import render_pdf, render_svg


def _base_path(value) -> Path:
    base = Path(value)
    if base.suffix in ('.musicxml', '.pdf', '.mid', '.json'):
        base = base.with_suffix('')
    if not base.name.endswith('.score'):
        base = base.with_name(base.name + '.score')
    return base


def _time_signature(name: str, notes: list[NoteEvent]) -> str:
    if name and name != 'auto':
        return name
    # 自动拍号暂时保守用 4/4；拍号识别不可靠，UI/CLI 可覆盖。
    return '4/4'


def _generate(notes: list[NoteEvent], base: Path,
              options: ScoreOptions) -> ScoreResult:
    if not notes:
        raise ValueError('没有可打谱的音符')

    bpm = float(options.bpm) if options.bpm else estimate_bpm(notes)
    key_obj = parse_key(options.key)
    key_auto = key_obj is None
    if key_obj is None:
        key_obj = estimate_key(notes)
    if key_obj is None:
        from music21 import key as m21key
        key_obj = m21key.Key('C')
    time_signature = _time_signature(options.time_signature, notes)

    quantized = quantize_notes(notes, bpm,
                               tuplet_mode=options.tuplet_mode)
    assign_hands(quantized, split=options.split, max_span=options.max_span)
    layout = build_chords(
        quantized, mode=options.mode, max_span=options.max_span,
        max_notes=options.max_notes, arpeggio_ms=options.arpeggio_ms,
        max_chord_beats=options.max_chord_beats)
    rhythm = normalize_rhythm(
        layout.chords, time_signature=time_signature,
        tuplet_mode=options.tuplet_mode)

    title = options.title or base.name
    score = build_score(
        rhythm.chords, title=title, composer=options.composer,
        key_obj=key_obj, time_signature=time_signature, bpm=bpm)
    measures = count_measures(score)

    base.parent.mkdir(parents=True, exist_ok=True)
    musicxml = write_musicxml(score, Path(str(base) + '.musicxml'))

    result = ScoreResult(base=base, musicxml=musicxml)
    result.stats = dict(layout.stats)
    result.stats.update(rhythm.stats)
    result.stats['measures'] = measures
    result.stats['quantized_notes'] = len(quantized)
    result.stats['bpm'] = bpm
    result.metadata = {
        'title': title,
        'composer': options.composer,
        'key': key_to_dict(key_obj),
        'key_auto': key_auto,
        'time_signature': time_signature,
        'bpm': bpm,
        'mode': options.mode,
        'tuplet_mode': options.tuplet_mode,
        'split': options.split,
        'max_span': options.max_span,
        'max_notes': options.max_notes,
        'arpeggio_ms': options.arpeggio_ms,
    }

    if options.score_midi:
        try:
            result.midi = write_score_midi(score, Path(str(base) + '.mid'))
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f'score MIDI 生成失败：{exc}')

    if options.svg or options.pdf:
        try:
            pages, warnings, page_count = render_svg(
                musicxml, base, page_size=options.page_size)
            result.svg_pages = pages
            result.warnings.extend(warnings)
            result.stats['pages'] = page_count
            if options.pdf:
                pdf, pdf_warnings = render_pdf(
                    pages, Path(str(base) + '.pdf'))
                result.pdf = pdf
                result.warnings.extend(pdf_warnings)
        except Exception as exc:  # noqa: BLE001 - 谱面已经写好，渲染失败不致命
            result.warnings.append(f'SVG/PDF 渲染失败：{exc}')

    meta_path = Path(str(base) + '.json')
    meta_path.write_text(json.dumps({
        'options': options.to_dict(),
        'metadata': result.metadata,
        'stats': result.stats,
        'warnings': result.warnings,
        'files': {
            'musicxml': str(result.musicxml) if result.musicxml else None,
            'pdf': str(result.pdf) if result.pdf else None,
            'midi': str(result.midi) if result.midi else None,
            'svg': [str(p) for p in result.svg_pages],
        },
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def generate_score(midi_path, output_base=None,
                   options: ScoreOptions | None = None) -> ScoreResult:
    """从 MIDI 生成打谱版。``output_base`` 不带扩展名。"""
    options = options or ScoreOptions()
    midi_path = Path(midi_path)
    notes = load_midi_notes(midi_path)
    base = (_base_path(output_base) if output_base
            else midi_path.with_suffix('.score'))
    return _generate(notes, base, options)


def generate_score_from_events(events, output_base, options=None) -> ScoreResult:
    """从 Transkun 事件直接生成，避免经过假 120 BPM 的 MIDI tick。"""
    options = options or ScoreOptions()
    notes = notes_from_events(events)
    return _generate(notes, _base_path(output_base), options)
