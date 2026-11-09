import logging
import ssl

import librouteros
import pytest

logging.disable(logging.CRITICAL)

import mkmassupdate
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


def test_ssl_uses_verified_context_and_host_name_by_default(monkeypatch):
    captured = _capture_connect(monkeypatch)
    wrapped = []

    class FakeContext:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def wrap_socket(self, sock, *, server_hostname):
            wrapped.append((sock, server_hostname))
            return sock

    context = FakeContext()
    context_args = []
    monkeypatch.setattr(
        mkmassupdate.ssl,
        'create_default_context',
        lambda *, cafile=None: context_args.append(cafile) or context,
    )

    _connect_to_router(
        ('router.example.test', 8729, None, None, True), 'admin', 'pw', 5
    )
    captured['ssl_wrapper']('socket')

    assert context_args == [None]
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert wrapped == [('socket', 'router.example.test')]


def test_ssl_uses_configured_ca_bundle(monkeypatch):
    captured = _capture_connect(monkeypatch)
    ca_files = []

    class FakeContext:
        def wrap_socket(self, sock, *, server_hostname):
            return sock

    context = FakeContext()
    monkeypatch.setattr(
        mkmassupdate.ssl,
        'create_default_context',
        lambda *, cafile=None: ca_files.append(cafile) or context,
    )

    _connect_to_router(
        ('10.0.0.1', 8729, None, None, True),
        'admin', 'pw', 5, ssl_ca_file='/etc/mikrotik/routers-ca.pem',
    )

    assert ca_files == ['/etc/mikrotik/routers-ca.pem']
    assert captured['ssl_wrapper']('socket') == 'socket'


def test_ssl_can_explicitly_disable_certificate_verification(monkeypatch):
    captured = _capture_connect(monkeypatch)

    class FakeContext:
        def __init__(self, _protocol):
            self.check_hostname = True
            self.verify_mode = ssl.CERT_REQUIRED

        def wrap_socket(self, sock, *, server_hostname):
            return sock

    monkeypatch.setattr(mkmassupdate.ssl, 'SSLContext', FakeContext)

    _connect_to_router(
        ('10.0.0.1', 8729, None, None, True),
        'admin', 'pw', 5, ssl_verify=False,
    )

    assert captured['ssl_wrapper']('socket') == 'socket'


def test_legacy_ssl_ciphers_are_used_only_when_explicitly_requested(monkeypatch):
    captured = _capture_connect(monkeypatch)
    ciphers = []

    class FakeContext:
        def set_ciphers(self, cipher_list):
            ciphers.append(cipher_list)

        def wrap_socket(self, sock, *, server_hostname):
            return sock

    context = FakeContext()
    monkeypatch.setattr(
        mkmassupdate.ssl,
        'create_default_context',
        lambda *, cafile=None: context,
    )

    _connect_to_router(
        ('10.0.0.1', 8729, None, None, True),
        'admin', 'pw', 5, ssl_legacy_ciphers=True,
    )

    assert ciphers == ['ALL:@SECLEVEL=0']
    assert captured['ssl_wrapper']('socket') == 'socket'
