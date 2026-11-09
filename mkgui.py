#!/usr/bin/env python3
"""Tkinter desktop interface for MikroTik Mass Updater."""

from __future__ import annotations

import os
import math
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import mkmassupdate
from mkmassupdate import MassUpdater, UpdaterConfig
from mkgui_settings import SettingsError, load_settings, save_settings
from mkgui_theme import apply_theme


def build_updater_config(values: dict[str, Any]) -> UpdaterConfig:
    """Validate UI form values and map them to the CLI's runtime contract."""
    username = str(values.get("username", "")).strip()
    password = str(values.get("password", ""))
    if not username:
        raise ValueError("API username is required")
    if not password:
        raise ValueError("API password is required")

    def positive_int(name: str) -> int:
        try:
            value = int(values[name])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{name.replace('_', ' ').title()} must be a positive integer") from error
        if value < 1:
            raise ValueError(f"{name.replace('_', ' ').title()} must be a positive integer")
        return value

    def port_value(name: str) -> int:
        value = positive_int(name)
        if value > 65535:
            raise ValueError(f"{name.replace('_', ' ').title()} must be between 1 and 65535")
        return value

    try:
        update_check_delay = float(values.get("update_check_delay", "2.0"))
    except (TypeError, ValueError) as error:
        raise ValueError("Update check delay must be a positive number") from error
    if not math.isfinite(update_check_delay) or update_check_delay <= 0:
        raise ValueError("Update check delay must be a positive number")

    retry_value = values.get("retry_failed", "")
    if isinstance(retry_value, bool):
        retry_failed = "latest" if retry_value else None
    else:
        retry_failed = str(retry_value).strip() or None

    cloud_password = None
    if values.get("cloud_backup"):
        cloud_password = str(values.get("cloud_password", ""))
        if not cloud_password:
            raise ValueError("Cloud Backup password is required when Cloud Backup is enabled")

    ssl = bool(values.get("ssl", False))
    ssl_verify = bool(values.get("ssl_verify", True))
    ssl_ca_file = str(values.get("ssl_ca_file") or "").strip() or None
    ssl_legacy_ciphers = bool(values.get("ssl_legacy_ciphers", False))
    ssl_port = port_value("ssl_port")
    port = port_value("port")
    if ssl and port == 8728:
        port = ssl_port

    only = str(values.get("only", "")).strip() or None
    if only is not None and not any(item.strip() for item in only.split(',')):
        raise ValueError("Host selection must contain at least one host")

    return UpdaterConfig(
        username=username,
        password=password,
        threads=positive_int("threads"),
        timeout=positive_int("timeout"),
        ip_list=str(values.get("ip_list", "list.txt")).strip() or "list.txt",
        port=port,
        ssl_port=ssl_port,
        update_check_attempts=positive_int("update_check_attempts"),
        update_check_delay=update_check_delay,
        dry_run=bool(values.get("dry_run", False)),
        start_line=positive_int("start_line"),
        cloud_password=cloud_password,
        upgrade_firmware=bool(values.get("upgrade_firmware", False)),
        ssl=ssl,
        ssl_verify=ssl_verify,
        ssl_ca_file=ssl_ca_file,
        ssl_legacy_ciphers=ssl_legacy_ciphers,
        custom_commands=str(values.get("custom_commands", "")).strip() or None,
        inventory=bool(values.get("inventory", False)),
        only=only,
        retry_failed=retry_failed,
        report=bool(values.get("report", True)),
    )


class MikroTikUpdaterApp:
    COLUMNS = (
        ("ip", "Host", 145),
        ("identity", "Identity", 160),
        ("model", "Model", 155),
        ("routeros", "RouterOS", 130),
        ("firmware", "Firmware", 125),
        ("status", "Status", 100),
    )

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"MikroTik Mass Updater {mkmassupdate.VERSION}")
        initial_height = max(600, min(850, self.root.winfo_screenheight() - 80))
        self.root.geometry(f"1180x{initial_height}")
        self.root.minsize(960, 600)
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.updater: MassUpdater | None = None
        self.run_thread: threading.Thread | None = None
        self.run_dir: Path | None = None
        self.close_when_done = False
        self.hosts: dict[str, dict[str, Any]] = {}
        self.rows_by_ip: dict[str, list[str]] = {}
        self.active_rows: dict[str, str] = {}
        self.logs: dict[str, str] = {}
        self.pending_hosts: list[dict[str, Any]] = []
        self.pending_host_index = 0
        self._row_sequence = 0
        self.debug = tk.BooleanVar(value=False)

        self._make_variables()
        self._build_widgets()
        self._apply_theme()
        self.root.bind("<Configure>", self._resize_content)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after_idle(self._initialize_responsive_layout)
        self.root.after(100, self._poll_events)

    def _make_variables(self) -> None:
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.ip_list = tk.StringVar(value="list.txt")
        self.threads = tk.StringVar(value="5")
        self.timeout = tk.StringVar(value="5")
        self.port = tk.StringVar(value="8728")
        self.ssl_port = tk.StringVar(value="8729")
        self.update_check_attempts = tk.StringVar(value="15")
        self.update_check_delay = tk.StringVar(value="2.0")
        self.start_line = tk.StringVar(value="1")
        self.cloud_password = tk.StringVar()
        self.custom_commands = tk.StringVar()
        self.only = tk.StringVar()
        self.retry_failed = tk.StringVar()
        self.dry_run = tk.BooleanVar(value=False)
        self.inventory = tk.BooleanVar(value=False)
        self.upgrade_firmware = tk.BooleanVar(value=False)
        self.ssl = tk.BooleanVar(value=False)
        self.ssl_verify = tk.BooleanVar(value=True)
        self.ssl_ca_file = tk.StringVar()
        self.ssl_legacy_ciphers = tk.BooleanVar(value=False)
        self.cloud_backup = tk.BooleanVar(value=False)
        self.report = tk.BooleanVar(value=True)
        self.host_filter = tk.StringVar(value="All")
        self.theme = tk.StringVar(value="Light")

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 10))
        ttk.Label(
            heading, text="MikroTik Mass Updater", font=("TkDefaultFont", 16, "bold")
        ).pack(side="left")
        ttk.Label(heading, text=f"Version {mkmassupdate.VERSION}").pack(side="right")
        self.theme_selector = ttk.Combobox(
            heading,
            textvariable=self.theme,
            values=("Light", "Dark"),
            state="readonly",
            width=9,
        )
        self.theme_selector.pack(side="right", padx=(0, 6))
        ttk.Label(heading, text="Theme:").pack(side="right", padx=(12, 4))
        self.theme_selector.bind("<<ComboboxSelected>>", self._on_theme_selected)

        options = ttk.LabelFrame(outer, text="Connection and run options", padding=10)
        options.pack(fill="x", pady=(0, 10))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(4, weight=1)
        self._form_entry(options, 0, 0, "API username", self.username)
        self._form_entry(options, 0, 3, "API password", self.password, show="•")
        self._form_entry(options, 1, 0, "Host list", self.ip_list)
        ttk.Button(
            options, text="Browse…", command=self._choose_ip_list, style="Secondary.TButton"
        ).grid(
            row=1, column=2, padx=(6, 12), sticky="w"
        )
        self._form_entry(options, 1, 3, "Custom commands YAML", self.custom_commands)
        ttk.Button(
            options, text="Browse…", command=self._choose_commands, style="Secondary.TButton"
        ).grid(
            row=1, column=5, padx=(6, 0), sticky="w"
        )

        self._form_entry(options, 2, 0, "Workers", self.threads, width=10)
        self._form_entry(options, 2, 3, "Timeout (seconds)", self.timeout, width=10)
        self._form_entry(options, 3, 0, "API port", self.port, width=10)
        self._form_entry(options, 3, 3, "API-SSL port", self.ssl_port, width=10)
        self._form_entry(options, 4, 0, "Start at line", self.start_line, width=10)
        self._form_entry(options, 4, 3, "Update check attempts", self.update_check_attempts, width=10)
        self._form_entry(options, 5, 0, "Update check delay (seconds)", self.update_check_delay, width=10)
        self._form_entry(options, 5, 3, "Only these hosts", self.only)
        self._form_entry(options, 6, 0, "Cloud Backup password", self.cloud_password, show="•")
        self._form_entry(options, 6, 3, "Retry failed (latest or state path)", self.retry_failed)
        ttk.Button(
            options, text="Browse…", command=self._choose_retry_state, style="Secondary.TButton"
        ).grid(
            row=6, column=5, padx=(6, 0), sticky="w"
        )

        flags = ttk.Frame(options)
        self._form_entry(options, 7, 0, "SSL CA bundle", self.ssl_ca_file)
        ttk.Button(
            options, text="Browse…", command=self._choose_ssl_ca_file, style="Secondary.TButton"
        ).grid(
            row=7, column=2, padx=(6, 12), sticky="w"
        )
        ttk.Checkbutton(
            options,
            text="Allow legacy SSL ciphers (insecure)",
            variable=self.ssl_legacy_ciphers,
        ).grid(row=7, column=3, columnspan=3, sticky="w")

        flags.grid(row=8, column=0, columnspan=6, sticky="w", pady=(8, 0))
        for index, (text, variable) in enumerate((
            ("Dry run", self.dry_run),
            ("Read-only inventory", self.inventory),
            ("Upgrade firmware", self.upgrade_firmware),
            ("SSL for all hosts", self.ssl),
            ("Verify SSL certificates (recommended)", self.ssl_verify),
            ("Cloud Backup", self.cloud_backup),
            ("Write reports", self.report),
            ("Debug logging", self.debug),
        )):
            ttk.Checkbutton(flags, text=text, variable=variable).grid(
                row=index // 4,
                column=index % 4,
                sticky="w",
                padx=(0, 16),
            )

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="Hosts").pack(side="left")
        ttk.Label(toolbar, text="Show:").pack(side="right", padx=(10, 4))
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.host_filter,
            values=("All", "Pending", "Successful", "Failed", "Update available"),
            state="readonly",
            width=18,
        )
        filter_box.pack(side="right")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_hosts())

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        self.host_table = ttk.Treeview(
            table_frame,
            columns=tuple(column[0] for column in self.COLUMNS),
            show="headings",
            selectmode="browse",
        )
        for key, title, width in self.COLUMNS:
            self.host_table.heading(key, text=title)
            self.host_table.column(key, width=width, minwidth=75, stretch=key in {"identity", "model"})
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.host_table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.host_table.xview)
        self.host_table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.host_table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.host_table.bind("<<TreeviewSelect>>", self._show_selected_log)

        log_frame = ttk.LabelFrame(outer, text="Selected host log", padding=6)
        log_frame.pack(fill="x", pady=(8, 0))
        self.log_view = tk.Text(log_frame, height=7, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=log_scroll.set)
        self.log_view.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        footer = ttk.Frame(outer)
        # Reserve this command strip before the expandable panes can consume its space.
        footer.pack(side="bottom", fill="x", pady=(10, 0), before=table_frame)
        self.progress = ttk.Progressbar(
            footer, mode="determinate", length=220, style="Primary.Horizontal.TProgressbar"
        )
        self.progress.pack(side="left", padx=(0, 10))
        self.summary = ttk.Label(footer, text="Ready")
        self.summary.pack(side="left", fill="x", expand=True)
        self.open_button = ttk.Button(
            footer,
            text="Open output folder",
            command=self._open_output,
            state="disabled",
            style="Secondary.TButton",
        )
        self.open_button.pack(side="right", padx=(6, 0))
        self.stop_button = ttk.Button(
            footer, text="Stop", command=self._stop_run, state="disabled", style="Danger.TButton"
        )
        self.stop_button.pack(side="right", padx=(6, 0))
        self.start_button = ttk.Button(
            footer, text="Start run", command=self._start_run, style="Primary.TButton"
        )
        self.start_button.pack(side="right")

        self._responsive_frames = (heading, options, toolbar, log_frame, footer)
        self._table_frame = table_frame
        self._log_frame = log_frame
        self._fixed_layout_height: int | None = None
        self._row_height = 20
        self._line_height = 16

    def _apply_theme(self) -> None:
        theme_name = apply_theme(
            self.root,
            self.theme.get(),
            log_view=self.log_view,
            host_table=self.host_table,
        )
        self.theme.set(theme_name.title())

    def _on_theme_selected(self, _event: tk.Event[Any] | None = None) -> None:
        self._apply_theme()
        self._save_settings()

    @staticmethod
    def _content_widget_heights(
        available_height: int, row_height: int, line_height: int
    ) -> tuple[int, int]:
        """Choose host rows and log lines that fit the space above the footer."""
        available_height = max(0, available_height)
        row_height = max(1, row_height)
        line_height = max(1, line_height)
        host_rows = max(1, min(10, int(available_height * 0.68 / row_height)))
        log_space = max(0, available_height - host_rows * row_height)
        log_lines = max(1, min(7, int(log_space / line_height)))
        return host_rows, log_lines

    def _initialize_responsive_layout(self) -> None:
        """Measure fixed controls once before shrinking the host and log panes."""
        self.root.update_idletasks()
        heading, options, toolbar, log_frame, footer = self._responsive_frames
        footer.configure(height=footer.winfo_reqheight())
        footer.pack_propagate(False)
        self.root.update_idletasks()
        log_chrome = max(
            0, log_frame.winfo_reqheight() - self.log_view.winfo_reqheight()
        )
        try:
            row_height = int(ttk.Style(self.root).lookup("Treeview", "rowheight"))
        except (TypeError, ValueError):
            row_height = 0
        if row_height <= 0:
            row_count = max(1, int(self.host_table.cget("height")))
            row_height = max(1, self.host_table.winfo_reqheight() // row_count)
        self._row_height = row_height
        self._line_height = max(
            1, math.ceil(self.log_view.winfo_reqheight() / max(1, int(self.log_view.cget("height"))))
        )
        table_rows = max(1, int(self.host_table.cget("height")))
        table_chrome = max(
            0, self._table_frame.winfo_reqheight() - table_rows * self._row_height
        )
        # Outer padding, pack spacing, and fixed headers/scrollbars.
        self._fixed_layout_height = (
            sum(frame.winfo_reqheight() for frame in (heading, options, toolbar, footer))
            + log_chrome
            + table_chrome
            + 68
        )
        self._resize_content()

    def _resize_content(self, event: tk.Event[Any] | None = None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if self._fixed_layout_height is None:
            return
        height = event.height if event is not None else self.root.winfo_height()
        available = height - self._fixed_layout_height
        host_rows, log_lines = self._content_widget_heights(
            available, self._row_height, self._line_height
        )
        if int(self.host_table.cget("height")) != host_rows:
            self.host_table.configure(height=host_rows)
        if int(self.log_view.cget("height")) != log_lines:
            self.log_view.configure(height=log_lines)

    @staticmethod
    def _form_entry(
        parent: ttk.LabelFrame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        width: int = 28,
        show: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable, width=width, show=show)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(8, 8), pady=3)

    def _choose_ip_list(self) -> None:
        path = filedialog.askopenfilename(title="Select host list")
        if path:
            self.ip_list.set(path)

    def _choose_commands(self) -> None:
        path = filedialog.askopenfilename(
            title="Select custom commands YAML",
            filetypes=(("YAML files", "*.yaml *.yml"), ("All files", "*")),
        )
        if path:
            self.custom_commands.set(path)

    def _choose_ssl_ca_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select SSL CA bundle",
            filetypes=(
                ("Certificate files", "*.pem *.crt *.cer"),
                ("All files", "*"),
            ),
        )
        if path:
            self.ssl_ca_file.set(path)

    def _choose_retry_state(self) -> None:
        path = filedialog.askopenfilename(
            title="Select previous run state",
            filetypes=(("State files", "state.json"), ("JSON files", "*.json"), ("All files", "*")),
        )
        if path:
            self.retry_failed.set(path)

    def apply_startup_args(self, args: Any) -> None:
        """Populate controls from shared CLI, config-file, and saved defaults."""
        for name in (
            "username", "password", "ip_list", "threads", "timeout", "port",
            "ssl_port", "update_check_attempts", "update_check_delay", "start_line",
            "only", "custom_commands", "ssl_ca_file",
        ):
            value = getattr(args, name, None)
            variable = getattr(self, name)
            variable.set("" if value is None else str(value))

        self.dry_run.set(args.dry_run)
        self.inventory.set(args.inventory)
        self.upgrade_firmware.set(args.upgrade_firmware)
        self.ssl.set(args.ssl)
        self.ssl_verify.set(args.ssl_verify)
        self.ssl_legacy_ciphers.set(args.ssl_legacy_ciphers)
        self.cloud_password.set(args.cloud_password or "")
        self.cloud_backup.set(bool(args.cloud_password))
        self.retry_failed.set(args.retry_failed or "")
        self.report.set(args.report)
        self.debug.set(args.debug)
        self.theme.set("Dark" if str(getattr(args, "theme", "light")).lower() == "dark" else "Light")
        self._apply_theme()

    def _form_values(self) -> dict[str, Any]:
        return {
            "username": self.username.get(),
            "password": self.password.get(),
            "ip_list": self.ip_list.get(),
            "threads": self.threads.get(),
            "timeout": self.timeout.get(),
            "port": self.port.get(),
            "ssl_port": self.ssl_port.get(),
            "update_check_attempts": self.update_check_attempts.get(),
            "update_check_delay": self.update_check_delay.get(),
            "start_line": self.start_line.get(),
            "dry_run": self.dry_run.get(),
            "inventory": self.inventory.get(),
            "upgrade_firmware": self.upgrade_firmware.get(),
            "ssl": self.ssl.get(),
            "ssl_verify": self.ssl_verify.get(),
            "ssl_ca_file": self.ssl_ca_file.get(),
            "ssl_legacy_ciphers": self.ssl_legacy_ciphers.get(),
            "cloud_backup": self.cloud_backup.get(),
            "cloud_password": self.cloud_password.get(),
            "custom_commands": self.custom_commands.get(),
            "only": self.only.get(),
            "retry_failed": self.retry_failed.get(),
            "report": self.report.get(),
            "debug": self.debug.get(),
            "theme": self.theme.get().lower(),
        }

    def _settings_values(self) -> dict[str, Any]:
        values = self._form_values()
        return {
            key: values[key]
            for key in (
                "username", "ip_list", "threads", "timeout", "port", "ssl_port",
                "update_check_attempts", "update_check_delay", "start_line", "dry_run",
                "inventory", "ssl", "ssl_verify", "ssl_ca_file",
                "ssl_legacy_ciphers",
                "custom_commands", "only", "report", "debug", "theme",
            )
        }

    def _save_settings(self) -> None:
        try:
            save_settings(self._settings_values())
        except (OSError, SettingsError) as error:
            messagebox.showwarning(
                "Preferences not saved",
                f"Could not save desktop preferences: {error}",
                parent=self.root,
            )

    def _start_run(self) -> None:
        try:
            config = build_updater_config(self._form_values())
        except ValueError as error:
            messagebox.showerror("Invalid options", str(error), parent=self.root)
            return

        if not config.inventory and not config.dry_run:
            confirmed = messagebox.askyesno(
                "Confirm device changes",
                "This run may install RouterOS or firmware updates and reboot devices. Continue?",
                parent=self.root,
            )
            if not confirmed:
                return

        self._save_settings()

        try:
            self.run_dir = mkmassupdate._create_run_dir(mkmassupdate._new_run_id())
        except OSError as error:
            messagebox.showerror("Cannot create output folder", str(error), parent=self.root)
            return
        self.open_button.configure(state="normal")

        for handler in list(mkmassupdate.logger.handlers):
            mkmassupdate.logger.removeHandler(handler)
            handler.close()
        mkmassupdate._setup_logger(False, self.debug.get(), self.run_dir, console=False)

        self.events = queue.Queue()
        self.hosts.clear()
        self.rows_by_ip.clear()
        self.active_rows.clear()
        self.logs.clear()
        self.pending_hosts.clear()
        self.pending_host_index = 0
        self._row_sequence = 0
        self.host_table.delete(*self.host_table.get_children())
        self._set_log_text("")
        self.progress.configure(maximum=1, value=0)
        self.summary.configure(text="Starting…")
        self.updater = MassUpdater(
            config,
            run_dir=self.run_dir,
            event_queue=self.events,
        )
        current_updater = self.updater
        current_events = self.events
        self._set_running(True)

        def run() -> None:
            try:
                current_updater.run()
            except Exception as error:  # Surface unexpected worker failures in the UI.
                current_events.put(
                    {"type": "ui_error", "message": f"{type(error).__name__}: {error}"}
                )

        self.run_thread = threading.Thread(target=run, name="UpdaterRun", daemon=True)
        self.run_thread.start()

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _stop_run(self) -> None:
        if self.updater is not None:
            self.updater.request_stop()
            self.stop_button.configure(state="disabled")
            self.summary.configure(text="Stopping after active hosts finish…")

    def _close(self) -> None:
        self._save_settings()
        if self.run_thread is not None and self.run_thread.is_alive():
            self.close_when_done = True
            self._stop_run()
            return
        self.root.destroy()

    def _poll_events(self) -> None:
        self._append_pending_hosts(250)
        if self.pending_hosts:
            self.root.after(50, self._poll_events)
            return
        for _ in range(250):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
            if self.pending_hosts:
                break
        self._append_pending_hosts(250)
        if self.close_when_done and (self.run_thread is None or not self.run_thread.is_alive()):
            self.root.destroy()
            return
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "hosts_loaded":
            self.progress.configure(maximum=max(1, event.get("total", 0)), value=0)
            self.pending_hosts = event.get("hosts", [])
            self.pending_host_index = 0
            self.summary.configure(text=f"Loaded {event.get('total', 0)} hosts")
        elif event_type == "host_started":
            row = self._next_row_for_ip(event.get("ip", ""))
            if row:
                record = self.hosts[row]
                record["status"] = "working"
                self.active_rows[event.get("worker", "")] = row
                self._update_host_row(row)
        elif event_type == "log_line":
            row = self.active_rows.get(event.get("worker", ""))
            if row:
                line = event.get("message", "")
                self.logs[row] = self.logs.get(row, "") + line + "\n"
                if self.host_table.selection() == (row,):
                    self._show_log(row)
        elif event_type == "host_result":
            worker = event.get("worker", "")
            self._apply_host_result(event["host"], self.active_rows.pop(worker, None))
        elif event_type == "progress":
            self.progress.configure(maximum=max(1, event.get("total", 0)), value=event.get("processed", 0))
            self.summary.configure(
                text=f"Processed {event.get('processed', 0)} / {event.get('total', 0)}  ·  "
                f"OK {event.get('succeeded', 0)}  ·  Failed {event.get('failed', 0)}"
            )
        elif event_type == "completed":
            self._complete_run(event)
        elif event_type == "ui_error":
            self._set_running(False)
            self.summary.configure(text="Run stopped unexpectedly")
            messagebox.showerror("Run error", event.get("message", "Unknown error"), parent=self.root)

    def _add_host(self, host: dict[str, Any]) -> None:
        self._row_sequence += 1
        row = f"host-{self._row_sequence}"
        record = {
            "ip": host.get("ip", ""),
            "port": host.get("port", ""),
            "identity": "—",
            "model": "—",
            "routeros": "—",
            "firmware": "—",
            "update": "Unknown",
            "status": "pending",
        }
        self.hosts[row] = record
        self.rows_by_ip.setdefault(record["ip"], []).append(row)
        self.logs[row] = ""
        self._update_host_row(row)

    def _append_pending_hosts(self, limit: int) -> None:
        end = min(self.pending_host_index + limit, len(self.pending_hosts))
        for host in self.pending_hosts[self.pending_host_index:end]:
            self._add_host(host)
        self.pending_host_index = end
        if end == len(self.pending_hosts):
            self.pending_hosts = []
            self.pending_host_index = 0

    def _current_row_for_ip(self, ip: str) -> str | None:
        for row in self.rows_by_ip.get(ip, []):
            if self.hosts[row]["status"] == "working":
                return row
        return None

    def _next_row_for_ip(self, ip: str) -> str | None:
        for row in self.rows_by_ip.get(ip, []):
            if self.hosts[row]["status"] == "pending":
                return row
        return None

    def _apply_host_result(self, host: dict[str, Any], row: str | None = None) -> None:
        ip = host.get("ip", "")
        row = row or self._current_row_for_ip(ip) or self._next_row_for_ip(ip)
        if row is None:
            self._add_host({"ip": ip, "port": host.get("port", "")})
            row = self.rows_by_ip[ip][-1]
        record = self.hosts[row]
        record.update(
            identity=host.get("identity") or "—",
            model=host.get("model") or "—",
            routeros=self._routeros_label(host),
            firmware=self._firmware_label(host),
            update=self._update_label(host.get("update_available")),
            status=host.get("status", "failed"),
        )
        self._update_host_row(row)

    @staticmethod
    def _routeros_label(host: dict[str, Any]) -> str:
        installed = host.get("installed_version") or host.get("routeros_version") or "—"
        latest = host.get("latest_version")
        if host.get("update_available") is True and latest and latest != installed:
            return f"{installed} → {latest}"
        return str(installed)

    @staticmethod
    def _firmware_label(host: dict[str, Any]) -> str:
        current = host.get("firmware_current") or "—"
        upgrade = host.get("firmware_upgrade")
        return f"{current} → {upgrade}" if upgrade and upgrade != current else str(current)

    @staticmethod
    def _update_label(available: bool | None) -> str:
        if available is True:
            return "Available"
        if available is False:
            return "Current"
        return "Unknown"

    def _matches_filter(self, record: dict[str, Any]) -> bool:
        selected = self.host_filter.get()
        if selected == "Pending":
            return record["status"] in {"pending", "working"}
        if selected == "Successful":
            return record["status"] == "ok"
        if selected == "Failed":
            return record["status"] == "failed"
        if selected == "Update available":
            return record["update"] == "Available"
        return True

    def _update_host_row(self, row: str) -> None:
        record = self.hosts[row]
        values = tuple(record[key] for key, _title, _width in self.COLUMNS)
        tags = (f"status-{record['status']}",)
        if self._matches_filter(record):
            if self.host_table.exists(row):
                self.host_table.item(row, values=values, tags=tags)
            else:
                self.host_table.insert("", "end", iid=row, values=values, tags=tags)
        elif self.host_table.exists(row):
            self.host_table.delete(row)

    def _refresh_hosts(self) -> None:
        selection = self.host_table.selection()
        selected = selection[0] if selection else None
        for row in list(self.host_table.get_children()):
            self.host_table.delete(row)
        for row, record in self.hosts.items():
            if self._matches_filter(record):
                values = tuple(record[key] for key, _title, _width in self.COLUMNS)
                self.host_table.insert(
                    "", "end", iid=row, values=values, tags=(f"status-{record['status']}",)
                )
        if selected and self.host_table.exists(selected):
            self.host_table.selection_set(selected)

    def _show_selected_log(self, _event: tk.Event[Any] | None = None) -> None:
        selection = self.host_table.selection()
        self._show_log(selection[0] if selection else None)

    def _show_log(self, row: str | None) -> None:
        self._set_log_text(self.logs.get(row, "") if row else "")

    def _set_log_text(self, text: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.insert("1.0", text)
        self.log_view.configure(state="disabled")

    def _complete_run(self, event: dict[str, Any]) -> None:
        if event.get("cancelled"):
            for row, record in self.hosts.items():
                if record["status"] == "pending":
                    record["status"] = "cancelled"
                    self._update_host_row(row)
        summary = event.get("summary", {})
        state = "Cancelled" if event.get("cancelled") else "Finished"
        self.summary.configure(
            text=f"{state}: {summary.get('total', 0)} processed  ·  "
            f"OK {summary.get('ok', 0)}  ·  Failed {summary.get('failed', 0)}  ·  "
            f"{summary.get('elapsed', '0s')}"
        )
        self._set_running(False)
        if event.get("error"):
            self.summary.configure(text=event["error"])
            messagebox.showerror("Run could not start", event["error"], parent=self.root)
        if self.close_when_done:
            return

    def _open_output(self) -> None:
        if self.run_dir is None:
            return
        try:
            if os.name == "nt":
                os.startfile(self.run_dir)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(self.run_dir)])
        except OSError as error:
            messagebox.showerror("Cannot open output folder", str(error), parent=self.root)


def main(argv: list[str] | None = None) -> None:
    settings_error: str | None = None
    try:
        preferences = load_settings()
    except SettingsError as error:
        preferences = {}
        settings_error = str(error)

    args = mkmassupdate._parse_args(
        argv,
        require_username=False,
        defaults=preferences,
    )
    root = tk.Tk()
    app = MikroTikUpdaterApp(root)
    app.apply_startup_args(args)
    if settings_error:
        root.after_idle(
            lambda: messagebox.showwarning(
                "Preferences could not be loaded", settings_error, parent=root
            )
        )
    root.mainloop()


if __name__ == "__main__":
    main()
