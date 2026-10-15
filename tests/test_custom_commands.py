import argparse
import logging
import os
import tempfile

import pytest

logging.disable(logging.CRITICAL)

from mkmassupdate import MassUpdater


def _load(content: str) -> list:
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8')
    tmp.write(content)
    tmp.close()
    try:
        args = argparse.Namespace(custom_commands=tmp.name)
        return MassUpdater(args)._load_custom_commands()
    finally:
        os.unlink(tmp.name)


def test_plain_string_entries_are_supported():
    result = _load("- /system/clock/print\n- /system/identity/print\n")
    assert result == ['/system/clock/print', '/system/identity/print']


def test_object_entries_without_params():
    result = _load("- command: /system/clock/print\n")
    assert result == ['/system/clock/print']


def test_object_entries_with_params():
    result = _load("- command: /user/add\n  params:\n    name: newuser\n")
    assert result == [('/user/add', {'name': 'newuser'})]


def test_mixed_entries_keep_order():
    content = (
        "- /system/clock/print\n"
        "- command: /user/add\n"
        "  params:\n"
        "    name: newuser\n"
        "- /system/resource/print\n"
    )
    assert _load(content) == [
        '/system/clock/print',
        ('/user/add', {'name': 'newuser'}),
        '/system/resource/print',
    ]


def test_malformed_entries_are_skipped():
    content = "- 42\n- command: /system/clock/print\n- not_a_mapping: true\n"
    assert _load(content) == ['/system/clock/print']


def test_non_list_document_returns_empty():
    assert _load("command: /system/clock/print\n") == []


def test_empty_document_returns_empty():
    assert _load("") == []
    assert _load("---\n") == []


def test_missing_file_returns_empty(monkeypatch):
    args = argparse.Namespace(custom_commands='/nonexistent/commands.yaml')
    assert MassUpdater(args)._load_custom_commands() == []


def test_invalid_yaml_returns_empty():
    assert _load("command: [unclosed\n") == []


def test_no_custom_commands_argument_returns_empty():
    assert MassUpdater(argparse.Namespace(custom_commands=None))._load_custom_commands() == []
