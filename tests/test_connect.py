import logging

import librouteros
import pytest

logging.disable(logging.CRITICAL)

from mkmassupdate import _connect_to_router


def _capture_connect(monkeypatch) -> dict:
    captured: dict = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(librouteros, 'connect', fake_connect)
    return captured


def test_connect_uses_configured_timeout(monkeypatch):
    captured = _capture_connect(monkeypatch)

    _connect_to_router(('10.0.0.1', 8728, None, None, False), 'admin', 'pw', 5, False)

    assert captured['timeout'] == 5


def test_connect_uses_larger_configured_timeout(monkeypatch):
    captured = _capture_connect(monkeypatch)

    _connect_to_router(('10.0.0.1', 8728, None, None, False), 'admin', 'pw', 120, False)

    assert captured['timeout'] == 120


def test_connect_uses_custom_credentials_and_port(monkeypatch):
    captured = _capture_connect(monkeypatch)

    _connect_to_router(('10.0.0.1', 8730, 'bob', 'secret', False), 'admin', 'pw', 10, False)

    assert captured['host'] == '10.0.0.1'
    assert captured['port'] == 8730
    assert captured['username'] == 'bob'
    assert captured['password'] == 'secret'
    assert 'ssl_wrapper' not in captured


def test_global_ssl_adds_ssl_wrapper(monkeypatch):
    captured = _capture_connect(monkeypatch)

    _connect_to_router(('10.0.0.1', 8728, None, None, False), 'admin', 'pw', 5, True)

    assert 'ssl_wrapper' in captured
