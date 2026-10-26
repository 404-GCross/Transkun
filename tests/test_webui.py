"""WebUI 接口测试：复用 selftest 的假 runner 与客户端。"""

from __future__ import annotations

from transkun_app.webui import selftest as webui_selftest


def test_auth():
    webui_selftest.test_auth()


def test_limits():
    webui_selftest.test_limits()


def test_job_flow():
    webui_selftest.test_job_flow()


def test_retry():
    webui_selftest.test_retry()


def test_cancel():
    webui_selftest.test_cancel()


def test_queue_full():
    webui_selftest.test_queue_full()


def test_failure():
    webui_selftest.test_failure()


def test_csrf():
    webui_selftest.test_csrf()
