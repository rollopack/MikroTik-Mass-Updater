import pytest
import sys
sys.path.insert(0, '/mnt/dropbox/Documenti/Mikrotik/MikroTik-Mass-Updater')

import logging
logging.disable(logging.CRITICAL)

from mkmassupdate import parse_host_line


def test_ip_only():
    result = parse_host_line('192.168.1.1', 8728)
    assert result == ('192.168.1.1', 8728, None, None, False)


def test_ip_with_port():
    result = parse_host_line('192.168.1.2:8729', 8728)
    assert result == ('192.168.1.2', 8729, None, None, False)


def test_ip_with_custom_credentials():
    result = parse_host_line('192.168.1.3|customuser|custompass', 8728)
    assert result == ('192.168.1.3', 8728, 'customuser', 'custompass', False)


def test_ip_port_and_credentials():
    result = parse_host_line('192.168.1.4:8729|customuser2|custompass2', 8728)
    assert result == ('192.168.1.4', 8729, 'customuser2', 'custompass2', False)


def test_ip_ssl_flag():
    result = parse_host_line('192.168.1.5|SSL', 8728)
    assert result == ('192.168.1.5', 8729, None, None, True)


def test_ip_port_ssl_flag():
    result = parse_host_line('192.168.1.6:8730|SSL', 8728)
    assert result == ('192.168.1.6', 8730, None, None, True)


def test_ip_credentials_and_ssl():
    result = parse_host_line('192.168.1.7|admin|password123|SSL', 8728)
    assert result == ('192.168.1.7', 8729, 'admin', 'password123', True)


def test_ip_port_credentials_and_ssl():
    result = parse_host_line('192.168.1.8:8730|admin|password123|SSL', 8728)
    assert result == ('192.168.1.8', 8730, 'admin', 'password123', True)


def test_default_ssl_port():
    result = parse_host_line('10.0.0.1', 8728, default_ssl_port=8729)
    assert result == ('10.0.0.1', 8728, None, None, False)


def test_ssl_flag_uses_ssl_port():
    result = parse_host_line('10.0.0.1|SSL', 8728, default_ssl_port=8729)
    assert result == ('10.0.0.1', 8729, None, None, True)


def test_negative_port():
    result = parse_host_line('10.0.0.1:-1', 8728)
    assert result is None


def test_zero_port():
    result = parse_host_line('10.0.0.1:0', 8728)
    assert result is None


def test_port_too_high():
    result = parse_host_line('10.0.0.1:65536', 8728)
    assert result is None


def test_empty_ip():
    result = parse_host_line(':8728', 8728)
    assert result is None


def test_malformed_line_random():
    result = parse_host_line('not-a-valid-line-!!!', 8728)
    assert result is not None
    assert result[0] == 'not-a-valid-line-!!!'


def test_empty_line_after_strip():
    result = parse_host_line('   ', 8728)
    assert result is None


def test_ipv6_not_supported():
    result = parse_host_line('::1', 8728)
    assert result is None


def test_ssl_flag_case_insensitive():
    result = parse_host_line('192.168.1.1|ssl', 8728)
    assert result == ('192.168.1.1', 8729, None, None, True)

    result = parse_host_line('192.168.1.1|Ssl', 8728)
    assert result == ('192.168.1.1', 8729, None, None, True)


def test_credentials_only():
    result = parse_host_line('192.168.1.1|user', 8728)
    assert result == ('192.168.1.1', 8728, 'user', None, False)


def test_password_with_pipe():
    result = parse_host_line('192.168.1.1|user|pass|word', 8728)
    assert result is not None
    assert result[2] == 'user'
    assert result[3] == 'pass'
