"""打谱版自检：合成音符 → MusicXML / PDF，不需要模型或音频。"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from transkun_app.score.model import ScoreOptions


SELFTEST_BPM = 95


def _demo_events(bpm=SELFTEST_BPM):
    events = []
    beat = 60.0 / bpm

    def add(pitch, start, end, velocity=80):
        events.append(SimpleNamespace(pitch=pitch, start=start, end=end,
                                      velocity=velocity))

    # 4 小节：右手和弦 + 旋律，左手低音（时间用拍数表示，随 BPM 缩放）
    for bar in range(4):
        base = bar * 4 * beat
        for pitch in (62, 66, 69, 74):
            add(pitch, base, base + beat, 85)
        add(78, base + beat, base + 2 * beat, 90)
        add(81, base + 2 * beat, base + 3 * beat, 88)
        add(74, base, base + 4 * beat, 70)
        add(50, base, base + 2 * beat, 80)
        add(45, base + 2 * beat, base + 4 * beat, 75)
    # 滚奏和弦（应加琶音记号）
    for index, pitch in enumerate((62, 66, 69, 74)):
        add(pitch, 16 * beat + index * 0.02, 20 * beat, 80)
    # 超宽和弦（应删内声部）
    add(45, 20 * beat, 24 * beat, 80)
    add(64, 20 * beat, 24 * beat, 80)
    add(72, 20 * beat, 24 * beat, 80)
    add(84, 20 * beat, 24 * beat, 80)
    # 三连音：两整组 + 一个孤立音（孤立音应回退到 16 分，不生成单音符 tuplet）
    for group in range(2):
        base = 24 * beat + group * beat
        for index, pitch in enumerate((72, 76, 79)):
            add(pitch, base + index * beat / 3,
                base + (index + 1) * beat / 3, 80)
    add(84, 26 * beat, 26 * beat + beat / 3, 80)
    return events


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Transkun 打谱版自检')
    parser.add_argument('--keep', metavar='DIR', help='保留产物')
    parser.add_argument('--no-pdf', action='store_true',
                        help='不生成 PDF（macOS/无 cairo 环境）')
    args = parser.parse_args(argv)

    work = Path(args.keep) if args.keep else Path(
        tempfile.mkdtemp(prefix='transkun-score-selftest-'))
    work.mkdir(parents=True, exist_ok=True)
    base = work / 'demo.score'
    options = ScoreOptions(
        mode='block', bpm=SELFTEST_BPM, arpeggio_ms=50,
        title='自检 · 中文标题', composer='Transkun',
        pdf=not args.no_pdf, svg=True, score_midi=True)

    from transkun_app.score import generate_score_from_events
    result = generate_score_from_events(_demo_events(), base, options)
    print(f'MusicXML: {result.musicxml}')
    print(f'PDF: {result.pdf}')
    print(f'Score MIDI: {result.midi}')
    print('统计: ' + result.detail())

    checks = [
        ('MusicXML 存在', bool(result.musicxml and result.musicxml.exists())),
        ('Score MIDI 存在', bool(result.midi and result.midi.exists())),
        ('有音符', result.stats.get('notes_out', 0) > 0),
        ('有小节', result.stats.get('measures', 0) > 0),
    ]

    if result.musicxml and result.musicxml.exists():
        try:
            from music21 import converter
            parsed = converter.parse(str(result.musicxml))
            checks.append(('MusicXML 可解析', parsed is not None))
        except Exception as exc:  # noqa: BLE001
            checks.append((f'MusicXML 可解析（{exc}）', False))
        try:
            from transkun_app.score.validate import (
                tuplet_details, tuplet_span_ok)
            ratios, details, divisions = tuplet_details(result.musicxml)
            checks.append(('三连音比例只有 3:2', ratios <= {(3, 2)}))
            checks.append(('三连音组都是 3 个音',
                           bool(details) and all(item['count'] == 3
                                                for item in details)))
            checks.append(('三连音组不跨小节',
                           all(item['start_measure'] == item['stop_measure']
                               for item in details)))
            checks.append(('三连音跨度匹配',
                           all(tuplet_span_ok(item, divisions)
                               for item in details)))
        except Exception as exc:  # noqa: BLE001
            checks.append((f'三连音结构（{exc}）', False))
        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(str(result.musicxml)).getroot()
            tempos = [float(item.get('tempo')) for item in root.iter('sound')
                      if item.get('tempo')]
            checks.append(('MusicXML 有 sound tempo',
                           any(abs(value - SELFTEST_BPM) < 0.1
                               for value in tempos)))
        except Exception as exc:  # noqa: BLE001
            checks.append((f'MusicXML 速度（{exc}）', False))
    if result.midi and result.midi.exists():
        try:
            import mido
            midi = mido.MidiFile(str(result.midi))
            tempos = [mido.tempo2bpm(message.tempo)
                      for track in midi.tracks for message in track
                      if message.type == 'set_tempo']
            checks.append(('Score MIDI 速度匹配',
                           bool(tempos) and all(
                               abs(value - SELFTEST_BPM) < 0.1
                               for value in tempos)))
        except Exception as exc:  # noqa: BLE001
            checks.append((f'Score MIDI 速度（{exc}）', False))
    if not args.no_pdf:
        checks.append(('PDF 存在', bool(result.pdf and result.pdf.exists())))
        if result.pdf and result.pdf.exists():
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(result.pdf))
                checks.append((f'PDF 页数 {len(reader.pages)}',
                               len(reader.pages) > 0))
            except Exception as exc:  # noqa: BLE001
                checks.append((f'PDF 可打开（{exc}）', False))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + name)
    for warning in result.warnings:
        print(f'  提示: {warning}')
    if failed:
        print(f'\n{len(failed)} 项失败: {failed}', file=sys.stderr)
        return 1
    print('\n打谱自检通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
