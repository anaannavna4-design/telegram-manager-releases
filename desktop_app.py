from __future__ import annotations

import json
import os
import importlib.util
import subprocess
import sys
import threading
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_paths import account_dir, import_legacy_data, runtime_dir, source_dir
from settings_store import load_accounts, load_settings, save_accounts, save_settings
from desktop_bridge import load_desktop_queue, submit_desktop_command

APP_VERSION = "1.1.4"

# Telegram Manager inspired palette
BG = "#0b1017"
SIDEBAR = "#101823"
PANEL = "#121b26"
PANEL_ALT = "#172230"
PANEL_DARK = "#0e151f"
BORDER = "#26384c"
BORDER_SOFT = "#1d2b3c"
TEXT = "#f4f7fb"
MUTED = "#89a0be"
MUTED_2 = "#657a96"
ACCENT = "#4aa8ff"
ACCENT_DARK = "#183c61"
ACCENT_HOVER = "#234b71"
BUTTON = "#1a2838"
BUTTON_HOVER = "#22354a"
GREEN = "#43d18b"
RED = "#ff6c72"
YELLOW = "#e7bd5b"


def _as_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _split_lines(value: str) -> list[str]:
    result = []
    for raw in (value or "").replace(",", "\n").splitlines():
        item = raw.strip().lstrip("@")
        if item and item not in result:
            result.append(item)
    return result


def run_engine_mode() -> None:
    # Import only in the child/engine process. This guarantees that config.py
    # reads the settings that were just saved by the Desktop UI.
    import asyncio
    from main import main as engine_main

    asyncio.run(engine_main())


class AccountDialog(tk.Toplevel):
    def __init__(self, parent, initial=None, used_names=None):
        super().__init__(parent)
        self.title("Telegram-аккаунт")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.initial = initial or {}
        self.used_names = {str(x).lower() for x in (used_names or [])}
        if self.initial.get("name"):
            self.used_names.discard(str(self.initial["name"]).lower())

        shell = tk.Frame(self, bg=BG, padx=18, pady=18)
        shell.pack(fill="both", expand=True)
        card = tk.Frame(shell, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=18, pady=18)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="Telegram-аккаунт", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 16))

        self.name_var = tk.StringVar(value=self.initial.get("name", ""))
        self.admin_var = tk.StringVar(value=str(self.initial.get("admin_id", "")))
        self.session_var = tk.StringVar(value=self.initial.get("session", ""))
        self.autopilot_var = tk.BooleanVar(value=bool(self.initial.get("autopilot", False)))

        self._field(card, 1, "Название аккаунта", self.name_var)
        self._field(card, 3, "Telegram ID администратора (необязательно без BotPanel)", self.admin_var)
        self._field(card, 5, "Имя сессии", self.session_var)

        check = tk.Checkbutton(
            card, text="Автопилот включён", variable=self.autopilot_var,
            bg=PANEL, fg=TEXT, activebackground=PANEL, activeforeground=TEXT,
            selectcolor=PANEL_ALT, font=("Segoe UI", 10), bd=0, highlightthickness=0,
        )
        check.grid(row=7, column=0, sticky="w", pady=(4, 12))

        tk.Label(
            card,
            text=("После первого запуска NeuroComment откроет QR-код для этого аккаунта.\n"
                  "Telegram ID нужен только если включена дополнительная Telegram BotPanel."),
            bg=PANEL, fg=MUTED, justify="left", font=("Segoe UI", 9),
        ).grid(row=8, column=0, sticky="w", pady=(0, 16))

        buttons = tk.Frame(card, bg=PANEL)
        buttons.grid(row=9, column=0, sticky="e")
        self._button(buttons, "Отмена", self.destroy).pack(side="right", padx=(8, 0))
        self._button(buttons, "Сохранить", self._save, primary=True).pack(side="right")

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(50, lambda: self.focus_force())

    def _field(self, parent, row, label, variable):
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=(0, 5))
        entry = tk.Entry(
            parent, textvariable=variable, width=42, bg=PANEL_DARK, fg=TEXT,
            insertbackground=TEXT, relief="flat", highlightbackground=BORDER,
            highlightcolor=ACCENT, highlightthickness=1, font=("Segoe UI", 10),
        )
        entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 12), ipady=8)
        return entry

    def _button(self, parent, text, command, primary=False):
        bg = ACCENT_DARK if primary else BUTTON
        active = ACCENT_HOVER if primary else BUTTON_HOVER
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=TEXT,
            activebackground=active, activeforeground=TEXT, relief="flat",
            bd=0, padx=16, pady=8, cursor="hand2", font=("Segoe UI", 9),
        )

    def _save(self):
        name = self.name_var.get().strip()
        if not name:
            return messagebox.showerror("Аккаунт", "Введите название аккаунта.", parent=self)
        if name.lower() in self.used_names:
            return messagebox.showerror("Аккаунт", "Аккаунт с таким названием уже существует.", parent=self)

        admin_raw = self.admin_var.get().strip()
        admin_id = _as_int(admin_raw, 0)
        if admin_raw and admin_id <= 0:
            return messagebox.showerror(
                "Аккаунт", "Telegram ID администратора должен быть положительным числом или пустым.", parent=self
            )

        session = self.session_var.get().strip() or f"neurocomment_{name}"
        self.result = {
            "name": name,
            "admin_id": admin_id,
            "session": session,
            "autopilot": bool(self.autopilot_var.get()),
        }
        self.destroy()


class NeuroCommentDesktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"NeuroComment {APP_VERSION}")
        self.geometry("1200x780")
        self.minsize(1050, 700)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.settings = load_settings()
        self.accounts = load_accounts()
        self.engine_process = None
        self._closing = False
        self._stop_requested = False
        self._ollama_ok = False
        self._openai_ok = False
        self._current_page = None
        self.pages = {}
        self.nav_buttons = {}
        self.desktop_queue = []
        self.desktop_queue_by_key = {}
        self._queue_signature = None

        self._configure_style()
        self._install_clipboard_support()
        self._build_ui()
        self._load_into_ui()
        self._update_status(False)
        self.show_page("home")
        self.after(500, self._poll_desktop_queue)

    # ---------------------------- theme / helpers ----------------------------

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "Dark.Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            bordercolor=BORDER,
            borderwidth=0,
            rowheight=34,
            font=("Segoe UI", 9),
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", ACCENT_DARK)],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=PANEL_ALT,
            foreground=TEXT,
            bordercolor=BORDER,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padding=(8, 8),
        )
        style.map("Dark.Treeview.Heading", background=[("active", BUTTON_HOVER)])

        style.configure(
            "Dark.TEntry",
            fieldbackground=PANEL_DARK,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=(8, 7),
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=PANEL_DARK,
            background=PANEL_DARK,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=(8, 6),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", PANEL_DARK)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL_DARK)],
            selectforeground=[("readonly", TEXT)],
        )
        self.option_add("*TCombobox*Listbox.background", PANEL_DARK)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DARK)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)

        style.configure(
            "NC.Horizontal.TProgressbar",
            troughcolor=PANEL_DARK,
            background=ACCENT,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=14,
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=BUTTON,
            troughcolor=PANEL_DARK,
            bordercolor=PANEL_DARK,
            arrowcolor=MUTED,
        )

    # ---------------------------- clipboard ----------------------------

    @staticmethod
    def _is_text_widget(widget):
        return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox, tk.Spinbox))

    @staticmethod
    def _widget_is_writable(widget):
        try:
            if isinstance(widget, tk.Text):
                return str(widget.cget("state")) != "disabled"
            if isinstance(widget, ttk.Combobox):
                return "disabled" not in widget.state() and "readonly" not in widget.state()
            if isinstance(widget, ttk.Entry):
                return "disabled" not in widget.state() and "readonly" not in widget.state()
            return str(widget.cget("state")) not in {"disabled", "readonly"}
        except Exception:
            return True

    def _selection_text(self, widget):
        try:
            if isinstance(widget, tk.Text):
                return widget.get("sel.first", "sel.last")
            return widget.selection_get()
        except Exception:
            return ""

    def _copy_from_widget(self, widget):
        if not self._is_text_widget(widget):
            return
        text = self._selection_text(widget)
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _delete_selection(self, widget):
        try:
            if isinstance(widget, tk.Text):
                widget.delete("sel.first", "sel.last")
            elif widget.selection_present():
                widget.delete("sel.first", "sel.last")
        except Exception:
            pass

    def _paste_into_widget(self, widget):
        if not self._is_text_widget(widget) or not self._widget_is_writable(widget):
            return
        try:
            text = self.clipboard_get()
        except Exception:
            return
        self._delete_selection(widget)
        try:
            if isinstance(widget, tk.Text):
                widget.insert("insert", text)
            else:
                widget.insert("insert", text)
        except Exception:
            pass

    def _cut_from_widget(self, widget):
        if not self._is_text_widget(widget) or not self._widget_is_writable(widget):
            return
        self._copy_from_widget(widget)
        self._delete_selection(widget)

    def _select_all_widget(self, widget):
        if not self._is_text_widget(widget):
            return
        try:
            if isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
                widget.see("insert")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
        except Exception:
            pass

    def _handle_clipboard_key(self, event):
        widget = event.widget
        if not self._is_text_widget(widget):
            return None

        # On Windows keycode is the virtual-key code and therefore keeps
        # working even when the active keyboard layout is Russian.
        keysym = str(getattr(event, "keysym", "")).lower()
        keycode = int(getattr(event, "keycode", 0) or 0)
        action = None
        if keysym in {"c", "с"} or keycode == 67:
            action = "copy"
        elif keysym in {"v", "м"} or keycode == 86:
            action = "paste"
        elif keysym in {"x", "ч"} or keycode == 88:
            action = "cut"
        elif keysym in {"a", "ф"} or keycode == 65:
            action = "select_all"

        if action == "copy":
            self._copy_from_widget(widget)
        elif action == "paste":
            self._paste_into_widget(widget)
        elif action == "cut":
            self._cut_from_widget(widget)
        elif action == "select_all":
            self._select_all_widget(widget)
        else:
            return None
        return "break"

    def _show_text_context_menu(self, event):
        widget = event.widget
        if not self._is_text_widget(widget):
            return None
        try:
            widget.focus_set()
        except Exception:
            pass

        menu = tk.Menu(
            self, tearoff=False, bg=PANEL_ALT, fg=TEXT,
            activebackground=ACCENT_DARK, activeforeground=TEXT,
            bd=0, relief="flat",
        )
        writable = self._widget_is_writable(widget)
        has_selection = bool(self._selection_text(widget))
        menu.add_command(label="Вырезать", command=lambda: self._cut_from_widget(widget),
                         state="normal" if writable and has_selection else "disabled")
        menu.add_command(label="Копировать", command=lambda: self._copy_from_widget(widget),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Вставить", command=lambda: self._paste_into_widget(widget),
                         state="normal" if writable else "disabled")
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self._select_all_widget(widget))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def _install_clipboard_support(self):
        # One handler covers every Entry/Text field, including AccountDialog.
        # Explicit keycodes keep Ctrl+C/V/X/A functional with RU keyboard layout.
        self.bind_all("<Control-KeyPress>", self._handle_clipboard_key, add="+")
        self.bind_all("<Control-Insert>", lambda e: (self._copy_from_widget(e.widget), "break")[1]
                      if self._is_text_widget(e.widget) else None, add="+")
        self.bind_all("<Shift-Insert>", lambda e: (self._paste_into_widget(e.widget), "break")[1]
                      if self._is_text_widget(e.widget) else None, add="+")
        self.bind_all("<Button-3>", self._show_text_context_menu, add="+")

    def _button(self, parent, text, command, *, primary=False, danger=False, width=None):
        if danger:
            bg, active = "#4a2028", "#60303a"
        elif primary:
            bg, active = ACCENT_DARK, ACCENT_HOVER
        else:
            bg, active = BUTTON, BUTTON_HOVER
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=TEXT,
            activebackground=active,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            font=("Segoe UI", 9),
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        if width:
            btn.configure(width=width)
        return btn

    def _page_header(self, parent, title, subtitle):
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", pady=(0, 18))
        tk.Label(box, text=title, bg=BG, fg=TEXT,
                 font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(box, text=subtitle, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        return box

    def _panel(self, parent, *, padding=12):
        frame = tk.Frame(
            parent, bg=PANEL, highlightbackground=BORDER,
            highlightthickness=1, padx=padding, pady=padding,
        )
        return frame

    def _label(self, parent, text, *, muted=False, bold=False, size=9, bg=None):
        return tk.Label(
            parent,
            text=text,
            bg=bg or parent.cget("bg"),
            fg=MUTED if muted else TEXT,
            font=("Segoe UI", size, "bold" if bold else "normal"),
        )

    def _entry(self, parent, variable, *, show=None, width=None):
        return ttk.Entry(parent, textvariable=variable, show=show, width=width, style="Dark.TEntry")

    def _text(self, parent, *, height=10, wrap="word", font=("Segoe UI", 9)):
        return tk.Text(
            parent, height=height, wrap=wrap, bg=PANEL_DARK, fg=TEXT,
            insertbackground=TEXT, selectbackground=ACCENT_DARK,
            selectforeground=TEXT, relief="flat", highlightbackground=BORDER,
            highlightcolor=ACCENT, highlightthickness=1, font=font,
            padx=8, pady=8,
        )

    def _checkbox(self, parent, text, variable):
        return tk.Checkbutton(
            parent, text=text, variable=variable, bg=parent.cget("bg"), fg=TEXT,
            activebackground=parent.cget("bg"), activeforeground=TEXT,
            selectcolor=PANEL_ALT, bd=0, highlightthickness=0,
            font=("Segoe UI", 9), cursor="hand2",
        )

    def _field_row(self, parent, row, label, variable, *, show=None, width=52):
        self._label(parent, label, muted=True).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
        entry = self._entry(parent, variable, show=show, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=7)
        return entry

    # ---------------------------- main shell ----------------------------

    def _build_ui(self):
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True, padx=14, pady=14)

        self.sidebar = tk.Frame(
            shell, bg=SIDEBAR, width=190,
            highlightbackground=BORDER_SOFT, highlightthickness=1,
            padx=16, pady=16,
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content_shell = tk.Frame(shell, bg=BG)
        self.content_shell.pack(side="left", fill="both", expand=True, padx=(24, 0))

        self._build_sidebar()

        self.page_container = tk.Frame(self.content_shell, bg=BG)
        self.page_container.pack(fill="both", expand=True)

        for key in ("home", "accounts", "channels", "comments", "settings", "logs", "about"):
            page = tk.Frame(self.page_container, bg=BG)
            self.pages[key] = page

        self._build_home_page()
        self._build_accounts_page()
        self._build_channels_page()
        self._build_comments_page()
        self._build_settings_page()
        self._build_logs_page()
        self._build_about_page()

        self.statusbar = tk.Frame(self.content_shell, bg=BG)
        self.statusbar.pack(fill="x", pady=(10, 0))
        tk.Frame(self.statusbar, bg=BORDER_SOFT, height=1).pack(fill="x", pady=(0, 8))
        line = tk.Frame(self.statusbar, bg=BG)
        line.pack(fill="x")
        self.status_dot = tk.Label(line, text="●", bg=BG, fg=MUTED_2, font=("Segoe UI", 10))
        self.status_dot.pack(side="left")
        self.bottom_status = tk.Label(
            line, text="Ожидание", bg=BG, fg=MUTED,
            font=("Segoe UI", 9), anchor="w",
        )
        self.bottom_status.pack(side="left", padx=(6, 0))
        self.version_bottom = tk.Label(line, text=f"NeuroComment {APP_VERSION}", bg=BG, fg=MUTED_2,
                                       font=("Segoe UI", 8))
        self.version_bottom.pack(side="right")

    def _build_sidebar(self):
        logo = tk.Frame(self.sidebar, bg=PANEL_DARK, height=54,
                        highlightbackground=BORDER_SOFT, highlightthickness=1)
        logo.pack(fill="x", pady=(0, 8))
        logo.pack_propagate(False)
        badge = tk.Frame(logo, bg=PANEL_ALT, width=48, height=42,
                         highlightbackground=ACCENT, highlightthickness=1)
        badge.pack(side="left", padx=7, pady=6)
        badge.pack_propagate(False)
        tk.Label(badge, text="NC", bg=PANEL_ALT, fg=ACCENT,
                 font=("Segoe UI", 16, "bold")).pack(expand=True)

        tk.Label(self.sidebar, text="NEUROCOMMENT", bg=SIDEBAR, fg=TEXT,
                 font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x", pady=(2, 0))
        tk.Label(self.sidebar, text=APP_VERSION, bg=SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(5, 0))
        tk.Label(self.sidebar, text="AI comment manager", bg=SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(4, 16))

        nav = [
            ("home", "Главная"),
            ("accounts", "Аккаунты"),
            ("channels", "Каналы"),
            ("comments", "Комментарии"),
            ("settings", "Настройки"),
            ("logs", "Логи"),
            ("about", "О программе"),
        ]
        for key, text in nav:
            btn = tk.Button(
                self.sidebar, text=text, command=lambda k=key: self.show_page(k),
                bg=BUTTON, fg=TEXT, activebackground=BUTTON_HOVER,
                activeforeground=TEXT, relief="flat", bd=0,
                font=("Segoe UI", 9), pady=8, cursor="hand2",
                highlightthickness=1, highlightbackground=BORDER,
            )
            btn.pack(fill="x", pady=4)
            self.nav_buttons[key] = btn

        spacer = tk.Frame(self.sidebar, bg=SIDEBAR)
        spacer.pack(fill="both", expand=True)
        self.sidebar_status = tk.Label(
            self.sidebar, text="● Движок остановлен", bg=SIDEBAR, fg=MUTED,
            font=("Segoe UI", 8), anchor="w", justify="left",
        )
        self.sidebar_status.pack(fill="x", pady=(8, 0))

    def show_page(self, key):
        if key not in self.pages:
            return
        if self._current_page:
            self.pages[self._current_page].pack_forget()
        self._current_page = key
        self.pages[key].pack(fill="both", expand=True)

        for name, btn in self.nav_buttons.items():
            if name == key:
                btn.configure(bg=ACCENT_DARK, activebackground=ACCENT_HOVER,
                              highlightbackground=ACCENT)
            else:
                btn.configure(bg=BUTTON, activebackground=BUTTON_HOVER,
                              highlightbackground=BORDER)

    # ---------------------------- home ----------------------------

    def _stat_card(self, parent, col, title, value="0", wide=False):
        card = self._panel(parent, padding=10)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 6 if col < 3 else 0))
        self._label(card, title, muted=True, size=8).pack(anchor="w")
        value_label = tk.Label(card, text=value, bg=PANEL, fg=TEXT,
                               font=("Segoe UI", 20 if not wide else 16, "bold"), anchor="w")
        value_label.pack(anchor="w", pady=(6, 0))
        return value_label

    def _build_home_page(self):
        page = self.pages["home"]
        self._page_header(
            page, "Главная",
            "Управление Telegram-аккаунтами, каналами и AI-движком автоматизации.",
        )

        stats = tk.Frame(page, bg=BG)
        stats.pack(fill="x")
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1, uniform="stats")
        self.home_accounts_value = self._stat_card(stats, 0, "Аккаунты")
        self.home_channels_value = self._stat_card(stats, 1, "Каналы")
        self.home_autopilot_value = self._stat_card(stats, 2, "Автопилот")
        self.home_engine_value = self._stat_card(stats, 3, "Движок", "Ожидание", wide=True)

        self._label(page, "Готовность к запуску", bold=True, size=11, bg=BG).pack(anchor="w", pady=(24, 8))
        ready_panel = self._panel(page, padding=10)
        ready_panel.pack(fill="x")
        self.ready_progress = ttk.Progressbar(
            ready_panel, mode="determinate", maximum=5, style="NC.Horizontal.TProgressbar"
        )
        self.ready_progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.ready_text = self._label(ready_panel, "0 / 5", bold=True, bg=PANEL)
        self.ready_text.pack(side="right")

        secondary = tk.Frame(page, bg=BG)
        secondary.pack(fill="x", pady=(16, 0))
        for i in range(4):
            secondary.grid_columnconfigure(i, weight=1, uniform="secondary")
        self.home_api_value = self._stat_card_secondary(secondary, 0, "Telegram API", "Ожидание")
        self.home_bot_value = self._stat_card_secondary(secondary, 1, "Bot Panel", "Ожидание")
        self.home_ai_value = self._stat_card_secondary(secondary, 2, "AI", "Не проверено")
        self.home_status_value = self._stat_card_secondary(secondary, 3, "Статус", "Остановлен")

        self._label(page, "Быстрые действия", bold=True, size=11, bg=BG).pack(anchor="w", pady=(24, 8))
        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x")
        self._button(actions, "Добавить аккаунт", self.add_account, primary=True).pack(side="left", padx=(0, 8))
        self._button(actions, "Настройки", lambda: self.show_page("settings")).pack(side="left", padx=(0, 8))
        self._button(actions, "Проверить AI", self.check_ai_provider).pack(side="left", padx=(0, 8))
        self.home_start_btn = self._button(actions, "▶ Запустить", self.start_engine, primary=True)
        self.home_start_btn.pack(side="left", padx=(0, 8))
        self.home_stop_btn = self._button(actions, "■ Остановить", self.stop_engine, danger=True)
        self.home_stop_btn.pack(side="left")

        hint = self._panel(page, padding=12)
        hint.pack(fill="x", pady=(22, 0))
        self._label(hint, "Подсказка", bold=True, bg=PANEL).pack(anchor="w")
        self._label(
            hint,
            "При первом запуске нового аккаунта NeuroComment откроет QR-код. После авторизации сессия сохранится локально.",
            muted=True, bg=PANEL,
        ).pack(anchor="w", pady=(5, 0))

    def _stat_card_secondary(self, parent, col, title, value):
        card = self._panel(parent, padding=10)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 6 if col < 3 else 0))
        self._label(card, title, muted=True, size=8).pack(anchor="w")
        label = tk.Label(card, text=value, bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 12, "bold"), anchor="w")
        label.pack(anchor="w", pady=(6, 0))
        return label

    # ---------------------------- accounts ----------------------------

    def _build_accounts_page(self):
        page = self.pages["accounts"]
        self._page_header(
            page, "Аккаунты",
            "Telegram-коды и пароль 2FA не сохраняются. Авторизация выполняется локально.",
        )

        tools = tk.Frame(page, bg=BG)
        tools.pack(fill="x", pady=(0, 8))
        self._button(tools, "Добавить", self.add_account, primary=True).pack(side="left", padx=(0, 7))
        self._button(tools, "Изменить", self.edit_account).pack(side="left", padx=(0, 7))
        self._button(tools, "Удалить", self.delete_account, danger=True).pack(side="left", padx=(0, 7))
        self._button(tools, "Сброс истории", self.reset_seen_history).pack(side="left", padx=(0, 7))
        self._button(tools, "Обновить", self._refresh_accounts).pack(side="left")

        table = self._panel(page, padding=0)
        table.pack(fill="both", expand=True)
        columns = ("id", "name", "admin", "mode", "session", "status")
        self.account_tree = ttk.Treeview(table, columns=columns, show="headings", style="Dark.Treeview")
        headings = {
            "id": "ID", "name": "Имя", "admin": "Admin ID",
            "mode": "Режим", "session": "Сессия", "status": "Статус",
        }
        widths = {"id": 52, "name": 170, "admin": 130, "mode": 120, "session": 260, "status": 150}
        for c in columns:
            self.account_tree.heading(c, text=headings[c])
            self.account_tree.column(c, width=widths[c], anchor="w")
        self.account_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.account_tree.yview,
                               style="Dark.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")
        self.account_tree.configure(yscrollcommand=scroll.set)
        self.account_tree.bind("<Double-1>", lambda _e: self.edit_account())

        self.accounts_footer = self._label(page, "Аккаунтов: 0", muted=True, bg=BG)
        self.accounts_footer.pack(anchor="w", pady=(8, 0))

    # ---------------------------- channels ----------------------------

    def _build_channels_page(self):
        page = self.pages["channels"]
        self._page_header(
            page, "Каналы",
            "Автоматическое обнаружение подписок или собственные списки каналов и исключений.",
        )

        option_panel = self._panel(page, padding=10)
        option_panel.pack(fill="x", pady=(0, 10))
        self.auto_discover_var = tk.BooleanVar()
        self.skip_private_var = tk.BooleanVar()
        self._checkbox(option_panel, "Автоматически использовать подписки аккаунта", self.auto_discover_var).pack(
            side="left", padx=(0, 22)
        )
        self._checkbox(option_panel, "Пропускать приватные каналы без username", self.skip_private_var).pack(side="left")

        editors = tk.Frame(page, bg=BG)
        editors.pack(fill="both", expand=True)
        for i in range(3):
            editors.grid_columnconfigure(i, weight=1, uniform="editors")
        editors.grid_rowconfigure(0, weight=1)

        self.channels_text = self._editor_card(editors, 0, "Каналы", "По одному username в строке")
        self.excludes_text = self._editor_card(editors, 1, "Исключения", "Эти каналы никогда не обрабатываются")
        self.keywords_text = self._editor_card(editors, 2, "Ключевые слова", "Пусто = обрабатывать все текстовые посты")

        footer = tk.Frame(page, bg=BG)
        footer.pack(fill="x", pady=(10, 0))
        self._label(footer, "Можно вставлять @username — символ @ будет удалён автоматически.",
                    muted=True, bg=BG).pack(side="left")
        self._button(footer, "Сохранить", self.save_all, primary=True).pack(side="right")

    def _editor_card(self, parent, col, title, hint):
        card = self._panel(parent, padding=10)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 6 if col < 2 else 0))
        self._label(card, title, bold=True, bg=PANEL).pack(anchor="w")
        self._label(card, hint, muted=True, size=8, bg=PANEL).pack(anchor="w", pady=(3, 8))
        text = self._text(card, height=18)
        text.pack(fill="both", expand=True)
        return text

    # ---------------------------- comments / PC control ----------------------------

    def _build_comments_page(self):
        page = self.pages["comments"]
        self._page_header(
            page, "Комментарии",
            "Основная очередь NeuroComment на ПК. Telegram-бот теперь является дополнительной удалённой панелью.",
        )

        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        self.comments_counter = self._label(toolbar, "В очереди: 0", muted=True, bg=BG)
        self.comments_counter.pack(side="left")
        self._button(toolbar, "Обновить", self._refresh_comments_from_disk).pack(side="right")

        table = self._panel(page, padding=0)
        table.pack(fill="x")
        columns = ("account", "channel", "autopilot", "status", "comment")
        self.comments_tree = ttk.Treeview(table, columns=columns, show="headings", height=8, style="Dark.Treeview")
        specs = {
            "account": ("Аккаунт", 120),
            "channel": ("Канал", 190),
            "autopilot": ("Режим", 100),
            "status": ("Статус", 150),
            "comment": ("AI-комментарий", 430),
        }
        for key, (title, width) in specs.items():
            self.comments_tree.heading(key, text=title)
            self.comments_tree.column(key, width=width, anchor="w")
        self.comments_tree.pack(side="left", fill="x", expand=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.comments_tree.yview,
                               style="Dark.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")
        self.comments_tree.configure(yscrollcommand=scroll.set)
        self.comments_tree.bind("<<TreeviewSelect>>", self._on_comment_selected)

        detail = tk.Frame(page, bg=BG)
        detail.pack(fill="both", expand=True, pady=(12, 0))
        detail.grid_columnconfigure(0, weight=1, uniform="comment_detail")
        detail.grid_columnconfigure(1, weight=1, uniform="comment_detail")
        detail.grid_rowconfigure(0, weight=1)

        post_card = self._panel(detail, padding=10)
        post_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._label(post_card, "Пост", bold=True, bg=PANEL).pack(anchor="w", pady=(0, 7))
        self.comment_post_text = self._text(post_card, height=12)
        self.comment_post_text.pack(fill="both", expand=True)
        self.comment_post_text.configure(state="disabled")

        ai_card = self._panel(detail, padding=10)
        ai_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._label(ai_card, "Комментарий", bold=True, bg=PANEL).pack(anchor="w", pady=(0, 7))
        self.comment_ai_text = self._text(ai_card, height=12)
        self.comment_ai_text.pack(fill="both", expand=True)
        self._label(
            ai_card, "Текст можно отредактировать вручную перед публикацией.",
            muted=True, size=8, bg=PANEL,
        ).pack(anchor="w", pady=(6, 0))

        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x", pady=(10, 0))
        self._button(actions, "✅ Опубликовать", self._desktop_publish, primary=True).pack(side="left", padx=(0, 7))
        self._button(actions, "❌ Пропустить", self._desktop_skip, danger=True).pack(side="left", padx=(0, 7))
        self._button(actions, "🔄 Другой вариант", self._desktop_regen).pack(side="left", padx=(0, 7))
        self._button(actions, "💾 Сохранить правку", self._desktop_save_edit).pack(side="left")
        self.comments_hint = self._label(actions, "Выберите пост в таблице.", muted=True, bg=BG)
        self.comments_hint.pack(side="right")

    def _engine_is_running(self):
        return bool(self.engine_process and self.engine_process.poll() is None)

    def _selected_comment_key(self):
        selected = self.comments_tree.selection() if hasattr(self, "comments_tree") else ()
        return str(selected[0]) if selected else ""

    def _refresh_comments_from_disk(self):
        if self._engine_is_running():
            self.desktop_queue = load_desktop_queue()
        else:
            self.desktop_queue = []
        self.desktop_queue_by_key = {
            str(item.get("key")): item for item in self.desktop_queue if item.get("key")
        }
        self._refresh_comments_table()

    def _poll_desktop_queue(self):
        try:
            if self._engine_is_running():
                items = load_desktop_queue()
            else:
                items = []
            signature = tuple(
                (str(x.get("key")), str(x.get("comment")), str(x.get("status")), bool(x.get("autopilot")))
                for x in items
            )
            if signature != self._queue_signature:
                self._queue_signature = signature
                self.desktop_queue = items
                self.desktop_queue_by_key = {
                    str(item.get("key")): item for item in items if item.get("key")
                }
                self._refresh_comments_table()
        except Exception as exc:
            self._append_log(f"[DESKTOP] Не удалось обновить очередь: {exc}\n")
        finally:
            if not self._closing:
                self.after(700, self._poll_desktop_queue)

    def _refresh_comments_table(self):
        if not hasattr(self, "comments_tree"):
            return
        selected = self._selected_comment_key()
        for row in self.comments_tree.get_children():
            self.comments_tree.delete(row)
        for item in self.desktop_queue:
            key = str(item.get("key") or "")
            if not key:
                continue
            comment = str(item.get("comment") or "").replace("\n", " ")
            if len(comment) > 90:
                comment = comment[:87] + "…"
            self.comments_tree.insert(
                "", "end", iid=key,
                values=(
                    item.get("account", ""),
                    item.get("chat_title", ""),
                    "Автопилот" if item.get("autopilot") else "Ручной",
                    item.get("status", "Ожидает"),
                    comment,
                ),
            )
        count = len(self.desktop_queue)
        self.comments_counter.configure(text=f"В очереди: {count}")
        if "comments" in self.nav_buttons:
            self.nav_buttons["comments"].configure(text=f"Комментарии ({count})" if count else "Комментарии")
        if selected and selected in self.desktop_queue_by_key:
            self.comments_tree.selection_set(selected)
            self.comments_tree.focus(selected)
            self._show_comment_item(self.desktop_queue_by_key[selected])
        elif count:
            first = str(self.desktop_queue[0].get("key"))
            self.comments_tree.selection_set(first)
            self.comments_tree.focus(first)
            self._show_comment_item(self.desktop_queue_by_key[first])
        else:
            self._show_comment_item(None)

    def _on_comment_selected(self, _event=None):
        key = self._selected_comment_key()
        self._show_comment_item(self.desktop_queue_by_key.get(key))

    def _set_text_widget(self, widget, value, *, readonly=False):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value or "")
        if readonly:
            widget.configure(state="disabled")

    def _show_comment_item(self, item):
        if not hasattr(self, "comment_post_text"):
            return
        if not item:
            self._set_text_widget(self.comment_post_text, "", readonly=True)
            self._set_text_widget(self.comment_ai_text, "")
            self.comments_hint.configure(text="Выберите пост в таблице.")
            return
        self._set_text_widget(self.comment_post_text, str(item.get("post_text") or ""), readonly=True)
        self._set_text_widget(self.comment_ai_text, str(item.get("comment") or ""))
        self.comments_hint.configure(
            text=f"{item.get('account', '')} • {item.get('chat_title', '')} • {item.get('status', 'Ожидает')}"
        )

    def _ensure_desktop_action_ready(self):
        if not self._engine_is_running():
            messagebox.showinfo("Комментарии", "Сначала запустите движок NeuroComment.", parent=self)
            return ""
        key = self._selected_comment_key()
        if not key:
            messagebox.showinfo("Комментарии", "Выберите пост в очереди.", parent=self)
            return ""
        return key

    def _desktop_publish(self):
        key = self._ensure_desktop_action_ready()
        if not key:
            return
        comment = self.comment_ai_text.get("1.0", "end").strip()
        if not comment:
            return messagebox.showerror("Комментарии", "Комментарий не может быть пустым.", parent=self)
        submit_desktop_command("approve", key, comment=comment)
        self.comments_hint.configure(text="Команда публикации отправлена…")

    def _desktop_skip(self):
        key = self._ensure_desktop_action_ready()
        if not key:
            return
        submit_desktop_command("skip", key)
        self.comments_hint.configure(text="Пост пропускается…")

    def _desktop_regen(self):
        key = self._ensure_desktop_action_ready()
        if not key:
            return
        submit_desktop_command("regen", key)
        self.comments_hint.configure(text="AI генерирует новый вариант…")

    def _desktop_save_edit(self):
        key = self._ensure_desktop_action_ready()
        if not key:
            return
        comment = self.comment_ai_text.get("1.0", "end").strip()
        if not comment:
            return messagebox.showerror("Комментарии", "Комментарий не может быть пустым.", parent=self)
        submit_desktop_command("edit", key, comment=comment)
        self.comments_hint.configure(text="Правка отправлена движку…")

    # ---------------------------- settings ----------------------------

    def _build_settings_page(self):
        page = self.pages["settings"]
        self._page_header(
            page, "Настройки",
            "Подключение Telegram, локальный AI и параметры автоматизации.",
        )

        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 10))
        self.settings_tab_buttons = {}
        self.settings_sections = {}
        for key, title in (("connection", "Подключение"), ("ai", "AI"), ("automation", "Автопилот")):
            b = self._button(toolbar, title, lambda k=key: self._show_settings_section(k))
            b.pack(side="left", padx=(0, 7))
            self.settings_tab_buttons[key] = b

        holder = tk.Frame(page, bg=BG)
        holder.pack(fill="both", expand=True)
        for key in ("connection", "ai", "automation"):
            self.settings_sections[key] = tk.Frame(holder, bg=BG)

        self._build_connection_settings(self.settings_sections["connection"])
        self._build_ai_settings(self.settings_sections["ai"])
        self._build_automation_settings(self.settings_sections["automation"])
        self._show_settings_section("connection")

        bottom = tk.Frame(page, bg=BG)
        bottom.pack(fill="x", pady=(10, 0))
        self._button(bottom, "Открыть папку данных", self._open_data_folder).pack(side="left")
        self._button(bottom, "Импорт из старой версии", self._import_old_data).pack(side="left", padx=(7, 0))
        self._button(bottom, "Сохранить настройки", self.save_all, primary=True).pack(side="right")

    def _show_settings_section(self, key):
        for name, frame in self.settings_sections.items():
            frame.pack_forget()
            btn = self.settings_tab_buttons[name]
            btn.configure(bg=BUTTON, activebackground=BUTTON_HOVER, highlightbackground=BORDER)
        self.settings_sections[key].pack(fill="both", expand=True)
        self.settings_tab_buttons[key].configure(
            bg=ACCENT_DARK, activebackground=ACCENT_HOVER, highlightbackground=ACCENT
        )

    def _build_connection_settings(self, parent):
        api = self._panel(parent, padding=16)
        api.pack(fill="x")
        api.grid_columnconfigure(1, weight=1)
        self._label(api, "Telegram API", bold=True, size=11, bg=PANEL).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        self.api_id_var = tk.StringVar()
        self.api_hash_var = tk.StringVar()
        self.bot_token_var = tk.StringVar()
        self.bot_panel_enabled_var = tk.BooleanVar(value=True)

        self.api_id_entry = self._field_row(api, 1, "API ID", self.api_id_var)
        self.api_hash_entry = self._field_row(api, 2, "API Hash", self.api_hash_var, show="•")
        self._checkbox(
            api, "Использовать Telegram-бота как дополнительную удалённую панель",
            self.bot_panel_enabled_var,
        ).grid(row=3, column=1, sticky="w", pady=(5, 2))
        self.bot_token_entry = self._field_row(
            api, 4, "BOT TOKEN (необязательно для работы с ПК)", self.bot_token_var, show="•"
        )

        self.show_secrets_var = tk.BooleanVar(value=False)
        self._checkbox(api, "Показывать секретные значения", self.show_secrets_var).grid(
            row=5, column=1, sticky="w", pady=(3, 0)
        )
        self.show_secrets_var.trace_add("write", lambda *_: self._toggle_secrets())

    def _build_ai_settings(self, parent):
        provider = self._panel(parent, padding=16)
        provider.pack(fill="x")
        provider.grid_columnconfigure(1, weight=1)
        self._label(provider, "AI-провайдер", bold=True, size=11, bg=PANEL).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        self.ai_provider_var = tk.StringVar()
        self.ollama_url_var = tk.StringVar()
        self.ollama_model_var = tk.StringVar()
        self.openai_api_key_var = tk.StringVar()
        self.openai_base_url_var = tk.StringVar()
        self.openai_model_var = tk.StringVar()

        self._label(provider, "Провайдер", muted=True, bg=PANEL).grid(
            row=1, column=0, sticky="w", padx=(0, 18), pady=7
        )
        provider_combo = ttk.Combobox(
            provider,
            textvariable=self.ai_provider_var,
            values=["Ollama (локально)", "OpenAI API"],
            state="readonly",
            style="Dark.TCombobox",
        )
        provider_combo.grid(row=1, column=1, sticky="ew", pady=7)
        provider_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_ai_provider_ui())

        self.ai_provider_holder = tk.Frame(provider, bg=PANEL)
        self.ai_provider_holder.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.ollama_provider_frame = tk.Frame(self.ai_provider_holder, bg=PANEL)
        self.ollama_provider_frame.grid_columnconfigure(1, weight=1)
        self._field_row(self.ollama_provider_frame, 0, "Адрес Ollama", self.ollama_url_var)
        self._field_row(self.ollama_provider_frame, 1, "Модель", self.ollama_model_var)
        ollama_actions = tk.Frame(self.ollama_provider_frame, bg=PANEL)
        ollama_actions.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._button(ollama_actions, "Проверить Ollama", self.check_ollama).pack(side="left")
        self._button(ollama_actions, "Установить Ollama", self.install_ollama).pack(side="left", padx=(7, 0))
        self._button(ollama_actions, "Скачать модель", self.pull_ollama_model).pack(side="left", padx=(7, 0))
        self.ollama_status = self._label(
            self.ollama_provider_frame, "Не проверено", muted=True, bg=PANEL
        )
        self.ollama_status.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._label(
            self.ollama_provider_frame,
            "Ollama работает локально и бесплатно. Для первого запуска установите Ollama и скачайте модель.",
            muted=True, size=8, bg=PANEL,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.openai_provider_frame = tk.Frame(self.ai_provider_holder, bg=PANEL)
        self.openai_provider_frame.grid_columnconfigure(1, weight=1)
        self.openai_api_key_entry = self._field_row(
            self.openai_provider_frame, 0, "OpenAI API Key", self.openai_api_key_var, show="•"
        )
        self._field_row(self.openai_provider_frame, 1, "Модель", self.openai_model_var)
        self._field_row(self.openai_provider_frame, 2, "API URL", self.openai_base_url_var)
        openai_actions = tk.Frame(self.openai_provider_frame, bg=PANEL)
        openai_actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._button(openai_actions, "Проверить OpenAI", self.check_openai).pack(side="left")
        self.openai_status = self._label(
            self.openai_provider_frame, "Не проверено", muted=True, bg=PANEL
        )
        self.openai_status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._label(
            self.openai_provider_frame,
            "Используется ваш OpenAI API Key. Подписка ChatGPT и баланс OpenAI API — разные вещи.",
            muted=True, size=8, bg=PANEL,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        style_box = self._panel(parent, padding=16)
        style_box.pack(fill="both", expand=True, pady=(12, 0))
        style_box.grid_columnconfigure(1, weight=1)
        self._label(style_box, "Стиль комментариев", bold=True, size=11, bg=PANEL).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        self.persona_var = tk.StringVar()
        self.tone_var = tk.StringVar()
        self.emoji_var = tk.StringVar()
        self._field_row(style_box, 1, "Роль / образ", self.persona_var)

        self._label(style_box, "Тон", muted=True, bg=PANEL).grid(
            row=2, column=0, sticky="w", padx=(0, 18), pady=7
        )
        tone = ttk.Combobox(
            style_box, textvariable=self.tone_var,
            values=["Дружелюбный", "Разговорный", "Нейтральный", "Серьёзный", "Ироничный"],
            state="readonly", style="Dark.TCombobox",
        )
        tone.grid(row=2, column=1, sticky="ew", pady=7)

        self._label(style_box, "Эмодзи", muted=True, bg=PANEL).grid(
            row=3, column=0, sticky="w", padx=(0, 18), pady=7
        )
        emoji = ttk.Combobox(
            style_box, textvariable=self.emoji_var,
            values=["Никогда", "Иногда", "Часто"], state="readonly", style="Dark.TCombobox",
        )
        emoji.grid(row=3, column=1, sticky="ew", pady=7)

        self._label(style_box, "Дополнительный промпт", muted=True, bg=PANEL).grid(
            row=4, column=0, sticky="nw", padx=(0, 18), pady=7
        )
        self.prompt_text = self._text(style_box, height=10)
        self.prompt_text.grid(row=4, column=1, sticky="nsew", pady=7)
        style_box.grid_rowconfigure(4, weight=1)
        self._label(
            style_box,
            "Выбранный тон теперь задаёт отдельные правила генерации. Фильтр трагических тем остаётся активным.",
            muted=True, bg=PANEL,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _provider_key(self):
        value = str(self.ai_provider_var.get() or "").lower()
        return "openai" if "openai" in value else "ollama"

    def _refresh_ai_provider_ui(self):
        if not hasattr(self, "ollama_provider_frame"):
            return
        self.ollama_provider_frame.pack_forget()
        self.openai_provider_frame.pack_forget()
        if self._provider_key() == "openai":
            self.openai_provider_frame.pack(fill="x")
        else:
            self.ollama_provider_frame.pack(fill="x")
        if hasattr(self, "home_ai_value"):
            self._update_home_stats()

    def _build_automation_settings(self, parent):
        box = self._panel(parent, padding=16)
        box.pack(fill="x")
        box.grid_columnconfigure(1, weight=1)
        self._label(box, "Автоматизация", bold=True, size=11, bg=PANEL).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        self.autopilot_delay_var = tk.StringVar()
        self.ai_interval_var = tk.StringVar()
        self.batch_size_var = tk.StringVar()
        self.rotate_minutes_var = tk.StringVar()
        self.discover_hours_var = tk.StringVar()
        self.backfill_var = tk.StringVar()
        self.inactive_days_var = tk.StringVar()
        self.auto_leave_banned_var = tk.BooleanVar()

        self._field_row(box, 1, "Задержка автопубликации, сек", self.autopilot_delay_var, width=18)
        self._field_row(box, 2, "Интервал AI-запросов, сек", self.ai_interval_var, width=18)
        self._field_row(box, 3, "Каналов в одной группе", self.batch_size_var, width=18)
        self._field_row(box, 4, "Ротация групп, минут", self.rotate_minutes_var, width=18)
        self._field_row(box, 5, "Обновление списка, часов", self.discover_hours_var, width=18)
        self._field_row(box, 6, "Backfill: постов на канал", self.backfill_var, width=18)
        self._field_row(box, 7, "Покидать неактивные через, дней", self.inactive_days_var, width=18)
        self._checkbox(
            box, "Автоматически покидать каналы при подтверждённом бане", self.auto_leave_banned_var
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

    # ---------------------------- logs ----------------------------

    def _build_logs_page(self):
        page = self.pages["logs"]
        header = self._page_header(
            page, "Логи",
            "Технический журнал NeuroComment и сообщения движка в реальном времени.",
        )
        self._button(header, "Очистить", self._clear_logs).pack(side="right", anchor="e")

        holder = self._panel(page, padding=0)
        holder.pack(fill="both", expand=True)
        self.log_text = self._text(holder, height=28, wrap="none", font=("Consolas", 9))
        self.log_text.configure(state="disabled", highlightthickness=0)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll_y = ttk.Scrollbar(holder, orient="vertical", command=self.log_text.yview,
                                 style="Dark.Vertical.TScrollbar")
        scroll_y.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll_y.set)

    # ---------------------------- about ----------------------------

    def _build_about_page(self):
        page = self.pages["about"]
        self._page_header(page, "О программе", "Коммерческая desktop-оболочка NeuroComment.")

        card = self._panel(page, padding=22)
        card.pack(fill="x")
        top = tk.Frame(card, bg=PANEL)
        top.pack(fill="x")
        badge = tk.Frame(top, bg=PANEL_ALT, width=72, height=72,
                         highlightbackground=ACCENT, highlightthickness=1)
        badge.pack(side="left", padx=(0, 18))
        badge.pack_propagate(False)
        tk.Label(badge, text="NC", bg=PANEL_ALT, fg=ACCENT,
                 font=("Segoe UI", 22, "bold")).pack(expand=True)
        info = tk.Frame(top, bg=PANEL)
        info.pack(side="left", fill="x", expand=True)
        self._label(info, "NeuroComment", bold=True, size=18, bg=PANEL).pack(anchor="w")
        self._label(info, f"Версия {APP_VERSION}", muted=True, bg=PANEL).pack(anchor="w", pady=(4, 0))
        self._label(info, "AI-комментирование Telegram с выбором Ollama или OpenAI API.",
                    muted=True, bg=PANEL).pack(anchor="w", pady=(8, 0))

        tk.Frame(card, bg=BORDER_SOFT, height=1).pack(fill="x", pady=18)
        self._label(card, "Возможности", bold=True, bg=PANEL).pack(anchor="w")
        features = (
            "• Мультиаккаунт и QR-авторизация\n"
            "• Мониторинг каналов и ротация\n"
            "• Генерация через Ollama или OpenAI API\n"
            "• Ручное подтверждение и автопилот\n"
            "• Обработка ограничений, FloodWait и банов"
        )
        tk.Label(card, text=features, bg=PANEL, fg=MUTED, justify="left",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))

    # ---------------------------- state / data ----------------------------

    def _load_into_ui(self):
        s = self.settings
        self.api_id_var.set(str(s.get("telegram_api_id") or ""))
        self.api_hash_var.set(str(s.get("telegram_api_hash") or ""))
        self.bot_token_var.set(str(s.get("bot_token") or ""))
        self.bot_panel_enabled_var.set(bool(s.get("bot_panel_enabled", True)))
        self.ai_provider_var.set(
            "OpenAI API" if str(s.get("ai_provider") or "ollama").lower() == "openai"
            else "Ollama (локально)"
        )
        self.ollama_url_var.set(str(s.get("ollama_base_url") or "http://127.0.0.1:11434"))
        self.ollama_model_var.set(str(s.get("ollama_model") or "qwen3:8b"))
        self.openai_api_key_var.set(str(s.get("openai_api_key") or ""))
        self.openai_base_url_var.set(str(s.get("openai_base_url") or "https://api.openai.com/v1"))
        self.openai_model_var.set(str(s.get("openai_model") or "gpt-5.6-luna"))
        self.auto_discover_var.set(bool(s.get("auto_discover", True)))
        self.skip_private_var.set(bool(s.get("skip_private_channels", True)))
        self.persona_var.set(str(s.get("ai_persona") or "Обычный пользователь Telegram"))
        self.tone_var.set(str(s.get("ai_tone") or "Дружелюбный"))
        self.emoji_var.set(str(s.get("ai_emoji_mode") or "Иногда"))
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", str(s.get("ai_custom_prompt") or ""))

        self.channels_text.delete("1.0", "end")
        self.channels_text.insert("1.0", "\n".join(s.get("channels") or []))
        self.excludes_text.delete("1.0", "end")
        self.excludes_text.insert("1.0", "\n".join(s.get("exclude_channels") or []))
        self.keywords_text.delete("1.0", "end")
        self.keywords_text.insert("1.0", "\n".join(s.get("keywords") or []))

        self.autopilot_delay_var.set(str(s.get("autopilot_delay", 90)))
        self.ai_interval_var.set(str(s.get("ai_request_interval", 10)))
        self.batch_size_var.set(str(s.get("batch_size", 50)))
        self.rotate_minutes_var.set(str(max(0, int(s.get("rotate_interval", 1800)) // 60)))
        self.discover_hours_var.set(str(max(0, int(s.get("discover_interval", 21600)) // 3600)))
        self.backfill_var.set(str(s.get("backfill_limit", 3)))
        self.inactive_days_var.set(str(s.get("auto_leave_inactive_days", 30)))
        self.auto_leave_banned_var.set(bool(s.get("auto_leave_banned_channels", True)))
        self._refresh_ai_provider_ui()
        self._refresh_accounts()
        self._update_home_stats()

    def _collect_settings(self) -> dict:
        s = dict(self.settings)
        s.update({
            "telegram_api_id": _as_int(self.api_id_var.get(), 0),
            "telegram_api_hash": self.api_hash_var.get().strip(),
            "bot_token": self.bot_token_var.get().strip(),
            "bot_panel_enabled": bool(self.bot_panel_enabled_var.get()),
            "ai_provider": self._provider_key(),
            "ollama_base_url": self.ollama_url_var.get().strip() or "http://127.0.0.1:11434",
            "ollama_model": self.ollama_model_var.get().strip() or "qwen3:8b",
            "openai_api_key": self.openai_api_key_var.get().strip(),
            "openai_base_url": self.openai_base_url_var.get().strip() or "https://api.openai.com/v1",
            "openai_model": self.openai_model_var.get().strip() or "gpt-5.6-luna",
            "auto_discover": bool(self.auto_discover_var.get()),
            "skip_private_channels": bool(self.skip_private_var.get()),
            "channels": _split_lines(self.channels_text.get("1.0", "end")),
            "exclude_channels": _split_lines(self.excludes_text.get("1.0", "end")),
            "keywords": [x.strip() for x in self.keywords_text.get("1.0", "end").splitlines() if x.strip()],
            "ai_persona": self.persona_var.get().strip() or "Обычный пользователь Telegram",
            "ai_tone": self.tone_var.get().strip() or "Дружелюбный",
            "ai_emoji_mode": self.emoji_var.get().strip() or "Иногда",
            "ai_custom_prompt": self.prompt_text.get("1.0", "end").strip(),
            "autopilot_delay": max(10, _as_int(self.autopilot_delay_var.get(), 90)),
            "ai_request_interval": max(1, _as_int(self.ai_interval_var.get(), 10)),
            "batch_size": max(1, _as_int(self.batch_size_var.get(), 50)),
            "rotate_interval": max(0, _as_int(self.rotate_minutes_var.get(), 30)) * 60,
            "discover_interval": max(0, _as_int(self.discover_hours_var.get(), 6)) * 3600,
            "backfill_limit": max(0, _as_int(self.backfill_var.get(), 3)),
            "auto_leave_inactive_days": max(0, _as_int(self.inactive_days_var.get(), 30)),
            "auto_leave_banned_channels": bool(self.auto_leave_banned_var.get()),
            "telegram_login_mode": "qr",
        })
        return s

    def _validate(self, settings=None, show=True) -> bool:
        s = settings or self._collect_settings()
        errors = []
        if int(s.get("telegram_api_id") or 0) <= 0:
            errors.append("• Укажите Telegram API ID.")
        if not str(s.get("telegram_api_hash") or "").strip():
            errors.append("• Укажите Telegram API Hash.")
        bot_enabled = bool(s.get("bot_panel_enabled", True))
        if bot_enabled and not str(s.get("bot_token") or "").strip():
            errors.append("• Для дополнительной Telegram BotPanel укажите BOT TOKEN или выключите её.")
        provider = str(s.get("ai_provider") or "ollama").lower()
        if provider == "openai":
            if not str(s.get("openai_api_key") or "").strip():
                errors.append("• Для OpenAI укажите API Key.")
            if not str(s.get("openai_model") or "").strip():
                errors.append("• Для OpenAI укажите модель.")
        else:
            if not str(s.get("ollama_base_url") or "").strip():
                errors.append("• Укажите адрес Ollama.")
            if not str(s.get("ollama_model") or "").strip():
                errors.append("• Укажите модель Ollama.")
        if not self.accounts:
            errors.append("• Добавьте хотя бы один Telegram-аккаунт.")
        if bot_enabled:
            for acc in self.accounts:
                if _as_int(acc.get("admin_id"), 0) <= 0:
                    errors.append(f"• Для BotPanel у аккаунта {acc.get('name', '?')} нужен Admin ID.")
        if errors and show:
            messagebox.showerror("Не хватает настроек", "\n".join(errors), parent=self)
        return not errors

    def save_all(self, silent=False):
        self.settings = self._collect_settings()
        save_settings(self.settings)
        save_accounts(self.accounts)
        self._update_home_stats()
        if not silent:
            messagebox.showinfo("NeuroComment", "Настройки сохранены.", parent=self)
        return True

    def _toggle_secrets(self):
        show = "" if self.show_secrets_var.get() else "•"
        self.api_hash_entry.configure(show=show)
        self.bot_token_entry.configure(show=show)
        if hasattr(self, "openai_api_key_entry"):
            self.openai_api_key_entry.configure(show=show)

    def _refresh_accounts(self):
        if not hasattr(self, "account_tree"):
            return
        for item in self.account_tree.get_children():
            self.account_tree.delete(item)
        for index, acc in enumerate(self.accounts):
            mode = "Автопилот" if acc.get("autopilot") else "Ручной"
            self.account_tree.insert(
                "", "end", iid=str(index),
                values=(index + 1, acc.get("name", ""), acc.get("admin_id", ""), mode,
                        acc.get("session", ""), "Готов"),
            )
        self.accounts_footer.configure(text=f"Аккаунтов: {len(self.accounts)}")
        self._update_home_stats()

    def _update_home_stats(self):
        if not hasattr(self, "home_accounts_value"):
            return
        try:
            s = self._collect_settings()
        except Exception:
            s = self.settings
        accounts_count = len(self.accounts)
        channel_count = len(_split_lines(self.channels_text.get("1.0", "end"))) if hasattr(self, "channels_text") else 0
        autopilot_count = sum(1 for a in self.accounts if a.get("autopilot"))

        self.home_accounts_value.configure(text=str(accounts_count))
        self.home_channels_value.configure(text=("Авто" if bool(s.get("auto_discover", True)) else str(channel_count)))
        self.home_autopilot_value.configure(text=f"{autopilot_count}/{accounts_count}" if accounts_count else "0")

        api_ok = int(s.get("telegram_api_id") or 0) > 0 and bool(str(s.get("telegram_api_hash") or "").strip())
        bot_enabled = bool(s.get("bot_panel_enabled", True))
        bot_token_ok = bool(str(s.get("bot_token") or "").strip())
        bot_ok = (not bot_enabled) or bot_token_ok
        acc_ok = bool(self.accounts)
        provider = str(s.get("ai_provider") or "ollama").lower()
        if provider == "openai":
            ai_cfg = bool(
                str(s.get("openai_api_key") or "").strip()
                and str(s.get("openai_model") or "").strip()
            )
            ai_checked = self._openai_ok
            ai_label = "OpenAI: готов" if ai_checked else "OpenAI: не проверен"
        else:
            ai_cfg = bool(
                str(s.get("ollama_base_url") or "").strip()
                and str(s.get("ollama_model") or "").strip()
            )
            ai_checked = self._ollama_ok
            ai_label = "Ollama: готов" if ai_checked else "Ollama: не проверена"

        values = [api_ok, bot_ok, acc_ok, ai_cfg, ai_checked]
        done = sum(1 for x in values if x)
        self.ready_progress.configure(value=done)
        self.ready_text.configure(text=f"{done} / 5")
        self.home_api_value.configure(text="Готов" if api_ok else "Ожидание", fg=GREEN if api_ok else TEXT)
        if not bot_enabled:
            self.home_bot_value.configure(text="Выключена", fg=MUTED)
        else:
            self.home_bot_value.configure(
                text="Готов" if bot_token_ok else "Ожидание",
                fg=GREEN if bot_token_ok else TEXT,
            )
        self.home_ai_value.configure(text=ai_label, fg=GREEN if ai_checked else TEXT)

    # ---------------------------- accounts actions ----------------------------

    def add_account(self):
        dlg = AccountDialog(self, used_names=[a.get("name", "") for a in self.accounts])
        self.wait_window(dlg)
        if dlg.result:
            self.accounts.append(dlg.result)
            self._refresh_accounts()

    def edit_account(self):
        selected = self.account_tree.selection()
        if not selected:
            return messagebox.showinfo("Аккаунты", "Выберите аккаунт.", parent=self)
        index = int(selected[0])
        dlg = AccountDialog(
            self,
            initial=self.accounts[index],
            used_names=[a.get("name", "") for a in self.accounts],
        )
        self.wait_window(dlg)
        if dlg.result:
            self.accounts[index] = dlg.result
            self._refresh_accounts()

    def delete_account(self):
        selected = self.account_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        name = self.accounts[index].get("name", "аккаунт")
        if messagebox.askyesno(
            "Удалить аккаунт",
            f"Удалить «{name}» из настроек?\n\nФайл Telegram-сессии на диске не удаляется автоматически.",
            parent=self,
        ):
            self.accounts.pop(index)
            self._refresh_accounts()

    def reset_seen_history(self):
        """Clear persisted processed-post history for the selected account.

        This is intentionally manual: clearing it can make recent posts appear
        in backfill again, which is useful after migration/testing but should
        never happen silently in a commercial build.
        """
        selected = self.account_tree.selection()
        if not selected:
            return messagebox.showinfo("История", "Выберите аккаунт.", parent=self)
        if self._engine_is_running():
            return messagebox.showwarning(
                "История",
                "Сначала остановите NeuroComment. Историю нельзя сбрасывать во время работы движка.",
                parent=self,
            )
        index = int(selected[0])
        name = str(self.accounts[index].get("name") or "").strip()
        if not name:
            return
        if not messagebox.askyesno(
            "Сбросить историю обработанных постов",
            f"Сбросить историю для «{name}»?\n\n"
            "При следующем запуске последние посты могут снова попасть в обработку. "
            "Telegram-сессия, каналы и настройки не удаляются.",
            parent=self,
        ):
            return
        path = account_dir(name) / "seen_messages.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[]\n", encoding="utf-8")
        except Exception as exc:
            return messagebox.showerror("История", f"Не удалось сбросить историю:\n{exc}", parent=self)
        messagebox.showinfo(
            "История",
            "История обработанных постов очищена. Теперь запустите NeuroComment снова.",
            parent=self,
        )

    # ---------------------------- AI provider / engine ----------------------------

    def check_ai_provider(self):
        if self._provider_key() == "openai":
            self.check_openai()
        else:
            self.check_ollama()

    def check_ollama(self):
        self.ollama_status.configure(text="Проверяю…", fg=MUTED)
        self.home_ai_value.configure(text="Проверяю…", fg=TEXT)
        url = self.ollama_url_var.get().strip().rstrip("/") + "/api/tags"
        wanted = self.ollama_model_var.get().strip()

        def worker():
            ok = False
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                names = {str(x.get("name", "")) for x in data.get("models", [])}
                if wanted in names:
                    result = f"✓ Ollama работает, {wanted} установлена"
                    ok = True
                else:
                    result = f"Ollama работает, но {wanted} не найдена"
            except Exception as exc:
                result = f"✕ Нет соединения: {exc}"

            def apply():
                self._ollama_ok = ok
                self.ollama_status.configure(text=result, fg=GREEN if ok else RED)
                self._update_home_stats()

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def check_openai(self):
        self.openai_status.configure(text="Проверяю…", fg=MUTED)
        self.home_ai_value.configure(text="Проверяю…", fg=TEXT)
        key = self.openai_api_key_var.get().strip()
        model = self.openai_model_var.get().strip()
        base = self.openai_base_url_var.get().strip().rstrip("/") or "https://api.openai.com/v1"

        if not key or not model:
            self._openai_ok = False
            self.openai_status.configure(text="Укажите API Key и модель", fg=RED)
            self._update_home_stats()
            return

        def worker():
            ok = False
            try:
                model_id = urllib.parse.quote(model, safe="")
                request = urllib.request.Request(
                    f"{base}/models/{model_id}",
                    headers={"Authorization": f"Bearer {key}"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    response.read()
                result = f"✓ OpenAI работает, модель {model} доступна"
                ok = True
            except Exception as exc:
                result = f"✕ OpenAI: {exc}"

            def apply():
                self._openai_ok = ok
                self.openai_status.configure(text=result, fg=GREEN if ok else RED)
                self._update_home_stats()

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def install_ollama(self):
        if os.name != "nt":
            return messagebox.showinfo(
                "Ollama",
                "Автоматическая установка подготовлена для Windows. Откройте сайт Ollama вручную.",
                parent=self,
            )
        if not messagebox.askyesno(
            "Установить Ollama",
            "Открыть официальную страницу загрузки Ollama для Windows?\n\n"
            "После установки вернитесь в NeuroComment и нажмите «Скачать модель».",
            parent=self,
        ):
            return
        try:
            os.startfile("https://ollama.com/download/windows")  # type: ignore[attr-defined]
        except Exception:
            import webbrowser
            webbrowser.open("https://ollama.com/download/windows")

    def _find_ollama_exe(self):
        exe = shutil.which("ollama")
        if exe:
            return exe
        if os.name == "nt":
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return ""

    def pull_ollama_model(self):
        model = self.ollama_model_var.get().strip() or "qwen3:8b"
        exe = self._find_ollama_exe()
        if not exe:
            return messagebox.showwarning(
                "Ollama",
                "Ollama не найдена. Сначала нажмите «Установить Ollama» и завершите установку.",
                parent=self,
            )
        if not messagebox.askyesno(
            "Скачать модель",
            f"Скачать модель {model}?\n\nДля qwen3:8b требуется несколько гигабайт свободного места.",
            parent=self,
        ):
            return

        self.ollama_status.configure(text=f"Скачиваю {model}…", fg=MUTED)
        self._append_log(f"\n[OLLAMA] Начинаю загрузку модели {model}…\n")

        def worker():
            try:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                proc = subprocess.Popen(
                    [exe, "pull", model],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                if proc.stdout:
                    for line in proc.stdout:
                        self.after(0, self._append_log, "[OLLAMA] " + line)
                code = proc.wait()
                if code != 0:
                    raise RuntimeError(f"ollama pull завершился с кодом {code}")
                self.after(0, self.check_ollama)
            except Exception as exc:
                self.after(
                    0,
                    lambda: self.ollama_status.configure(
                        text=f"✕ Ошибка загрузки: {exc}", fg=RED
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _engine_command(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--engine"]
        return [sys.executable, str(Path(__file__).resolve()), "--engine"]

    def _missing_runtime_dependencies(self):
        """Возвращает отсутствующие Python-зависимости в режиме исходников."""
        if getattr(sys, "frozen", False):
            return []
        required = {
            "telethon": "telethon",
            "python-dotenv": "dotenv",
            "qrcode": "qrcode",
            "Pillow": "PIL",
        }
        if bool(self.bot_panel_enabled_var.get()):
            required["python-telegram-bot"] = "telegram"
        return [package for package, module in required.items() if importlib.util.find_spec(module) is None]

    def start_engine(self):
        if self.engine_process and self.engine_process.poll() is None:
            return

        missing = self._missing_runtime_dependencies()
        if missing:
            names = ", ".join(missing)
            messagebox.showerror(
                "Не установлены зависимости",
                "Не хватает компонентов: " + names + "\n\n"
                "Откройте PowerShell в папке NeuroComment и выполните:\n"
                "python -m pip install -r requirements.txt",
                parent=self,
            )
            self._append_log(f"[DESKTOP] Не установлены зависимости: {names}\n")
            return

        settings = self._collect_settings()
        if not self._validate(settings, show=True):
            return
        provider = str(settings.get("ai_provider") or "ollama").lower()
        checked = self._openai_ok if provider == "openai" else self._ollama_ok
        if not checked:
            label = "OpenAI" if provider == "openai" else "Ollama"
            if not messagebox.askyesno(
                "AI не проверен",
                f"{label} ещё не прошёл проверку подключения. Запустить NeuroComment всё равно?",
                parent=self,
            ):
                self.show_page("settings")
                self._show_settings_section("ai")
                return

        self.settings = settings
        save_settings(settings)
        save_accounts(self.accounts)
        self._append_log("\n=== Запуск NeuroComment ===\n")
        self.show_page("logs")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["NEUROCOMMENT_DATA_DIR"] = str(runtime_dir())
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._stop_requested = False
        try:
            self.engine_process = subprocess.Popen(
                self._engine_command(),
                cwd=str(source_dir()),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.engine_process = None
            return messagebox.showerror("Запуск", f"Не удалось запустить NeuroComment:\n{exc}", parent=self)

        self._update_status(True)
        threading.Thread(target=self._read_engine_output, daemon=True).start()

    def _read_engine_output(self):
        proc = self.engine_process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self.after(0, self._append_log, line)
        finally:
            code = proc.wait()
            self.after(0, self._engine_finished, code)

    def _engine_finished(self, code):
        intentional = self._stop_requested or self._closing
        self._stop_requested = False
        self._append_log(f"\n=== Движок остановлен (код {code}) ===\n")
        self.engine_process = None
        self._update_status(False)
        if not intentional and code != 0:
            messagebox.showwarning(
                "NeuroComment",
                f"Движок неожиданно завершился с кодом {code}. Смотрите раздел «Логи».",
                parent=self,
            )

    def stop_engine(self):
        proc = self.engine_process
        if proc is None or proc.poll() is not None:
            self.engine_process = None
            self._update_status(False)
            return
        self._append_log("\n[DESKTOP] Останавливаю движок…\n")
        self._stop_requested = True
        try:
            proc.terminate()
        except Exception as exc:
            self._append_log(f"[DESKTOP] Ошибка остановки: {exc}\n")

    def _update_status(self, running: bool):
        if running:
            self.sidebar_status.configure(text="● Движок работает", fg=GREEN)
            self.status_dot.configure(fg=GREEN)
            self.bottom_status.configure(text="Движок: работает  •  NeuroComment активен")
            self.home_status_value.configure(text="Работает", fg=GREEN)
            self.home_engine_value.configure(text="Работает", fg=GREEN)
            self.home_start_btn.configure(state="disabled")
            self.home_stop_btn.configure(state="normal")
        else:
            self.sidebar_status.configure(text="● Движок остановлен", fg=MUTED)
            self.status_dot.configure(fg=MUTED_2)
            self.bottom_status.configure(text="Движок: остановлен  •  Ожидание запуска")
            self.home_status_value.configure(text="Остановлен", fg=TEXT)
            if hasattr(self, "ai_provider_var") and self._provider_key() == "openai":
                model = self.openai_model_var.get().strip()
                engine_text = f"OpenAI / {model}" if model else "OpenAI"
            else:
                model = self.ollama_model_var.get().strip() if hasattr(self, "ollama_model_var") else ""
                engine_text = f"Ollama / {model}" if model else "Ollama"
            self.home_engine_value.configure(text=engine_text or "Ожидание", fg=TEXT)
            self.home_start_btn.configure(state="normal")
            self.home_stop_btn.configure(state="disabled")
            if hasattr(self, "comments_tree"):
                self.desktop_queue = []
                self.desktop_queue_by_key = {}
                self._queue_signature = ()
                self._refresh_comments_table()
        self._update_home_stats()

    # ---------------------------- logs / filesystem ----------------------------

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_logs(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _import_old_data(self):
        if self.engine_process and self.engine_process.poll() is None:
            return messagebox.showwarning(
                "Импорт данных",
                "Сначала остановите движок NeuroComment.",
                parent=self,
            )

        selected = filedialog.askdirectory(
            parent=self,
            title="Выберите папку предыдущей версии NeuroComment",
            mustexist=True,
        )
        if not selected:
            return
        if not messagebox.askyesno(
            "Импорт данных",
            "Будут импортированы настройки, аккаунты, Telegram-сессии и служебные данные "
            "из выбранной старой версии.\n\nТекущие данные перед импортом будут сохранены в резервную копию.\n\nПродолжить?",
            parent=self,
        ):
            return
        try:
            src, backup = import_legacy_data(selected)
            self.settings = load_settings()
            self.accounts = load_accounts()
            self._load_into_ui()
            backup_text = f"\nРезервная копия: {backup}" if backup else ""
            messagebox.showinfo(
                "Импорт завершён",
                f"Данные импортированы из:\n{src}\n\nПостоянная папка данных:\n{runtime_dir()}{backup_text}",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("Импорт данных", str(exc), parent=self)

    def _open_data_folder(self):
        path = runtime_dir()
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showinfo("Папка данных", f"{path}\n\n{exc}", parent=self)

    def _on_close(self):
        self._closing = True
        self.save_all(silent=True)
        proc = self.engine_process
        if proc and proc.poll() is None:
            if not messagebox.askyesno(
                "Закрыть NeuroComment",
                "Движок сейчас работает. Остановить его и закрыть программу?",
                parent=self,
            ):
                self._closing = False
                return
            self.stop_engine()
        self.destroy()


def run_desktop() -> None:
    app = NeuroCommentDesktop()
    app.mainloop()


if __name__ == "__main__":
    if "--engine" in sys.argv:
        run_engine_mode()
    else:
        run_desktop()