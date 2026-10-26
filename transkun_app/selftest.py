#!/usr/bin/env python3
"""端到端自检：合成一段钢琴琶音 → Transkun → MIDI → 可选整理。

不需要外部音频；权重随仓库提供。CPU 上 12 秒音频约 30~40 秒。

    transkun-app --selftest
    python -m transkun_app.selftest --duration 8 --keep /tmp/transkun-selftest
    python -m transkun_app.selftest --audio song.mp3
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


def make_arpeggio(path: Path, duration: float = 12.0,
                  sample_rate: int = 44100) -> Path:
    """写一段 C 大调琶音 WAV（44.1 kHz 单声道 16-bit）。"""
    from scipy.io import wavfile

    total = int(sample_rate * duration)
    out = np.zeros(total, dtype=np.float32)
    pitches = [60, 64, 67, 72, 76, 79, 84, 79, 76, 72, 67, 64]
    step = 0.25
    # 短音频也要有音：音长最多占 80%，并保证至少放得下一个起音。
    note_len = min(2.0, max(step, duration * 0.8))
    for index, start in enumerate(
            np.arange(0, max(step, duration - note_len + step), step)):
        frequency = 440.0 * 2 ** ((pitches[index % len(pitches)] - 69) / 12)
        count = int(sample_rate * note_len)
        times = np.arange(count) / sample_rate
        envelope = np.exp(-times * 2.2) * np.minimum(1.0, times / 0.005)
        wave = (np.sin(2 * np.pi * frequency * times)
                + 0.45 * np.sin(2 * np.pi * 2 * frequency * times)
                + 0.22 * np.sin(2 * np.pi * 3 * frequency * times)
                + 0.10 * np.sin(2 * np.pi * 4 * frequency * times))
        wave = wave * envelope * 0.18
        offset = int(start * sample_rate)
        end = min(total, offset + count)
        out[offset:end] += wave[:end - offset]
    out = np.clip(out, -1, 1)
    wavfile.write(str(path), sample_rate, (out * 32767).astype(np.int16))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Transkun 端到端自检')
    parser.add_argument('--audio', help='用指定音频（默认合成琶音）')
    parser.add_argument('--duration', type=float, default=12.0,
                        help='合成音频时长（秒，默认 12）')
    parser.add_argument('--device', default='cpu',
                        help='cpu / cuda / auto（默认 cpu，自检要可重复）')
    parser.add_argument('--keep', metavar='DIR',
                        help='保留自检产物到该目录')
    parser.add_argument('--no-rearrange', action='store_true',
                        help='跳过 MIDI 整理')
    args = parser.parse_args(argv)

    from transkun_app import backend
    import transkun_app.midi_rearrange as midi_rearrange

    work = Path(args.keep) if args.keep else Path(
        tempfile.mkdtemp(prefix='transkun-selftest-'))
    work.mkdir(parents=True, exist_ok=True)

    if args.audio:
        audio = Path(args.audio)
        if not audio.exists():
            print(f'音频不存在: {audio}', file=sys.stderr)
            return 2
    else:
        audio = work / 'arpeggio.wav'
        make_arpeggio(audio, args.duration)
        print(f'合成音频: {audio}（{args.duration:g} 秒）')

    transcriber = backend.get_transcriber(device=args.device)
    print(f'设备: {backend.device_summary(transcriber.device)}')

    def progress(done, total):
        if total:
            print(f'\r转谱: {done}/{total}', end='', flush=True)

    start = time.time()
    events = transcriber.transcribe_file(audio, progress_cb=progress)
    elapsed = time.time() - start
    print()

    notes = backend.note_count(events)
    pedals = backend.pedal_count(events)
    print(f'事件: {len(events)}（音符 {notes}，踏板区间 {pedals}）')

    output = work / 'selftest.mid'
    backend.save_midi(events, output)
    print(f'MIDI: {output}')

    checks = [
        ('输出有音符', notes > 0),
        ('MIDI 文件存在', output.exists() and output.stat().st_size > 0),
    ]
    if not args.no_rearrange:
        result = midi_rearrange.rearrange_file(output)
        print(f'整理版: {result.path}（{result.detail()}）')
        checks.append(('整理版存在', result.path.exists()))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + name)
    print(f'耗时 {elapsed:.1f} 秒（音频 {args.duration:g} 秒）')
    if failed:
        print(f'\n{len(failed)} 项失败: {failed}', file=sys.stderr)
        return 1
    print('\n端到端自检通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
