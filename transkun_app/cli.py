#!/usr/bin/env python3
"""Transkun 应用入口：命令行转谱 + WebUI。

``transkun`` 仍是上游的模型 CLI（``transkun in.wav out.mid``）；本入口提供
应用层功能，不会与上游参数冲突：

    transkun-app song.mp3 -o song.mid
    transkun-app --web --lan
    transkun-app --batch-input ./audio --batch-output ./midi
    transkun-app --check
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from transkun_app import APP_NAME, UPSTREAM_VERSION, __version__

AUDIO_EXTENSIONS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac',
                    '.aiff', '.aif', '.opus')

REARRANGE_DEFAULTS = {
    'velocity': 30,
    'min_ms': 30,
    'chord_ms': 60,
    'repeat_ms': 120,
}


def _fmt_clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f'{total // 60}:{total % 60:02d}'


def _print_result(events, output: Path, elapsed: float, device_info: str):
    from transkun_app import backend

    notes = backend.note_count(events)
    pedals = backend.pedal_count(events)
    print(f'设备: {device_info}')
    print(f'完成: {notes} 个音符，{pedals} 个踏板区间，'
          f'耗时 {elapsed:.1f} 秒（{_fmt_clock(elapsed)}）')
    print(f'MIDI 已保存: {output}')


def run_cli(args) -> int:
    from transkun_app import backend
    import transkun_app.midi_rearrange as midi_rearrange

    audio = Path(args.audio)
    if not audio.exists():
        print(f'音频文件不存在: {audio}', file=sys.stderr)
        return 2

    transcriber = backend.get_transcriber(
        weight=args.weight, conf=args.conf, device=args.device,
        threads=args.threads)
    device_info = backend.device_summary(transcriber.device)
    print(f'设备: {device_info}')
    print('加载模型…', end='', flush=True)

    last = [-1]

    def progress(done, total):
        if total:
            percent = int(100 * done / total)
            if percent != last[0]:
                last[0] = percent
                print(f'\r转谱进度: {done}/{total} ({percent}%)',
                      end='', flush=True)
        else:
            print(f'\r已处理 {done} 个片段', end='', flush=True)

    start = time.time()
    events = transcriber.transcribe_file(audio, progress_cb=progress)
    print()

    output = Path(args.output) if args.output else audio.with_suffix('.mid')
    backend.save_midi(events, output)
    _print_result(events, output, time.time() - start, device_info)

    if args.rearrange:
        result = midi_rearrange.rearrange_file(
            output,
            velocity=args.rearrange_velocity,
            min_ms=args.rearrange_min_ms,
            chord_ms=args.rearrange_chord_ms,
            repeat_ms=args.rearrange_repeat_ms)
        print(f'整理版 MIDI: {result.path}')
        print('后处理: ' + result.detail())
    return 0


def run_batch(args) -> int:
    from transkun_app import backend
    import transkun_app.midi_rearrange as midi_rearrange

    input_dir = Path(args.batch_input)
    output_dir = Path(args.batch_output)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in input_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
    if not files:
        print(f'目录中没有支持的音频文件: {input_dir}', file=sys.stderr)
        return 2

    transcriber = backend.get_transcriber(
        weight=args.weight, conf=args.conf, device=args.device,
        threads=args.threads)
    print(f'设备: {backend.device_summary(transcriber.device)}，共 {len(files)} 个文件')
    failures = 0
    for index, audio_path in enumerate(files, 1):
        output = output_dir / f'{audio_path.stem}.mid'
        print(f'[{index}/{len(files)}] {audio_path.name}')
        start = time.time()
        try:
            events = transcriber.transcribe_file(audio_path)
            backend.save_midi(events, output)
        except Exception as exc:  # noqa: BLE001 - 批量模式继续下一个
            failures += 1
            print(f'  失败: {type(exc).__name__}: {exc}', file=sys.stderr)
            continue
        print(f'  -> {output}（{backend.note_count(events)} 个音符，'
              f'{time.time() - start:.1f} 秒）')
        if args.rearrange:
            result = midi_rearrange.rearrange_file(
                output,
                velocity=args.rearrange_velocity,
                min_ms=args.rearrange_min_ms,
                chord_ms=args.rearrange_chord_ms,
                repeat_ms=args.rearrange_repeat_ms)
            print(f'  -> {result.path}')
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='transkun-app',
        description='Transkun：钢琴音频转 MIDI（命令行 / 浏览器 WebUI）')
    parser.add_argument(
        'audio', nargs='?', help='输入音频（wav/mp3/flac/ogg/m4a/aac/aiff/opus）')
    parser.add_argument('-o', '--output', help='输出 MIDI（默认与输入同名 .mid）')
    parser.add_argument(
        '--device', choices=['auto', 'cuda', 'cpu'], default='auto',
        help='推理设备（默认 auto：有 NVIDIA GPU 就用 CUDA）')
    parser.add_argument(
        '--threads', type=int, default=None, metavar='N',
        help='CPU 线程数（默认交给 PyTorch）')
    parser.add_argument('--weight', help='自定义权重 .pt（默认自带 2.0.pt）')
    parser.add_argument('--conf', help='自定义模型配置 .conf')

    parser.add_argument('--web', action='store_true',
                        help='启动 WebUI（配合 --lan 让手机/局域网访问）')
    parser.add_argument('--host', default='127.0.0.1',
                        help='WebUI 监听地址（默认 127.0.0.1）')
    parser.add_argument('--lan', action='store_true',
                        help='等价于 --host 0.0.0.0：局域网设备也能用（强制令牌）')
    parser.add_argument('--port', type=int, default=8765, metavar='N',
                        help='WebUI 端口（默认 8765，被占用时自动往后找）')
    parser.add_argument('--token', metavar='TOKEN',
                        help='WebUI 访问令牌（默认读取/生成 ~/.config/transkun/web_token）')
    parser.add_argument('--new-token', action='store_true',
                        help='重新生成 WebUI 访问令牌（旧链接会失效）')
    parser.add_argument('--max-bytes', type=int, default=1024 ** 3, metavar='N',
                        help='WebUI 单文件上限，字节（默认 1 GiB）')
    parser.add_argument('--max-minutes', type=float, default=30, metavar='M',
                        help='WebUI 音频时长上限，分钟（默认 30）')
    parser.add_argument('--queue', type=int, default=8, metavar='N',
                        help='WebUI 队列上限（默认 8）')
    parser.add_argument('--keep-hours', type=float, default=24, metavar='H',
                        help='WebUI 结果保留小时数（默认 24）')

    parser.add_argument('--batch-input', metavar='DIR',
                        help='批量模式输入目录')
    parser.add_argument('--batch-output', metavar='DIR',
                        help='批量模式输出目录')

    parser.add_argument('--rearrange', action='store_true',
                        help='转谱后做一次 MIDI 整理（另存 <输出>.rearranged.mid）')
    parser.add_argument('--rearrange-velocity', type=int,
                        default=REARRANGE_DEFAULTS['velocity'], metavar='N',
                        help='整理：力度 <= 该值的音符删除（默认 30）')
    parser.add_argument('--rearrange-min-ms', type=int,
                        default=REARRANGE_DEFAULTS['min_ms'], metavar='MS',
                        help='整理：最小时值（毫秒，默认 30）')
    parser.add_argument('--rearrange-chord-ms', type=int,
                        default=REARRANGE_DEFAULTS['chord_ms'], metavar='MS',
                        help='整理：琶音整合窗口（毫秒，默认 60）')
    parser.add_argument('--rearrange-repeat-ms', type=int,
                        default=REARRANGE_DEFAULTS['repeat_ms'], metavar='MS',
                        help='整理：连续消除窗口（毫秒，默认 120）')

    parser.add_argument('--check', action='store_true',
                        help='检查运行依赖是否完整（不加载模型）')
    parser.add_argument('--selftest', action='store_true',
                        help='合成音频跑一遍完整转谱（需要权重，约 1 分钟）')
    parser.add_argument('--keep', metavar='DIR',
                        help='--selftest 的产物保留目录')
    parser.add_argument('--rearrange-only', action='store_true',
                        help='只跑 MIDI 整理自检（不需要模型）')
    parser.add_argument('--version', action='store_true', help='显示版本')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f'{APP_NAME} {__version__}（模型 2.0，基于上游 Transkun '
              f'{UPSTREAM_VERSION}）')
        return 0

    if args.rearrange_only:
        from transkun_app import midi_rearrange
        return midi_rearrange.selftest()

    if args.check:
        from transkun_app import import_check
        return import_check.main()

    if args.selftest:
        from transkun_app import selftest
        extra = ['--device', args.device]
        if args.keep:
            extra += ['--keep', args.keep]
        return selftest.main(extra)

    if args.web:
        if args.audio or args.batch_input:
            parser.error('--web 不能和音频文件 / --batch-input 一起用')
        try:
            from transkun_app.webui.server import run_server
        except ImportError as exc:
            print(f'WebUI 依赖缺失：{exc}', file=sys.stderr)
            print("安装：pip install 'transkun[app]'", file=sys.stderr)
            return 1
        return run_server(args)

    if args.batch_input:
        if not args.batch_output:
            parser.error('--batch-input 必须同时指定 --batch-output')
        return run_batch(args)

    if args.audio:
        return run_cli(args)

    parser.print_help()
    print()
    print('提示：在浏览器里用（手机也行）:')
    print('  transkun-app --web --lan')
    return 0


if __name__ == '__main__':
    sys.exit(main())
