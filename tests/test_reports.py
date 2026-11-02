import json

import csv

import pytest

import logging
logging.disable(logging.CRITICAL)

from mkreport import (
    CSV_ENCODING,
    DASH,
    MASK,
    HostResult,
    Summary,
    build_summary,
    failed_ips,
    format_elapsed,
    latest_state_path,
    mask_secrets,
    parse_identity_entry,
    parse_resource_entry,
    parse_routerboard_entry,
    parse_update_entry,
    read_state,
    render_csv,
    render_html,
    render_state,
    state_files,
    write_reports,
)


SECRET = "sup3r-secret-download-key"


def _result(**overrides) -> HostResult:
    defaults = dict(
        ip="192.0.2.10",
        port=8728,
        identity="edge-1",
        model="RB750Gr3",
        serial_number="HEX123",
        routeros_version="7.14.3",
        architecture="arm",
        uptime="3d",
        firmware_current="7.14.3",
        firmware_upgrade="7.15.1",
        installed_version="7.14.3",
        latest_version="7.15.1",
        update_available=True,
        cloud_backup_key=SECRET,
        backup_ok=True,
        status="ok",
        success=True,
        duration_s=12.34,
    )
    defaults.update(overrides)
    return HostResult(**defaults)


class TestParsers:
    def test_identity(self):
        assert parse_identity_entry({"name": "edge-1"}) == {"identity": "edge-1"}

    def test_identity_missing(self):
        assert parse_identity_entry({}) == {"identity": None}

    def test_routerboard_uses_board_name(self):
        data = parse_routerboard_entry(
            {
                "board-name": "RB750Gr3",
                "model": "CCR1036",
                "serial-number": "HEX123",
                "current-firmware": "7.14.3",
                "upgrade-firmware": "7.15.1",
            }
        )
        assert data["model"] == "RB750Gr3"
        assert data["serial_number"] == "HEX123"
        assert data["firmware_current"] == "7.14.3"
        assert data["firmware_upgrade"] == "7.15.1"

    def test_routerboard_falls_back_to_model(self):
        assert parse_routerboard_entry({"model": "CCR1036"})["model"] == "CCR1036"

    def test_routerboard_missing_fields(self):
        data = parse_routerboard_entry({})
        assert data["model"] is None
        assert data["serial_number"] is None
        assert data["firmware_current"] is None

    def test_resource_stable_suffix(self):
        data = parse_resource_entry(
            {"version": "7.14.3", "build-time": "stable", "architecture-name": "arm"}
        )
        assert data["routeros_version"] == "7.14.3 (stable)"
        assert data["architecture"] == "arm"

    def test_resource_without_stable(self):
        assert parse_resource_entry({"version": "7.15beta2"})["routeros_version"] == "7.15beta2"

    def test_resource_missing(self):
        data = parse_resource_entry({})
        assert data["routeros_version"] is None
        assert data["architecture"] is None
        assert data["uptime"] is None

    def test_update_available(self):
        data = parse_update_entry(
            {"installed-version": "7.14.3", "latest-version": "7.15.1"}
        )
        assert data["update_available"] is True
        assert data["installed_version"] == "7.14.3"
        assert data["latest_version"] == "7.15.1"

    def test_update_not_available(self):
        data = parse_update_entry(
            {"installed-version": "7.15.1", "latest-version": "7.15.1"}
        )
        assert data["update_available"] is False

    def test_update_missing_latest(self):
        assert parse_update_entry({"installed-version": "7.15.1"})["update_available"] is None


class TestMaskSecrets:
    def test_masks_download_key(self):
        masked = mask_secrets(f"  Cloud backup: Secret Download Key: {SECRET}\n")
        assert SECRET not in masked
        assert MASK in masked

    def test_masks_password_parameter(self):
        masked = mask_secrets("  Error executing command ('/user/add', {'password': 'hunter2'})")
        assert "hunter2" not in masked

    def test_masks_generic_key_value(self):
        masked = mask_secrets("secret: topvalue")
        assert "topvalue" not in masked

    def test_keeps_regular_text(self):
        assert mask_secrets("  Model: RB750Gr3\n") == "  Model: RB750Gr3\n"

    def test_empty(self):
        assert mask_secrets("") == ""


class TestFormatElapsed:
    def test_seconds(self):
        assert format_elapsed(12.34) == "12.3s"

    def test_minutes(self):
        assert format_elapsed(1384.9) == "23m 04.9s"

    def test_hours(self):
        assert format_elapsed(3723.4) == "1h 02m 03.4s"


class TestSummary:
    def test_counts(self):
        results = [_result(), _result(ip="192.0.2.11", success=False, status="failed")]
        summary = build_summary(results, 30.0, dry_run=True, inventory=False, script_version="5.4.0")
        assert (summary.total, summary.ok, summary.failed) == (2, 1, 1)
        assert summary.elapsed == "30.0s"

    def test_empty(self):
        summary = build_summary([], 0.0)
        assert summary.total == 0
        assert summary.failed == 0


class TestCsv:
    def test_header_and_row(self, tmp_path):
        path = render_csv([_result()], tmp_path / "report.csv")
        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "CSV must carry a UTF-8 BOM"
        text = raw.decode(CSV_ENCODING)
        lines = text.strip().split("\r\n")
        assert lines[0].startswith("ip,port,ssl,identity")
        assert len(lines) == 2
        assert "192.0.2.10" in lines[1]
        assert "RB750Gr3" in lines[1]

    def test_masks_cloud_key(self, tmp_path):
        path = render_csv([_result()], tmp_path / "report.csv")
        text = path.read_text(encoding=CSV_ENCODING)
        assert SECRET not in text
        assert MASK in text

    def test_includes_backup_verification_status(self, tmp_path):
        result = _result(backup_ok=True, backup_verified=False)
        path = render_csv([result], tmp_path / "report.csv")
        rows = list(csv.reader(path.read_text(encoding=CSV_ENCODING).splitlines()))
        row = dict(zip(rows[0], rows[1]))
        assert row["backup_ok"] == "true"
        assert row["backup_verified"] == "false"

    def test_booleans_are_explicit(self, tmp_path):
        path = render_csv([_result()], tmp_path / "report.csv")
        rows = list(csv.reader(path.read_text(encoding=CSV_ENCODING).splitlines()))
        row = dict(zip(rows[0], rows[1]))
        assert row["ssl"] == "false"
        assert row["success"] == "true"
        assert row["update_available"] == "true"

    def test_empty_results_keeps_header(self, tmp_path):
        path = render_csv([], tmp_path / "report.csv")
        lines = path.read_text(encoding=CSV_ENCODING).strip().splitlines()
        assert len(lines) == 1


class TestHtml:
    def test_is_self_contained(self, tmp_path):
        text = render_html([_result()], build_summary([_result()], 12.3), "run-1", tmp_path / "r.html").read_text()
        assert "<!DOCTYPE html>" in text
        assert "Content-Security-Policy" in text
        assert "http://" not in text and "https://" not in text

    def test_escapes_device_text(self, tmp_path):
        result = _result(identity="<script>alert(1)</script>", model='RB"750')
        text = render_html([result], build_summary([result], 1.0), "run-1", tmp_path / "r.html").read_text()
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
        assert "RB&quot;750" in text

    def test_includes_every_host_log(self, tmp_path):
        failures = _result(ip="192.0.2.11", success=False, status="failed")
        successes = _result(ip="192.0.2.12")
        failures.log_lines = ["  Error: connection refused\n"]
        successes.log_lines = ["  Identity: edge-12\n"]
        text = render_html(
            [failures, successes], build_summary([failures, successes], 1.0), "run-1", tmp_path / "r.html"
        ).read_text()
        assert "connection refused" in text
        assert "Identity: edge-12" in text
        assert text.count('<details class="host"') == 2

    def test_details_are_collapsed_inside_table_after_each_host(self, tmp_path):
        first = _result(ip="192.0.2.10")
        second = _result(ip="192.0.2.11")
        text = render_html(
            [first, second], build_summary([first, second], 1.0), "run-1", tmp_path / "r.html"
        ).read_text()
        assert text.count('<table id="hosts">') == 1
        assert "<h2>Dettaglio per host</h2>" not in text
        assert text.index("192.0.2.10") < text.index('<details class="host" id="detail-0"')
        assert text.index('<details class="host" id="detail-0"') < text.index("192.0.2.11")
        assert "<details class=\"host\"" in text
        assert " open" not in text
        assert "detail-row-' + row.getAttribute('data-row')" in text

    def test_masks_secrets_in_logs(self, tmp_path):
        result = _result()
        result.log_lines = [f"  Cloud backup: Secret Download Key: {SECRET}\n"]
        text = render_html([result], build_summary([result], 1.0), "run-1", tmp_path / "r.html").read_text()
        assert SECRET not in text
        assert MASK in text

    def test_shows_summary_and_failures(self, tmp_path):
        failed = _result(ip="192.0.2.11", success=False, error="timeout")
        text = render_html(
            [failed], build_summary([failed], 5.0, inventory=True), "run-1", tmp_path / "r.html"
        ).read_text()
        assert "timeout" in text
        assert "Inventario" in text
        assert "Falliti" in text

    def test_shows_accepted_but_unverified_backup(self, tmp_path):
        result = _result(backup_ok=True, backup_verified=False)
        text = render_html([result], build_summary([result], 1.0), "run-1", tmp_path / "r.html").read_text()
        assert "caricato, non verificato" in text

    def test_missing_values_rendered_as_dash(self, tmp_path):
        result = _result(serial_number=None, firmware_current=None, firmware_upgrade=None)
        text = render_html([result], build_summary([result], 1.0), "run-1", tmp_path / "r.html").read_text()
        assert DASH in text

    def test_has_filter_and_sort_controls(self, tmp_path):
        text = render_html([_result()], build_summary([_result()], 1.0), "run-1", tmp_path / "r.html").read_text()
        assert 'id="filter"' in text
        assert 'aria-sort' not in text or "aria-sort" in text
        assert "addEventListener" in text


class TestState:
    def test_state_contains_no_credentials_or_keys(self, tmp_path):
        path = render_state([_result()], build_summary([_result()], 1.0), "run-1", tmp_path / "state.json")
        text = path.read_text(encoding="utf-8")
        assert SECRET not in text
        assert "password" not in text.lower()
        assert "hunter2" not in text

    def test_state_host_keys(self, tmp_path):
        result = _result(update_available=False)
        path = render_state([result], build_summary([result], 1.0), "run-1", tmp_path / "state.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_id"] == "run-1"
        assert isinstance(data["summary"], dict)
        assert data["hosts"][0]["update_available"] is False
        assert set(data["hosts"][0]) == {
            "ip", "port", "ssl", "status", "success", "error", "duration_s",
            "update_available",
        }

    def test_failed_ips(self, tmp_path):
        results = [
            _result(ip="192.0.2.1"),
            _result(ip="192.0.2.2", success=False, status="failed", update_available=None),
            _result(ip="192.0.2.3", success=False, status="failed", update_available=True),
            _result(ip="192.0.2.4", success=False, status="failed", update_available=False),
        ]
        path = render_state(results, build_summary(results, 1.0), "run-1", tmp_path / "state.json")
        assert failed_ips(read_state(path)) == {"192.0.2.2", "192.0.2.3"}

    def test_failed_ips_ignores_malformed(self):
        assert failed_ips({"hosts": "nope"}) == set()
        assert failed_ips({"hosts": [{"success": False}, "x"]}) == set()

    def test_old_state_without_update_status_remains_retryable(self):
        assert failed_ips(
            {"hosts": [{"ip": "192.0.2.9", "success": False}]}
        ) == {"192.0.2.9"}


class TestStateDiscovery:
    def _make_run(self, base, run_id, results):
        run_dir = base / run_id
        run_dir.mkdir(parents=True)
        render_state(results, build_summary(results, 1.0), run_id, run_dir / "state.json")

    def test_latest_is_newest_by_name(self, tmp_path):
        self._make_run(tmp_path, "2026-01-01-10-00-00", [_result()])
        self._make_run(tmp_path, "2026-02-01-10-00-00", [_result()])
        latest = latest_state_path(tmp_path)
        assert latest is not None
        assert latest.parent.name == "2026-02-01-10-00-00"

    def test_state_files_sorted(self, tmp_path):
        self._make_run(tmp_path, "2026-01-01-10-00-00", [_result()])
        self._make_run(tmp_path, "2026-01-01-10-00-00-2", [_result()])
        names = [path.parent.name for path in state_files(tmp_path)]
        assert names == ["2026-01-01-10-00-00", "2026-01-01-10-00-00-2"]

    def test_numeric_collision_suffix_order(self, tmp_path):
        for suffix in (1, 2, 9, 10, 11):
            run_id = "2026-01-01-10-00-00" if suffix == 1 else f"2026-01-01-10-00-00-{suffix}"
            self._make_run(tmp_path, run_id, [_result()])
        names = [path.parent.name for path in state_files(tmp_path)]
        assert names[-1] == "2026-01-01-10-00-00-11"
        assert latest_state_path(tmp_path).parent.name == "2026-01-01-10-00-00-11"

    def test_missing_dir(self, tmp_path):
        assert latest_state_path(tmp_path / "missing") is None
        assert state_files(tmp_path / "missing") == []


class TestWriteReports:
    def test_writes_three_files(self, tmp_path):
        results = [_result()]
        summary = build_summary(results, 1.0, script_version="5.4.0")
        paths = write_reports(tmp_path / "run", results, summary, "run")
        assert set(paths) == {"html", "csv", "state"}
        for path in paths.values():
            assert path.is_file()
        assert paths["html"].name == "report.html"
        assert paths["csv"].name == "report.csv"
        assert paths["state"].name == "state.json"

    def test_state_is_readable(self, tmp_path):
        results = [_result(success=False, status="failed", ip="192.0.2.9")]
        summary = build_summary(results, 1.0)
        paths = write_reports(tmp_path / "run", results, summary, "run")
        assert failed_ips(read_state(paths["state"])) == {"192.0.2.9"}
