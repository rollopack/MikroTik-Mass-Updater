"""Shared Light and Dark ttk styles for the desktop interface."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


THEME_PALETTES = {
    "light": {
        "window": "#eef2f5",
        "surface": "#ffffff",
        "field": "#ffffff",
        "text": "#243746",
        "muted": "#536b7a",
        "border": "#c8d4dc",
        "accent": "#176b7a",
        "accent_hover": "#115764",
        "accent_text": "#ffffff",
        "secondary": "#dde6eb",
        "secondary_hover": "#cbd9e1",
        "danger": "#a92e2a",
        "danger_hover": "#8c2522",
        "danger_text": "#ffffff",
        "success_text": "#17603f",
        "failed_text": "#a52a25",
        "working_text": "#165d78",
        "pending_text": "#526574",
        "selection": "#c9e3ea",
        "selection_text": "#1e3b49",
        "disabled": "#7d8c96",
        "trough": "#d8e1e6",
    },
    "dark": {
        "window": "#121a22",
        "surface": "#1e2b36",
        "field": "#192630",
        "text": "#e6edf3",
        "muted": "#b0bec9",
        "border": "#354652",
        "accent": "#45a8b7",
        "accent_hover": "#69bbc6",
        "accent_text": "#10232a",
        "secondary": "#2a3944",
        "secondary_hover": "#374955",
        "danger": "#a9413b",
        "danger_hover": "#c2524b",
        "danger_text": "#ffffff",
        "success_text": "#82d7a6",
        "failed_text": "#ff9d96",
        "working_text": "#80c9dc",
        "pending_text": "#b0bec9",
        "selection": "#29444f",
        "selection_text": "#eef6f8",
        "disabled": "#83929c",
        "trough": "#34434d",
    },
}

STATUS_COLORS = {
    "ok": "success_text",
    "failed": "failed_text",
    "working": "working_text",
    "pending": "pending_text",
    "cancelled": "pending_text",
}


def apply_theme(
    root: tk.Misc,
    theme_name: str,
    *,
    log_view: tk.Text,
    host_table: ttk.Treeview,
) -> str:
    """Apply a palette consistently and return its normalized name."""
    theme_name = str(theme_name).strip().lower()
    if theme_name not in THEME_PALETTES:
        theme_name = "light"
    palette = THEME_PALETTES[theme_name]

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=palette["window"], foreground=palette["text"])
    style.configure("TFrame", background=palette["window"])
    style.configure("TLabel", background=palette["window"], foreground=palette["text"])
    style.configure(
        "TLabelframe",
        background=palette["window"],
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
    )
    style.configure(
        "TLabelframe.Label",
        background=palette["window"],
        foreground=palette["muted"],
        font=("TkDefaultFont", 9, "bold"),
    )
    style.configure(
        "TEntry",
        fieldbackground=palette["field"],
        foreground=palette["text"],
        bordercolor=palette["border"],
        insertcolor=palette["text"],
        focuscolor=palette["accent"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
    )
    style.configure(
        "TCombobox",
        fieldbackground=palette["field"],
        foreground=palette["text"],
        background=palette["secondary"],
        arrowcolor=palette["text"],
        bordercolor=palette["border"],
        focuscolor=palette["accent"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette["field"])],
        foreground=[("readonly", palette["text"])],
    )
    style.configure("TCheckbutton", background=palette["window"], foreground=palette["text"])
    style.map(
        "TCheckbutton",
        background=[("active", palette["window"])],
        foreground=[("disabled", palette["disabled"])],
    )

    button_styles = (
        ("TButton", "surface", "text", "secondary_hover", "border", False),
        ("Secondary.TButton", "secondary", "text", "secondary_hover", "border", False),
        ("Primary.TButton", "accent", "accent_text", "accent_hover", "accent", True),
        ("Danger.TButton", "danger", "danger_text", "danger_hover", "border", False),
    )
    for name, background, foreground, hover, border, bold in button_styles:
        style.configure(
            name,
            background=palette[background],
            foreground=palette[foreground],
            bordercolor=palette[border],
            lightcolor=palette[border],
            darkcolor=palette[border],
            padding=(10, 5),
            focusthickness=2,
            focuscolor=palette["accent"],
            **({"font": ("TkDefaultFont", 9, "bold")} if bold else {}),
        )
        style.map(
            name,
            background=[("disabled", palette["secondary"]), ("active", palette[hover])],
            foreground=[("disabled", palette["disabled"])],
        )

    style.configure(
        "Treeview",
        background=palette["surface"],
        fieldbackground=palette["surface"],
        foreground=palette["text"],
        rowheight=25,
        bordercolor=palette["border"],
        lightcolor=palette["border"],
        darkcolor=palette["border"],
    )
    style.map(
        "Treeview",
        background=[("selected", palette["selection"])],
        foreground=[("selected", palette["selection_text"])],
    )
    style.configure(
        "Treeview.Heading",
        background=palette["secondary"],
        foreground=palette["text"],
        relief="flat",
        padding=(6, 6),
        lightcolor=palette["border"],
        darkcolor=palette["border"],
    )
    style.map("Treeview.Heading", background=[("active", palette["secondary_hover"])])
    style.configure(
        "Primary.Horizontal.TProgressbar",
        background=palette["accent"],
        troughcolor=palette["trough"],
        bordercolor=palette["border"],
    )
    style.configure(
        "TScrollbar",
        background=palette["secondary"],
        troughcolor=palette["window"],
        arrowcolor=palette["text"],
        bordercolor=palette["border"],
    )
    root.configure(background=palette["window"])
    log_view.configure(
        background=palette["field"],
        foreground=palette["text"],
        insertbackground=palette["text"],
        selectbackground=palette["selection"],
        selectforeground=palette["selection_text"],
        highlightthickness=1,
        highlightbackground=palette["border"],
        highlightcolor=palette["accent"],
    )
    for status, color_name in STATUS_COLORS.items():
        host_table.tag_configure(f"status-{status}", foreground=palette[color_name])

    return theme_name
