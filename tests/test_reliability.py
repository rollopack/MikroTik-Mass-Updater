import argparse
import logging

import librouteros

from mkmassupdate import (
    MassUpdater,
    _perform_cloud_backup,
    _execute_router_command,
    _format_elapsed_time,
    VERSION,
)


logging.disable(logging.CRITICAL)


class FakeApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, command, **params):
        self.calls.append((command, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _trap(message):
    return librouteros.exceptions.TrapError(message)


def test_command_retry_messages_stay_in_host_entry():
    api = FakeApi([TimeoutError("read timed out"), [{"name": "router"}]])
    entry_lines = ["\nHost: 192.0.2.10\n"]

    response = _execute_router_command(api, "/system/identity/print", entry_lines)

    assert response == [{"name": "router"}]
    assert any("Retry 1 failed" in line for line in entry_lines)
    assert all("192.0.2.10" not in line or line == entry_lines[0] for line in entry_lines)


def test_cloud_backup_uploads_before_removing_old_backups(monkeypatch):
    api = FakeApi([
        [{".id": "*1"}],
        [],
        [{".id": "*2", "secret-download-key": "key"}],
    ])
    entry_lines = []
    monkeypatch.setattr("mkmassupdate.time.sleep", lambda _: None)

    assert _perform_cloud_backup(api, "cloud-password", entry_lines)

    commands = [command for command, _ in api.calls]
    assert commands == [
        "/system/backup/cloud/print",
        "/system/backup/cloud/upload-file",
        "/system/backup/cloud/print",
        "/system/backup/cloud/remove-file",
    ]
    assert api.calls[-1][1] == {"number": "*1"}


def test_cloud_backup_keeps_existing_backup_when_upload_fails(monkeypatch):
    api = FakeApi([
        [{".id": "*1"}],
        _trap("upload failed"),
    ])
    entry_lines = []
    monkeypatch.setattr("mkmassupdate.time.sleep", lambda _: None)

    assert not _perform_cloud_backup(api, "cloud-password", entry_lines)
    assert [command for command, _ in api.calls] == [
        "/system/backup/cloud/print",
        "/system/backup/cloud/upload-file",
    ]


def test_cloud_backup_replaces_full_slot_without_deleting_backup(monkeypatch):
    api = FakeApi([
        [
            {".id": "*1", "name": "cloud-old-1"},
            {".id": "*2", "name": "cloud-old-2"},
        ],
        _trap("failure: Server error: All slots used. Delete file to free up space."),
        [],
        [{".id": "*3", "secret-download-key": "key"}],
    ])
    entry_lines = []
    monkeypatch.setattr("mkmassupdate.time.sleep", lambda _: None)

    assert _perform_cloud_backup(api, "cloud-password", entry_lines)

    commands = [command for command, _ in api.calls]
    assert commands == [
        "/system/backup/cloud/print",
        "/system/backup/cloud/upload-file",
        "/system/backup/cloud/upload-file",
        "/system/backup/cloud/print",
    ]
    assert api.calls[2][1] == {
        "action": "create-and-upload",
        "password": "cloud-password",
        "replace": "cloud-old-1",
    }
    assert not any("All slots used" in line for line in entry_lines)
    assert not any("replacing backup" in line for line in entry_lines)
    assert any("Successfully created and uploaded" in line for line in entry_lines)


def test_cloud_backup_fails_when_full_slot_replacement_fails(monkeypatch):
    api = FakeApi([
        [{".id": "*1", "name": "cloud-old-1"}],
        _trap("failure: Server error: All slots used. Delete file to free up space."),
        _trap("replacement failed"),
    ])
    entry_lines = []
    monkeypatch.setattr("mkmassupdate.time.sleep", lambda _: None)

    assert not _perform_cloud_backup(api, "cloud-password", entry_lines)
    commands = [command for command, _ in api.calls]
    assert commands[:2] == [
        "/system/backup/cloud/print",
        "/system/backup/cloud/upload-file",
    ]
    assert commands[2:] == ["/system/backup/cloud/upload-file"]


def test_cloud_backup_does_not_send_invalid_replace_without_backup_name(monkeypatch):
    api = FakeApi([
        [{".id": "*1"}],
        _trap("failure: Server error: All slots used. Delete file to free up space."),
    ])
    entry_lines = []
    monkeypatch.setattr("mkmassupdate.time.sleep", lambda _: None)

    assert not _perform_cloud_backup(api, "cloud-password", entry_lines)
    assert [command for command, _ in api.calls] == [
        "/system/backup/cloud/print",
        "/system/backup/cloud/upload-file",
    ]
    assert any("no backup name" in line.lower() for line in entry_lines)


def test_run_does_not_return_from_finally(monkeypatch):
    args = argparse.Namespace(
        username="admin", password="test", threads=1, timeout=5,
        ip_list="list.txt", port=8728, update_check_attempts=1,
        update_check_delay=0.01, no_colors=True, dry_run=True,
        start_line=1, debug=False, cloud_password=None,
        upgrade_firmware=False, ssl=False, custom_commands=None,
    )
    updater = MassUpdater(args)
    monkeypatch.setattr(updater, "_load_ip_list", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    try:
        updater.run()
    except RuntimeError as error:
        assert str(error) == "boom"
    else:
        raise AssertionError("run() suppressed the processing exception")


def test_version_is_shared_by_cli_and_module():
    assert len(VERSION.split(".")) == 3


def test_format_elapsed_time_keeps_seconds_for_short_jobs():
    assert _format_elapsed_time(12.34) == "12.3s"


def test_format_elapsed_time_formats_minutes_and_hours():
    assert _format_elapsed_time(1384.9) == "23m 04.9s"
    assert _format_elapsed_time(3723.4) == "1h 02m 03.4s"
