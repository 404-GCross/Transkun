"""CLI 参数与入口测试（不加载模型）。"""

from __future__ import annotations

import pytest

from transkun_app import cli


def test_defaults():
    args = cli.build_parser().parse_args(['song.wav'])
    assert args.audio == 'song.wav'
    assert args.device == 'auto'
    assert args.port == 8765
    assert args.rearrange is False


def test_rearrange_defaults():
    args = cli.build_parser().parse_args(['song.wav', '--rearrange'])
    assert (args.rearrange_velocity, args.rearrange_min_ms,
            args.rearrange_chord_ms, args.rearrange_repeat_ms) == (30, 30, 60, 120)


def test_web_and_audio_conflict():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        # main() 自己 parser.error；直接调用会打印用法并退出。
        cli.main(['--web', 'song.wav'])


def test_version(capsys):
    assert cli.main(['--version']) == 0
    out = capsys.readouterr().out
    assert 'Transkun 0.1.0' in out
    assert '2.0.1' in out


def test_no_args_prints_help(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert 'transkun-app' in out
    assert '--web --lan' in out
