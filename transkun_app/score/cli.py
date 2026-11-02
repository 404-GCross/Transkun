"""``transkun-score``：从已有 MIDI 生成打谱版。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transkun_app.score.model import (
    KEYS, MODES, TIME_SIGNATURES, TUPLET_MODES, ScoreOptions)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='transkun-score',
        description='把钢琴演奏 MIDI 变成可读的 MusicXML / PDF 打谱版')
    parser.add_argument('midi', nargs='?', help='输入 MIDI（演奏版）')
    parser.add_argument('-o', '--output', help='输出前缀（默认 <输入>.score）')
    parser.add_argument('--mode', choices=MODES, default='block',
                        help='block=缩编和弦块（默认），faithful=保留全部音')
    parser.add_argument('--tuplet-mode', choices=TUPLET_MODES,
                        default='auto',
                        help='三连音：auto=聚组 3:2（默认），none=纯 16 分')
    parser.add_argument('--key', choices=KEYS, default='auto',
                        help='调性（默认 auto）')
    parser.add_argument('--time-signature', choices=TIME_SIGNATURES,
                        default='auto', help='拍号（默认 auto → 4/4）')
    parser.add_argument('--bpm', type=float, default=None,
                        help='速度（默认自动搜索）')
    parser.add_argument('--split', type=int, default=60,
                        help='左右手分界偏置（默认 60）')
    parser.add_argument('--max-span', type=int, default=19,
                        help='单手最大跨度（半音，默认 19）')
    parser.add_argument('--max-notes', type=int, default=5,
                        help='和弦最多音数（默认 5）')
    parser.add_argument('--arpeggio-ms', type=float, default=70,
                        help='琶音判定阈值（毫秒，默认 70）')
    parser.add_argument('--page-size', choices=['A4', 'Letter'], default='A4')
    parser.add_argument('--no-pdf', action='store_true')
    parser.add_argument('--no-svg', action='store_true')
    parser.add_argument('--no-score-midi', action='store_true')
    parser.add_argument('--title', default='')
    parser.add_argument('--composer', default='')
    parser.add_argument('--selftest', action='store_true',
                        help='用合成音符跑一遍，不需要模型/音频')
    parser.add_argument('--keep', metavar='DIR',
                        help='--selftest 的产物保留目录')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        from transkun_app.score import selftest
        return selftest.main(['--keep', args.keep] if args.keep else [])
    if not args.midi:
        parser.error('需要输入 MIDI（或用 --selftest）')

    source = Path(args.midi)
    if not source.exists():
        print(f'MIDI 不存在: {source}', file=sys.stderr)
        return 2
    base = Path(args.output) if args.output else source.with_suffix('.score')
    options = ScoreOptions(
        mode=args.mode, tuplet_mode=args.tuplet_mode, key=args.key,
        time_signature=args.time_signature, bpm=args.bpm, split=args.split,
        max_span=args.max_span, max_notes=args.max_notes,
        arpeggio_ms=args.arpeggio_ms, pdf=not args.no_pdf,
        svg=not args.no_svg, score_midi=not args.no_score_midi,
        title=args.title, composer=args.composer, page_size=args.page_size)

    from transkun_app.score import generate_score
    try:
        result = generate_score(source, base, options)
    except Exception as exc:  # noqa: BLE001 - CLI 直接给用户看
        print(f'打谱失败: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1

    print(f'MusicXML: {result.musicxml}')
    if result.pdf:
        print(f'PDF:      {result.pdf}')
    if result.midi:
        print(f'Score MIDI: {result.midi}')
    for warning in result.warnings:
        print(f'提示: {warning}', file=sys.stderr)
    print('统计: ' + result.detail())
    return 0


if __name__ == '__main__':
    sys.exit(main())
