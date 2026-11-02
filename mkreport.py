"""Structured results and HTML/CSV/JSON reports for MikroTik Mass Updater.

This module holds the data model that the CLI and the report renderers share.
It has no dependency on ``mkmassupdate`` so it can be tested in isolation.
"""

from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

UNKNOWN = "N/A"
DASH = "\u2014"
MASK = "********"

CSV_DELIMITER = ","
CSV_ENCODING = "utf-8-sig"

SECRET_DOWNLOAD_KEY = re.compile(
    r"(?i)(secret[-_ ]download[-_ ]key\s*[=:]\s*)(\S+)"
)
SECRET_KEY_VALUE = re.compile(
    r"""(?ix)
    (["']?\b(?:pass(?:word|phrase)?|pwd|secret)\b["']?\s*[=:]\s*)
    ("[^"]*"|'[^']*'|\S+)
    """
)


def mask_secrets(text: str) -> str:
    """Mask credentials and download keys before they reach a report."""
    if not text:
        return text
    masked = SECRET_DOWNLOAD_KEY.sub(lambda m: m.group(1) + MASK, text)
    return SECRET_KEY_VALUE.sub(lambda m: m.group(1) + MASK, masked)


def format_elapsed(seconds: float) -> str:
    total_seconds = max(0.0, seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)

    if hours >= 1:
        return f"{int(hours)}h {int(minutes):02d}m {remaining_seconds:04.1f}s"
    if minutes >= 1:
        return f"{int(minutes)}m {remaining_seconds:04.1f}s"
    return f"{remaining_seconds:.1f}s"


# ---------------------------------------------------------------------------
# RouterOS response parsing (pure functions, easy to test)
# ---------------------------------------------------------------------------

def parse_identity_entry(response_item: dict[str, Any]) -> dict[str, Any]:
    return {"identity": response_item.get("name") or None}


def parse_routerboard_entry(response_item: dict[str, Any]) -> dict[str, Any]:
    model = response_item.get("board-name") or response_item.get("model") or None
    return {
        "model": model,
        "serial_number": response_item.get("serial-number") or None,
        "firmware_current": response_item.get("current-firmware") or None,
        "firmware_upgrade": response_item.get("upgrade-firmware") or None,
    }


def parse_resource_entry(response_item: dict[str, Any]) -> dict[str, Any]:
    version = response_item.get("version") or None
    build_time = response_item.get("build-time") or ""
    if version and "stable" in build_time:
        version = f"{version} (stable)"
    return {
        "routeros_version": version,
        "architecture": response_item.get("architecture-name") or None,
        "uptime": response_item.get("uptime") or None,
    }


def parse_update_entry(response_item: dict[str, Any]) -> dict[str, Any]:
    installed = response_item.get("installed-version") or None
    latest = response_item.get("latest-version") or None
    available = None if not (latest and installed) else latest != installed
    return {
        "installed_version": installed,
        "latest_version": latest,
        "update_available": available,
        "status": response_item.get("status") or None,
    }


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class HostResult:
    """Everything collected for one device. Never holds credentials."""

    ip: str
    port: int
    ssl: bool = False
    identity: str | None = None
    model: str | None = None
    serial_number: str | None = None
    routeros_version: str | None = None
    architecture: str | None = None
    uptime: str | None = None
    firmware_current: str | None = None
    firmware_upgrade: str | None = None
    installed_version: str | None = None
    latest_version: str | None = None
    update_available: bool | None = None
    cloud_backup_key: str | None = None
    backup_ok: bool | None = None
    backup_verified: bool | None = None
    status: str = "pending"
    success: bool = False
    error: str | None = None
    duration_s: float | None = None
    log_lines: list[str] = field(default_factory=list)

    def apply(self, data: dict[str, Any]) -> None:
        """Copy non-empty parsed values onto the result."""
        for key, value in data.items():
            if value is None:
                continue
            if hasattr(self, key):
                setattr(self, key, value)


@dataclass
class Summary:
    total: int
    ok: int
    failed: int
    elapsed_s: float
    dry_run: bool = False
    inventory: bool = False
    script_version: str = ""

    @property
    def elapsed(self) -> str:
        return format_elapsed(self.elapsed_s)


def build_summary(
    results: Sequence[HostResult],
    elapsed_s: float,
    dry_run: bool = False,
    inventory: bool = False,
    script_version: str = "",
) -> Summary:
    total = len(results)
    ok = sum(1 for result in results if result.success)
    return Summary(
        total=total,
        ok=ok,
        failed=total - ok,
        elapsed_s=elapsed_s,
        dry_run=dry_run,
        inventory=inventory,
        script_version=script_version,
    )


# ---------------------------------------------------------------------------
# CSV report
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "ip",
    "port",
    "ssl",
    "identity",
    "model",
    "serial_number",
    "routeros_version",
    "architecture",
    "uptime",
    "firmware_current",
    "firmware_upgrade",
    "installed_version",
    "latest_version",
    "update_available",
    "backup_ok",
    "backup_verified",
    "cloud_backup_key",
    "status",
    "success",
    "duration_s",
    "error",
]


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.1f}"
    return mask_secrets(str(value))


def _csv_row(result: HostResult) -> dict[str, str]:
    row = {column: _csv_value(getattr(result, column)) for column in CSV_COLUMNS}
    if result.cloud_backup_key:
        row["cloud_backup_key"] = MASK
    return row


def render_csv(results: Sequence[HostResult], path: Path) -> Path:
    path = Path(path)
    with path.open("w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=CSV_COLUMNS, delimiter=CSV_DELIMITER
        )
        writer.writeheader()
        for result in results:
            writer.writerow(_csv_row(result))
    return path


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1d2430;
  --muted: #5d6b7c;
  --border: #d7dde5;
  --ok: #1a7f37;
  --fail: #b42318;
  --warn: #9a6700;
  --accent: #0b5cad;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14181e;
    --panel: #1c222a;
    --text: #e7edf5;
    --muted: #9fb0c3;
    --border: #2c3540;
    --ok: #4ac26b;
    --fail: #ff7b72;
    --warn: #e3b341;
    --accent: #6cb6ff;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 1.5rem;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header, section { max-width: 1400px; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 1.75rem 0 .75rem; }
.sub { color: var(--muted); margin: 0 0 1.25rem; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 1rem; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: .7rem 1rem; min-width: 130px;
}
.card .label { color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
.card .value { font-size: 1.3rem; font-variant-numeric: tabular-nums; }
.toolbar { display: flex; gap: .75rem; align-items: center; margin: 1rem 0; flex-wrap: wrap; }
input[type=search] {
  flex: 1 1 260px; max-width: 420px; padding: .5rem .7rem; border-radius: 6px;
  border: 1px solid var(--border); background: var(--panel); color: var(--text);
}
.hint { color: var(--muted); font-size: .85rem; }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; }
th, td { padding: .45rem .6rem; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
th { background: color-mix(in srgb, var(--panel) 80%, var(--border)); font-size: .8rem; }
thead th:first-child, .host-row td:first-child {
  position: sticky; left: 0; z-index: 1; background: var(--panel);
  border-right: 1px solid var(--border);
}
thead th:first-child { background: color-mix(in srgb, var(--panel) 80%, var(--border)); z-index: 2; }
th button {
  all: unset; cursor: pointer; font: inherit; font-weight: 600;
  display: inline-flex; gap: .25rem; align-items: center;
}
th button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
tbody tr:hover { background: color-mix(in srgb, var(--panel) 90%, var(--accent)); }
tbody tr:last-child td { border-bottom: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { font-weight: 600; }
.badge.ok { color: var(--ok); }
.badge.fail { color: var(--fail); }
.badge.warn { color: var(--warn); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem; }
details.host {
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: .5rem .8rem;
}
details.host[hidden] { display: none; }
tr.detail-row > td { white-space: normal; padding: .45rem .6rem; }
tr.detail-row[hidden] { display: none; }
summary { cursor: pointer; font-weight: 600; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem .9rem; margin: .8rem 0; }
dt { color: var(--muted); }
dd { margin: 0; word-break: break-word; }
pre {
  background: color-mix(in srgb, var(--panel) 85%, var(--border));
  border: 1px solid var(--border); border-radius: 6px;
  padding: .7rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem;
}
footer { max-width: 1400px; margin: 2rem auto 0; color: var(--muted); font-size: .8rem; }
@media (max-width: 640px) {
  body { padding: .75rem; }
  dl { grid-template-columns: 1fr; }
  dd { margin-bottom: .5rem; }
}
"""

_JS = """
(function () {
  var tbody = document.querySelector('#hosts tbody');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr.host-row'));
  var ascending = {};
  function cellText(row, index) {
    var cell = row.children[index];
    if (!cell) { return ''; }
    var sort = cell.getAttribute('data-sort');
    return (sort === null ? cell.textContent : sort).trim();
  }
  function asNumber(text) {
    var n = Number(String(text).replace(',', '.'));
    return isNaN(n) ? null : n;
  }
  document.querySelectorAll('#hosts th button').forEach(function (button) {
    button.addEventListener('click', function () {
      var index = Number(button.parentNode.getAttribute('data-col'));
      var direction = ascending[index] ? -1 : 1;
      ascending[index] = !ascending[index];
      rows.sort(function (a, b) {
        var x = cellText(a, index), y = cellText(b, index);
        var nx = asNumber(x), ny = asNumber(y);
        if (nx !== null && ny !== null) { return (nx - ny) * direction; }
        return x.localeCompare(y, undefined, { numeric: true }) * direction;
      });
      rows.forEach(function (row) {
        tbody.appendChild(row);
        var detail = document.getElementById('detail-row-' + row.getAttribute('data-row'));
        if (detail) { tbody.appendChild(detail); }
      });
      document.querySelectorAll('#hosts th').forEach(function (th) {
        th.removeAttribute('aria-sort');
      });
      button.parentNode.setAttribute(
        'aria-sort', direction === 1 ? 'ascending' : 'descending'
      );
    });
  });
  var input = document.getElementById('filter');
  var count = document.getElementById('count');
  function apply() {
    var query = (input.value || '').toLowerCase();
    var visible = 0;
    rows.forEach(function (row) {
      var show = row.textContent.toLowerCase().indexOf(query) !== -1;
      row.hidden = !show;
      if (show) { visible += 1; }
      var detail = document.getElementById('detail-row-' + row.getAttribute('data-row'));
      if (detail) { detail.hidden = !show; }
    });
    if (count) { count.textContent = String(visible); }
  }
  input.addEventListener('input', apply);
  apply();
})();
"""


def _esc(value: Any) -> str:
    if value is None or value == "":
        return DASH
    return html.escape(str(value), quote=True)


def _plain(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _firmware_text(result: HostResult) -> str:
    current = result.firmware_current
    upgrade = result.firmware_upgrade
    if current and upgrade and current != upgrade:
        return f"{current} \u2192 {upgrade}"
    return current or upgrade or DASH


def _update_text(result: HostResult) -> str:
    if result.update_available and result.latest_version:
        return result.latest_version
    return DASH


def _backup_text(result: HostResult) -> str:
    if result.backup_ok is None:
        return DASH
    if result.backup_ok and result.backup_verified is False:
        return "caricato, non verificato"
    return "ok" if result.backup_ok else "fallito"


def _status_badge(result: HostResult) -> str:
    if result.success:
        return '<span class="badge ok">ok</span>'
    return '<span class="badge fail">fallito</span>'


def _duration_text(result: HostResult) -> str:
    if result.duration_s is None:
        return DASH
    return format_elapsed(result.duration_s)


def _detail_block(result: HostResult, index: int) -> str:
    log_text = mask_secrets("".join(result.log_lines)).strip()
    fields = [
        ("Porta", _esc(result.port)),
        ("SSL", "s\u00ec" if result.ssl else "no"),
        ("Identity", _esc(result.identity)),
        ("Modello", _esc(result.model)),
        ("Serial", _esc(result.serial_number)),
        ("RouterOS", _esc(result.routeros_version)),
        ("Architettura", _esc(result.architecture)),
        ("Uptime", _esc(result.uptime)),
        ("Firmware attuale", _esc(result.firmware_current)),
        ("Firmware disponibile", _esc(result.firmware_upgrade)),
        ("Versione installata", _esc(result.installed_version)),
        ("Versione disponibile", _esc(result.latest_version)),
        ("Backup cloud", _esc(_backup_text(result))),
        ("Chiave backup", MASK if result.cloud_backup_key else DASH),
        ("Esito", "ok" if result.success else "fallito"),
        ("Durata", _esc(_duration_text(result))),
        ("Errore", _esc(result.error)),
    ]
    body = "".join(
        f"<dt>{html.escape(label)}</dt><dd>{value}</dd>" for label, value in fields
    )
    log_html = (
        f"<pre>{html.escape(log_text)}</pre>"
        if log_text
        else "<p class=\"hint\">Nessun output registrato.</p>"
    )
    return (
        f'<tr class="detail-row" id="detail-row-{index}" data-row="{index}">'
        f'<td colspan="13"><details class="host" id="detail-{index}" data-row="{index}">'
        f"<summary>{_esc(result.ip)} &mdash; {_esc(result.model)} &mdash; "
        f"{_esc(_duration_text(result))}</summary>"
        f"<dl>{body}</dl>{log_html}</details></td></tr>"
    )


def render_html(
    results: Sequence[HostResult],
    summary: Summary,
    run_id: str,
    path: Path,
) -> Path:
    path = Path(path)
    rows = []
    details = []
    for index, result in enumerate(results):
        rows.append(
            "<tr class=\"host-row\" data-row=\"{index}\">"
            "<td class=\"mono\">{ip}</td>"
            "<td class=\"num\" data-sort=\"{port}\">{port}</td>"
            "<td>{identity}</td>"
            "<td>{model}</td>"
            "<td class=\"mono\">{serial}</td>"
            "<td class=\"mono\">{version}</td>"
            "<td>{arch}</td>"
            "<td class=\"mono\">{firmware}</td>"
            "<td class=\"mono\">{update}</td>"
            "<td>{backup}</td>"
            "<td>{status}</td>"
            "<td class=\"num\" data-sort=\"{duration}\">{duration_text}</td>"
            "<td>{error}</td>"
            "</tr>".format(
                index=index,
                ip=_esc(result.ip),
                port=result.port,
                identity=_esc(result.identity),
                model=_esc(result.model),
                serial=_esc(result.serial_number),
                version=_esc(result.routeros_version),
                arch=_esc(result.architecture),
                firmware=_esc(_firmware_text(result)),
                update=_esc(_update_text(result)),
                backup=_esc(_backup_text(result)),
                status=_status_badge(result),
                duration="" if result.duration_s is None else f"{result.duration_s:.1f}",
                duration_text=_esc(_duration_text(result)),
                error=_esc(result.error),
            )
        )
        details.append(_detail_block(result, index))
    table_rows = []
    for row, detail in zip(rows, details):
        table_rows.extend((row, detail))

    mode = []
    if summary.inventory:
        mode.append("Inventario (sola lettura)")
    if summary.dry_run:
        mode.append("Dry run")
    mode_text = " \u00b7 ".join(mode) if mode else "Esecuzione completa"

    document = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>MikroTik Mass Updater \u2014 {html.escape(run_id)}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>MikroTik Mass Updater</h1>
  <p class="sub">Run <span class="mono">{html.escape(run_id)}</span> \u00b7 {html.escape(mode_text)} \u00b7 versione {html.escape(summary.script_version)}</p>
  <div class="cards">
    <div class="card"><div class="label">Host</div><div class="value">{summary.total}</div></div>
    <div class="card"><div class="label">Riusciti</div><div class="value">{summary.ok}</div></div>
    <div class="card"><div class="label">Falliti</div><div class="value">{summary.failed}</div></div>
    <div class="card"><div class="label">Durata</div><div class="value">{html.escape(summary.elapsed)}</div></div>
  </div>
</header>
<section>
  <h2>Host elaborati</h2>
  <div class="toolbar">
    <input type="search" id="filter" placeholder="Filtra per IP, identity, modello, serial, versione\u2026" aria-label="Filtra gli host">
    <span class="hint"><span id="count">{summary.total}</span> di {summary.total} host visibili</span>
  </div>
  <div class="table-wrap">
    <table id="hosts">
      <thead>
        <tr>
          <th data-col="0"><button type="button">IP</button></th>
          <th data-col="1" class="num"><button type="button">Porta</button></th>
          <th data-col="2"><button type="button">Identity</button></th>
          <th data-col="3"><button type="button">Modello</button></th>
          <th data-col="4"><button type="button">Serial</button></th>
          <th data-col="5"><button type="button">RouterOS</button></th>
          <th data-col="6"><button type="button">Arch</button></th>
          <th data-col="7"><button type="button">Firmware</button></th>
          <th data-col="8"><button type="button">Update</button></th>
          <th data-col="9"><button type="button">Backup</button></th>
          <th data-col="10"><button type="button">Esito</button></th>
          <th data-col="11" class="num"><button type="button">Durata</button></th>
          <th data-col="12"><button type="button">Errore</button></th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(table_rows)}
      </tbody>
    </table>
  </div>
</section>
<footer>
  Generato da MikroTik Mass Updater {html.escape(summary.script_version)} \u2014
  file autoconsistente, nessuna risorsa esterna. Le credenziali e le chiavi di
  download sono mascherate.
</footer>
<script>{_JS}</script>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Run state (used by --retry-failed)
# ---------------------------------------------------------------------------

STATE_FILENAME = "state.json"
HTML_FILENAME = "report.html"
CSV_FILENAME = "report.csv"


def _state_host(result: HostResult) -> dict[str, Any]:
    return {
        "ip": result.ip,
        "port": result.port,
        "ssl": result.ssl,
        "status": result.status,
        "success": result.success,
        "update_available": result.update_available,
        "error": result.error,
        "duration_s": (
            None if result.duration_s is None else round(result.duration_s, 1)
        ),
    }


def render_state(
    results: Sequence[HostResult],
    summary: Summary,
    run_id: str,
    path: Path,
) -> Path:
    path = Path(path)
    payload = {
        "run_id": run_id,
        "script_version": summary.script_version,
        "dry_run": summary.dry_run,
        "inventory": summary.inventory,
        "summary": {
            "total": summary.total,
            "ok": summary.ok,
            "failed": summary.failed,
            "elapsed_s": round(summary.elapsed_s, 1),
        },
        "hosts": [_state_host(result) for result in results],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def write_reports(
    run_dir: Path,
    results: Sequence[HostResult],
    summary: Summary,
    run_id: str,
) -> dict[str, Path]:
    """Write the three report artifacts and return their paths."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "html": render_html(results, summary, run_id, run_dir / HTML_FILENAME),
        "csv": render_csv(results, run_dir / CSV_FILENAME),
        "state": render_state(results, summary, run_id, run_dir / STATE_FILENAME),
    }


def read_state(path: Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"State file must contain an object, got {type(data).__name__}")
    return data


def failed_ips(state: dict[str, Any]) -> set[str]:
    hosts = state.get("hosts")
    if not isinstance(hosts, list):
        return set()
    found = set()
    for host in hosts:
        if not isinstance(host, dict):
            continue
        ip = host.get("ip")
        if (
            isinstance(ip, str)
            and ip
            and not host.get("success")
            and host.get("update_available") is not False
        ):
            found.add(ip)
    return found


def state_files(base_dir: Path) -> list[Path]:
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return []
    found = [entry / STATE_FILENAME for entry in base_dir.iterdir() if entry.is_dir()]
    return sorted(
        (path for path in found if path.is_file()),
        key=lambda path: _run_id_sort_key(path.parent.name),
    )


def _run_id_sort_key(run_id: str) -> tuple[str, int]:
    match = re.fullmatch(
        r"(?P<timestamp>\d{4}(?:-\d{2}){5})(?:-(?P<suffix>\d+))?", run_id
    )
    if match is None:
        return run_id, 1
    return match.group("timestamp"), int(match.group("suffix") or 1)


def latest_state_path(base_dir: Path) -> Path | None:
    candidates = state_files(base_dir)
    return candidates[-1] if candidates else None
