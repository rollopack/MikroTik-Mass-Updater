import pytest
import sys
sys.path.insert(0, '/mnt/dropbox/Documenti/Mikrotik/MikroTik-Mass-Updater')

import logging
logging.disable(logging.CRITICAL)

from mkmassupdate import _process_identity, _process_routerboard, _process_resource


def test_process_identity():
    response = [{'name': 'MyRouter'}]
    entry_lines = []
    _process_identity(response, entry_lines)
    assert 'MyRouter' in entry_lines[0]
    assert 'Identity:' in entry_lines[0]


def test_process_identity_default_name():
    response = [{'not-name': 'value'}]
    entry_lines = []
    _process_identity(response, entry_lines)
    assert 'N/A' in entry_lines[0]


def test_process_identity_empty_response():
    response = []
    entry_lines = []
    _process_identity(response, entry_lines)
    assert entry_lines == []


def test_process_identity_none():
    _process_identity(None, [])
    pass


def test_process_routerboard():
    response = [{'board-name': 'RB750Gr3'}]
    entry_lines = []
    _process_routerboard(response, entry_lines)
    assert 'RB750Gr3' in entry_lines[0]
    assert 'Model:' in entry_lines[0]


def test_process_routerboard_fallback_model():
    response = [{'model': 'CCR1036'}]
    entry_lines = []
    _process_routerboard(response, entry_lines)
    assert 'CCR1036' in entry_lines[0]


def test_process_routerboard_default():
    response = [{'other': 'val'}]
    entry_lines = []
    _process_routerboard(response, entry_lines)
    assert 'N/A' in entry_lines[0]


def test_process_routerboard_empty():
    response = []
    entry_lines = []
    _process_routerboard(response, entry_lines)
    assert entry_lines == []


def test_process_resource():
    response = [{'version': '7.14.3', 'build-time': 'stable'}]
    entry_lines = []
    _process_resource(response, entry_lines)
    assert '7.14.3 (stable)' in entry_lines[0]
    assert 'Version:' in entry_lines[0]


def test_process_resource_no_stable():
    response = [{'version': '7.15beta2'}]
    entry_lines = []
    _process_resource(response, entry_lines)
    assert '7.15beta2' in entry_lines[0]
    assert '(stable)' not in entry_lines[0]


def test_process_resource_default():
    response = [{'other': 'val'}]
    entry_lines = []
    _process_resource(response, entry_lines)
    assert 'N/A' in entry_lines[0]


def test_process_resource_empty():
    response = []
    entry_lines = []
    _process_resource(response, entry_lines)
    assert entry_lines == []
