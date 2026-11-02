"""CLI 参数与入口测试（不加载模型）。"""

from __future__ import annotations

import pytest

from transkun_app import cli


def test_defaults():
    args = cli.build_parser().parse_args(['song.wav'])
    assert args.audio == 'song.wav'
    assert args.device == 'auto'
    assert args.port == 8765
    assert args.score is False


def test_score_defaults():
    args = cli.build_parser().parse_args(['song.wav', '--score'])
    assert args.score is True
    assert args.score_mode == 'block'
    assert args.score_tuplet_mode == 'auto'
    assert args.score_key == 'auto'
    assert args.score_time_signature == 'auto'
    assert args.score_no_pdf is False


def test_web_and_audio_conflict():
    with pytest.raises(SystemExit):
        # main() 自己 parser.error；直接调用会打印用法并退出。
        cli.main(['--web', 'song.wav'])


def test_version(capsys):
    assert cli.main(['--version']) == 0
    out = capsys.readouterr().out
    assert 'Transkun 0.3.2' in out
    assert '2.0.1' in out


def test_cli_without_audio_prints_help(capsys):
    assert cli.main(['--cli']) == 0
    out = capsys.readouterr().out
    assert 'transkun-app' in out
    assert '--web --lan' in out


def test_gui_flag_is_parsed():
    args = cli.build_parser().parse_args(['--gui', 'song.wav'])
    assert args.gui is True
    assert args.audio == 'song.wav'
