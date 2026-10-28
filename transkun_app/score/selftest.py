"""打谱版自检：合成音符 → MusicXML / PDF，不需要模型或音频。"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from transkun_app.score.model import ScoreOptions


def _demo_events():
    events = []

    def add(pitch, start, end, velocity=80):
        events.append(SimpleNamespace(pitch=pitch, start=start, end=end,
                                      velocity=velocity))

    # 4 小节：右手和弦 + 旋律，左手低音
    for bar in range(4):
        base = bar * 2.0
        for pitch in (62, 66, 69, 74):
            add(pitch, base, base + 0.5, 85)
        add(78, base + 0.5, base + 1.0, 90)
        add(81, base + 1.0, base + 1.5, 88)
        add(74, base, base + 2.0, 70)
        add(50, base, base + 1.0, 80)
        add(45, base + 1.0, base + 2.0, 75)
    # 滚奏和弦（应加琶音记号）
    for index, pitch in enumerate((62, 66, 69, 74)):
        add(pitch, 8.0 + index * 0.02, 10.0, 80)
    # 超宽和弦（应删内声部）
    add(45, 10.0, 12.0, 80)
    add(64, 10.0, 12.0, 80)
    add(72, 10.0, 12.0, 80)
    add(84, 10.0, 12.0, 80)
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
        mode='block', bpm=120, arpeggio_ms=50, title='自检 · 中文标题', composer='Transkun',
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
    if not args.no_pdf:
        checks.append(('PDF 存在', bool(result.pdf and result.pdf.exists())))
        if result.pdf and result.pdf.exists():
            try:
                import pikepdf
                with pikepdf.open(str(result.pdf)) as pdf:
                    checks.append((f'PDF 页数 {len(pdf.pages)}', len(pdf.pages) > 0))
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
