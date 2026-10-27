"""Tk 界面的无显示器测试。

本机/CI 没有 tkinter 或没有 DISPLAY 时自动跳过需要窗口的用例。
"""

from __future__ import annotations

import importlib.util
import sys
import pytest

from transkun_app import gui

HAS_TK = importlib.util.find_spec('tkinter') is not None
HAS_DISPLAY = bool(gui.os.environ.get('DISPLAY')) or sys.platform == 'darwin'


def test_load_and_save_config(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(gui, 'CONFIG_FILE', tmp_path / 'config.json')
    assert gui.load_config() == {}
    gui.save_config({'device': 'cpu', 'rearrange_velocity': 40})
    assert gui.load_config()['rearrange_velocity'] == 40


def test_load_config_ignores_broken_file(tmp_path, monkeypatch):
    broken = tmp_path / 'config.json'
    broken.write_text('{not json', encoding='utf-8')
    monkeypatch.setattr(gui, 'CONFIG_FILE', broken)
    assert gui.load_config() == {}


def test_venv_candidates_include_packaged_runtime(monkeypatch):
    monkeypatch.delenv('TRANSKUN_VENV_DIR', raising=False)
    candidates = [str(p) for p in gui._venv_python_candidates()]
    assert any('.local/share/transkun/venv/bin/python' in p for p in candidates)


@pytest.mark.skipif(HAS_TK, reason='环境里有 tkinter，测不到缺失分支')
def test_check_fonts_without_tkinter():
    assert gui.check_fonts() == 1


@pytest.mark.skipif(HAS_TK, reason='环境里有 tkinter，测不到缺失分支')
def test_run_gui_without_tkinter():
    assert gui.run_gui() == 1


@pytest.mark.skipif(not (HAS_TK and HAS_DISPLAY), reason='需要 tkinter + 显示器')
def test_gui_smoke(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(gui, 'CONFIG_FILE', tmp_path / 'config.json')
    app = gui.TranskunApp()
    app.root.withdraw()
    app.log('smoke')
    app.root.update()
    app.root.destroy()
