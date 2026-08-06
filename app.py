from __future__ import annotations

import queue
import random
import re
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from playwright.sync_api import sync_playwright

from browser_manager import BrowserManager, LoginRequiredError
from gmail_ui import GmailUiAutomator
from login_browser import LoginBrowserError, open_normal_login_browser
from models import BrowserProfile, Recipient
from profile_store import ProfileStore, assign_profiles_evenly, normalize_provider
from proton_ui import ProtonUiAutomator
from storage import HistoryStore
from template_engine import compose_message, load_recipients, load_templates

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "history.sqlite3"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
DEFAULT_TEMPLATES = APP_DIR / "templates.json"
DEFAULT_RECIPIENTS = APP_DIR / "recipients_sample.csv"

CHANNEL_LABELS = {
    "Google Chrome": "chrome",
    "Microsoft Edge": "msedge",
    "Playwright Chromium": "chromium",
}


class MailerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Playwright UI Mailer (Proton + Gmail)")
        self.geometry("1180x790")
        self.minsize(1000, 680)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.profile_store = ProfileStore(DATA_DIR)
        self.history = HistoryStore(DB_PATH)
        self.profiles: list[BrowserProfile] = []
        self.worker: threading.Thread | None = None
        self.profile_window_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self._build_ui()
        self._refresh_profiles()
        self._refresh_history()
        self.after(150, self._drain_events)

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        self.profiles_tab = ttk.Frame(notebook, padding=14)
        self.campaign_tab = ttk.Frame(notebook, padding=14)
        self.history_tab = ttk.Frame(notebook, padding=14)
        notebook.add(self.profiles_tab, text="1. Browser profiles")
        notebook.add(self.campaign_tab, text="2. Campaign")
        notebook.add(self.history_tab, text="3. History")

        self._build_profiles_tab()
        self._build_campaign_tab()
        self._build_history_tab()

    def _build_profiles_tab(self) -> None:
        tab = self.profiles_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)

        ttk.Label(
            tab,
            text=(
                "Create one separate browser profile for each authorized Proton Mail or Gmail account. "
                "The program never asks for your password: sign in manually in the visible browser window. "
                "Use the mailbox UI in English so the controls can be located reliably."
            ),
            wraplength=1020,
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        browser_row = ttk.Frame(tab)
        browser_row.grid(row=1, column=0, sticky="w", pady=(0, 10))
        ttk.Label(browser_row, text="Browser:").pack(side="left")
        self.browser_label = tk.StringVar(value="Google Chrome")
        ttk.Combobox(
            browser_row,
            textvariable=self.browser_label,
            values=tuple(CHANNEL_LABELS.keys()),
            width=22,
            state="readonly",
        ).pack(side="left", padx=(6, 16))
        ttk.Label(
            browser_row,
            text="Chrome or Edge is used for manual login. Playwright Chromium is only for campaign automation.",
        ).pack(side="left")

        buttons = ttk.Frame(tab)
        buttons.grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Button(buttons, text="Add browser profile", command=self._add_profile).pack(side="left")
        ttk.Button(buttons, text="Open normal browser to sign in", command=self._open_selected_profile).pack(side="left", padx=8)
        ttk.Button(buttons, text="Remove selected", command=self._remove_selected_profile).pack(side="left")
        ttk.Button(buttons, text="Refresh", command=self._refresh_profiles).pack(side="left", padx=8)

        columns = ("label", "provider", "email", "folder")
        self.profile_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        self.profile_tree.heading("label", text="Profile name")
        self.profile_tree.heading("provider", text="Provider")
        self.profile_tree.heading("email", text="Expected address")
        self.profile_tree.heading("folder", text="Local browser-data folder")
        self.profile_tree.column("label", width=160, anchor="w")
        self.profile_tree.column("provider", width=100, anchor="w")
        self.profile_tree.column("email", width=240, anchor="w")
        self.profile_tree.column("folder", width=560, anchor="w")
        self.profile_tree.grid(row=3, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.profile_tree.yview)
        scroll.grid(row=3, column=1, sticky="ns")
        self.profile_tree.configure(yscrollcommand=scroll.set)

        ttk.Label(
            tab,
            text=(
                "Close any manually opened profile window before starting a campaign. Browser cookies and login "
                "sessions are stored locally under data\\browser_profiles; protect this folder."
            ),
            wraplength=1020,
        ).grid(row=4, column=0, sticky="w", pady=(12, 0))

    def _build_campaign_tab(self) -> None:
        tab = self.campaign_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(12, weight=1)

        self.recipient_path = tk.StringVar(value=str(DEFAULT_RECIPIENTS))
        self.template_path = tk.StringVar(value=str(DEFAULT_TEMPLATES))
        self.campaign_name = tk.StringVar(value="approved-ui-mail")
        self.mode = tk.StringVar(value="Drafts")
        self.profile_strategy = tk.StringVar(value="Fixed profile")
        self.fixed_profile = tk.StringVar()
        self.local_cap = tk.IntVar(value=20)
        self.delay_seconds = tk.DoubleVar(value=30.0)
        self.confirmed = tk.BooleanVar(value=False)
        self.close_browsers = tk.BooleanVar(value=True)
        self.unsubscribe_text = tk.StringVar(
            value='To stop receiving these messages, reply with "unsubscribe."'
        )

        row = 0
        self._path_row(tab, row, "Recipients CSV:", self.recipient_path, self._browse_recipients)
        row += 1
        self._path_row(tab, row, "Templates JSON:", self.template_path, self._browse_templates)
        row += 1

        ttk.Label(tab, text="Campaign ID:").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(tab, textvariable=self.campaign_name).grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
        row += 1

        options = ttk.Frame(tab)
        options.grid(row=row, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Label(options, text="Mode:").pack(side="left")
        ttk.Combobox(
            options,
            textvariable=self.mode,
            values=("Drafts", "Send"),
            width=12,
            state="readonly",
        ).pack(side="left", padx=(5, 18))
        ttk.Label(options, text="Sender selection:").pack(side="left")
        strategy_box = ttk.Combobox(
            options,
            textvariable=self.profile_strategy,
            values=("Fixed profile", "Split across profiles", "CSV sender_profile"),
            width=22,
            state="readonly",
        )
        strategy_box.pack(side="left", padx=(5, 18))
        strategy_box.bind("<<ComboboxSelected>>", lambda _event: self._update_strategy_state())
        ttk.Label(options, text="Fixed profile:").pack(side="left")
        self.fixed_profile_box = ttk.Combobox(
            options,
            textvariable=self.fixed_profile,
            width=32,
            state="readonly",
        )
        self.fixed_profile_box.pack(side="left", padx=(5, 0))
        row += 1

        limits = ttk.Frame(tab)
        limits.grid(row=row, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(limits, text="Local cap per profile (rolling 24h):").pack(side="left")
        ttk.Spinbox(limits, from_=1, to=100, textvariable=self.local_cap, width=7).pack(side="left", padx=(5, 20))
        ttk.Label(limits, text="Max random delay after each operation (seconds, min 15):").pack(side="left")
        ttk.Spinbox(limits, from_=16, to=3600, increment=5, textvariable=self.delay_seconds, width=8).pack(side="left", padx=(5, 0))
        row += 1

        ttk.Label(tab, text="Opt-out footer:").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(tab, textvariable=self.unsubscribe_text).grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
        row += 1

        ttk.Checkbutton(
            tab,
            variable=self.close_browsers,
            text="Close automated browser windows when the campaign finishes.",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 2))
        row += 1

        ttk.Checkbutton(
            tab,
            variable=self.confirmed,
            text=(
                "I confirm every recipient consented or has a valid existing business relationship, and the list "
                "excludes unsubscribed recipients."
            ),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 8))
        row += 1

        ttk.Label(
            tab,
            text=(
                "Split across profiles divides the eligible CSV in order into equal slices (about 1/N each) using "
                "the profiles listed on the Browser profiles tab. CSV sender_profile mode requires each row to name "
                "a configured profile; use it for explicit assignment, not automatic limit bypass."
            ),
            wraplength=1020,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8))
        row += 1

        buttons = ttk.Frame(tab)
        buttons.grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Button(buttons, text="Preview first eligible email", command=self._preview).pack(side="left")
        self.start_button = ttk.Button(buttons, text="Start UI automation", command=self._start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(buttons, text="Stop after current email", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")
        row += 1

        self.progress = ttk.Progressbar(tab, mode="determinate")
        self.progress.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 3))
        row += 1
        self.status_text = tk.StringVar(value="Ready")
        ttk.Label(tab, textvariable=self.status_text).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        preview_frame = ttk.LabelFrame(tab, text="Preview / activity", padding=8)
        preview_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.output = tk.Text(preview_frame, wrap="word")
        self.output.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(preview_frame, command=self.output.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=scroll.set)

    def _build_history_tab(self) -> None:
        tab = self.history_tab
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)
        ttk.Button(tab, text="Refresh history", command=self._refresh_history).grid(row=0, column=0, sticky="w", pady=(0, 8))

        columns = ("time", "campaign", "recipient", "profile", "mode", "status", "subject", "error")
        self.history_tree = ttk.Treeview(tab, columns=columns, show="headings")
        widths = {
            "time": 145,
            "campaign": 135,
            "recipient": 190,
            "profile": 150,
            "mode": 70,
            "status": 105,
            "subject": 220,
            "error": 260,
        }
        for column in columns:
            self.history_tree.heading(column, text=column.title())
            self.history_tree.column(column, width=widths[column], anchor="w")
        self.history_tree.grid(row=1, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(tab, orient="vertical", command=self.history_tree.yview)
        scroll_y.grid(row=1, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(tab, orient="horizontal", command=self.history_tree.xview)
        scroll_x.grid(row=2, column=0, sticky="ew")
        self.history_tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, padx=(8, 0), pady=5)

    def _browse_recipients(self) -> None:
        path = filedialog.askopenfilename(title="Select recipients CSV", filetypes=[("CSV files", "*.csv")])
        if path:
            self.recipient_path.set(path)

    def _browse_templates(self) -> None:
        path = filedialog.askopenfilename(title="Select templates JSON", filetypes=[("JSON files", "*.json")])
        if path:
            self.template_path.set(path)

    def _add_profile(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Add browser profile")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        provider_var = tk.StringVar(value="Proton Mail")
        label_var = tk.StringVar()
        email_var = tk.StringVar()
        result: dict[str, str] = {}

        frame = ttk.Frame(dialog, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frame, text="Provider:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(
            frame,
            textvariable=provider_var,
            values=("Proton Mail", "Gmail"),
            width=28,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=4, padx=(8, 0))
        ttk.Label(frame, text="Profile name:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=label_var, width=32).grid(row=1, column=1, sticky="ew", pady=4, padx=(8, 0))
        ttk.Label(frame, text="Expected address:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=email_var, width=32).grid(row=2, column=1, sticky="ew", pady=4, padx=(8, 0))

        def submit() -> None:
            result["provider"] = "gmail" if provider_var.get() == "Gmail" else "proton"
            result["label"] = label_var.get()
            result["email"] = email_var.get()
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")
        ttk.Button(buttons, text="Add", command=submit).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.wait_window()

        if not result:
            return
        try:
            profile = self.profile_store.add_profile(
                result["label"],
                result["email"],
                provider=normalize_provider(result["provider"]),
            )
        except Exception as exc:
            messagebox.showerror("Could not add profile", str(exc))
            return
        self._refresh_profiles()
        messagebox.showinfo(
            "Profile created",
            f"Created {profile.label} ({profile.provider_label}). "
            "Select it and click 'Open normal browser to sign in'.",
        )

    def _selected_profile(self) -> BrowserProfile | None:
        selection = self.profile_tree.selection()
        if not selection:
            return None
        profile_id = selection[0]
        return next((profile for profile in self.profiles if profile.profile_id == profile_id), None)

    def _open_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo("Select a profile", "Select a browser profile first.")
            return
        if self.worker and self.worker.is_alive():
            messagebox.showerror("Campaign running", "Stop the campaign before opening a profile manually.")
            return
        if self.profile_window_thread and self.profile_window_thread.is_alive():
            messagebox.showerror("Browser already open", "Close the currently opened profile browser first.")
            return

        channel = CHANNEL_LABELS[self.browser_label.get()]
        self.status_text.set(f"Opening {profile.label}...")

        def work() -> None:
            try:
                process = open_normal_login_browser(profile, channel)
                self.events.put((
                    "log",
                    f"Opened ordinary browser for {profile.label}. Sign in as {profile.expected_email}, "
                    "select 'Keep me signed in', confirm the inbox loads, then close every window using this profile.\n",
                ))
                process.wait()
                # Chrome can leave a short-lived background process holding the profile lock.
                time.sleep(1.5)
                self.events.put(("profile_window_closed", profile.label))
            except (LoginBrowserError, OSError) as exc:
                self.events.put(("error", f"Could not open the normal login browser: {exc}"))
            except Exception as exc:
                self.events.put(("error", f"Could not open browser profile: {exc}"))

        self.profile_window_thread = threading.Thread(target=work, daemon=True)
        self.profile_window_thread.start()

    def _remove_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo("Select a profile", "Select a browser profile first.")
            return
        delete_data = messagebox.askyesno(
            "Remove profile",
            f"Remove {profile.label}?\n\nChoose Yes to also delete its local browser cookies and login session.",
        )
        self.profile_store.remove_profile(profile.profile_id, delete_browser_data=delete_data)
        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        try:
            self.profiles = self.profile_store.list_profiles()
        except Exception as exc:
            messagebox.showerror("Profile registry", str(exc))
            self.profiles = []

        for item in self.profile_tree.get_children():
            self.profile_tree.delete(item)
        for profile in self.profiles:
            self.profile_tree.insert(
                "",
                tk.END,
                iid=profile.profile_id,
                values=(
                    profile.label,
                    profile.provider_label,
                    profile.expected_email,
                    str(profile.user_data_dir),
                ),
            )

        labels = [profile.label for profile in self.profiles]
        self.fixed_profile_box.configure(values=labels)
        if labels and self.fixed_profile.get() not in labels:
            self.fixed_profile.set(labels[0])
        self.status_text.set(f"{len(self.profiles)} browser profile(s) configured.")

    def _update_strategy_state(self) -> None:
        state = "readonly" if self.profile_strategy.get() == "Fixed profile" else "disabled"
        self.fixed_profile_box.configure(state=state)

    def _normalized_campaign_id(self) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "-", self.campaign_name.get().strip()).strip("-")
        if not value:
            raise ValueError("Enter a campaign ID.")
        return value

    def _load_campaign(self) -> tuple[list[Recipient], dict, list[str]]:
        recipients, issues = load_recipients(self.recipient_path.get())
        templates = load_templates(self.template_path.get())
        if not recipients:
            raise ValueError("No eligible recipients were found. Confirm opt_in=yes and valid addresses.")
        return recipients, templates, issues

    def _preview(self) -> None:
        try:
            recipients, templates, issues = self._load_campaign()
            recipient = recipients[0]
            subject, body = compose_message(recipient, templates, self.unsubscribe_text.get())
            profile = self._resolve_profile(recipient, recipients)
            split_summary = ""
            if self.profile_strategy.get() == "Split across profiles":
                assigned = assign_profiles_evenly(recipients, self.profiles)
                counts: dict[str, int] = {}
                for item in assigned.values():
                    counts[item.label] = counts.get(item.label, 0) + 1
                parts = [f"{label}: {count}" for label, count in counts.items()]
                split_summary = "SPLIT PLAN: " + ", ".join(parts) + "\n"
        except Exception as exc:
            messagebox.showerror("Preview failed", str(exc))
            return

        self.output.delete("1.0", tk.END)
        if issues:
            self._append("CSV notes:\n" + "\n".join(issues[:20]) + "\n\n")
        self._append(
            f"{split_summary}"
            f"BROWSER PROFILE: {profile.label} ({profile.provider_label}, {profile.expected_email})\n"
            f"TO: {recipient.email}\nSUBJECT: {subject}\n\n{body}\n"
        )
        self.status_text.set(f"Previewing 1 of {len(recipients)} eligible recipients.")

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.profile_window_thread and self.profile_window_thread.is_alive():
            messagebox.showerror("Profile browser open", "Close the manually opened login browser before starting.")
            return
        if not self.confirmed.get():
            messagebox.showerror("Confirmation required", "Confirm the recipient-consent statement before continuing.")
            return
        if not self.profiles:
            messagebox.showerror("No browser profiles", "Create and sign in to at least one browser profile first.")
            return

        try:
            campaign_id = self._normalized_campaign_id()
            recipients, templates, issues = self._load_campaign()
            local_cap = int(self.local_cap.get())
            delay = float(self.delay_seconds.get())
            if not 1 <= local_cap <= 100:
                raise ValueError("The local rolling-24-hour cap must be between 1 and 100.")
            if not 15 < delay <= 3600:
                raise ValueError("The max random delay must be greater than 15 and at most 3600 seconds.")
            for recipient in recipients:
                self._resolve_profile(recipient, recipients)
        except Exception as exc:
            messagebox.showerror("Campaign configuration", str(exc))
            return

        mode = "send" if self.mode.get() == "Send" else "draft"
        if mode == "send":
            confirmation = simpledialog.askstring(
                "Confirm direct sending",
                "The browser will click the mailbox Send button. Type SEND to continue:",
                parent=self,
            )
            if confirmation != "SEND":
                return

        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(maximum=len(recipients), value=0)
        self.output.delete("1.0", tk.END)
        if issues:
            self._append("CSV notes:\n" + "\n".join(issues[:50]) + "\n\n")

        channel = CHANNEL_LABELS[self.browser_label.get()]
        self.worker = threading.Thread(
            target=self._run_campaign,
            args=(
                campaign_id,
                recipients,
                templates,
                local_cap,
                delay,
                mode,
                channel,
                self.unsubscribe_text.get(),
                self.close_browsers.get(),
            ),
            daemon=True,
        )
        self.worker.start()

    def _resolve_profile(
        self,
        recipient: Recipient,
        recipients: list[Recipient] | None = None,
        split_map: dict[str, BrowserProfile] | None = None,
    ) -> BrowserProfile:
        strategy = self.profile_strategy.get()
        if strategy == "Fixed profile":
            profile = self.profile_store.find(self.fixed_profile.get())
            if profile is None:
                raise ValueError("Select a valid fixed browser profile.")
            return profile

        if strategy == "Split across profiles":
            if split_map is not None:
                profile = split_map.get(recipient.email)
            elif recipients is not None:
                profile = assign_profiles_evenly(recipients, self.profiles).get(recipient.email)
            else:
                raise ValueError("Split across profiles requires the full recipient list.")
            if profile is None:
                raise ValueError(f"No split assignment was produced for {recipient.email}.")
            return profile

        if not recipient.sender_profile:
            raise ValueError(
                f"Recipient {recipient.email} has no sender_profile value while CSV sender_profile mode is selected."
            )
        profile = self.profile_store.find(recipient.sender_profile)
        if profile is None:
            raise ValueError(
                f"Recipient {recipient.email} references unknown sender_profile {recipient.sender_profile!r}."
            )
        return profile

    def _run_campaign(
        self,
        campaign_id: str,
        recipients: list[Recipient],
        templates: dict,
        local_cap: int,
        delay: float,
        mode: str,
        channel: str,
        unsubscribe_text: str,
        close_browsers: bool,
    ) -> None:
        completed = 0
        blocked_profiles: set[str] = set()

        try:
            with sync_playwright() as playwright:
                manager = BrowserManager(playwright, browser_channel=channel)
                automators = {
                    "proton": ProtonUiAutomator(SCREENSHOT_DIR),
                    "gmail": GmailUiAutomator(SCREENSHOT_DIR),
                }
                counts = {
                    profile.profile_id: self.history.operations_last_24h(profile.profile_id)
                    for profile in self.profiles
                }
                split_map = None
                if self.profile_strategy.get() == "Split across profiles":
                    split_map = assign_profiles_evenly(recipients, self.profiles)
                    plan = ", ".join(
                        f"{profile.label}: {sum(1 for item in split_map.values() if item.profile_id == profile.profile_id)}"
                        for profile in self.profiles
                    )
                    self.events.put(("log", f"Split across {len(self.profiles)} profile(s): {plan}\n"))

                for index, recipient in enumerate(recipients, start=1):
                    if self.stop_event.is_set():
                        self.events.put(("log", "Stopped by user before the next email.\n"))
                        break

                    self.events.put(("progress", (index - 1, f"Preparing {recipient.email}")))
                    if self.history.already_completed(campaign_id, recipient.email):
                        self.events.put(("log", f"SKIP duplicate campaign recipient: {recipient.email}\n"))
                        self.events.put(("progress", (index, f"Processed {index} of {len(recipients)}")))
                        continue

                    profile = self._resolve_profile(recipient, recipients, split_map)
                    provider = normalize_provider(profile.provider)
                    automator = automators[provider]
                    if profile.profile_id in blocked_profiles:
                        self.events.put(("log", f"SKIP {recipient.email}: profile {profile.label} needs attention.\n"))
                        self.events.put(("progress", (index, f"Processed {index} of {len(recipients)}")))
                        continue
                    if counts.get(profile.profile_id, 0) >= local_cap:
                        self.events.put(("log", f"SKIP {recipient.email}: {profile.label} reached the configured local cap.\n"))
                        self.events.put(("progress", (index, f"Processed {index} of {len(recipients)}")))
                        continue

                    subject = ""
                    opened = None
                    try:
                        subject, body = compose_message(recipient, templates, unsubscribe_text)
                        opened = manager.open_profile(profile)
                        self.events.put(
                            (
                                "log",
                                f"UI {mode.upper()} ({profile.provider_label}): {recipient.email} via {profile.label}\n",
                            )
                        )
                        automator.create_message(
                            opened,
                            recipient=recipient.email,
                            subject=subject,
                            body=body,
                            mode=mode,
                        )
                        status = "sent" if mode == "send" else "draft_created"
                        counts[profile.profile_id] = counts.get(profile.profile_id, 0) + 1
                        self.history.record(
                            campaign_id=campaign_id,
                            recipient=recipient.email,
                            profile_id=profile.profile_id,
                            profile_label=profile.label,
                            subject=subject,
                            mode=mode,
                            status=status,
                        )
                        completed += 1
                        self.events.put(("log", f"{status.upper()}: {recipient.email}\n"))
                    except LoginRequiredError as exc:
                        blocked_profiles.add(profile.profile_id)
                        screenshot = automator.capture_failure(opened, recipient.email) if opened else ""
                        self.history.record(
                            campaign_id=campaign_id,
                            recipient=recipient.email,
                            profile_id=profile.profile_id,
                            profile_label=profile.label,
                            subject=subject,
                            mode=mode,
                            status="failed",
                            error=str(exc),
                            screenshot=screenshot,
                        )
                        self.events.put(("log", f"LOGIN REQUIRED for {profile.label}: {exc}\n"))
                    except Exception as exc:
                        screenshot = automator.capture_failure(opened, recipient.email) if opened else ""
                        self.history.record(
                            campaign_id=campaign_id,
                            recipient=recipient.email,
                            profile_id=profile.profile_id,
                            profile_label=profile.label,
                            subject=subject,
                            mode=mode,
                            status="failed",
                            error=str(exc),
                            screenshot=screenshot,
                        )
                        self.events.put(("log", f"FAILED: {recipient.email} via {profile.label}: {exc}\n"))

                    self.events.put(
                        ("progress", (index, f"Completed {completed}; processed {index} of {len(recipients)}"))
                    )
                    if delay > 15 and index < len(recipients) and not self.stop_event.is_set():
                        wait_seconds = random.uniform(15, delay)
                        self.events.put(("log", f"Waiting {wait_seconds:.1f}s before next operation...\n"))
                        self.stop_event.wait(wait_seconds)

                if close_browsers:
                    manager.close_all()
                elif manager.open_profiles:
                    self.events.put(
                        ("log", "Campaign finished. Close the automated browser windows to release their profiles.\n")
                    )
                    while any(not opened.page.is_closed() for opened in manager.open_profiles.values()):
                        time.sleep(0.5)
                    manager.close_all()
        except Exception as exc:
            self.events.put(("error", f"Campaign stopped because the browser automation failed: {exc}"))
        finally:
            self.events.put(("finished", completed))

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_text.set("Stopping after the current mailbox UI operation...")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._append(str(payload))
                elif event == "progress":
                    value, text = payload
                    self.progress.configure(value=value)
                    self.status_text.set(text)
                elif event == "profile_window_closed":
                    self.status_text.set(f"Closed browser profile: {payload}")
                elif event == "error":
                    self.status_text.set(str(payload))
                    self._append(f"ERROR: {payload}\n")
                    messagebox.showerror("Error", str(payload))
                elif event == "finished":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_text.set(f"Campaign finished. Completed: {payload or 0}")
                    self._refresh_history()
        except queue.Empty:
            pass
        self.after(150, self._drain_events)

    def _append(self, text: str) -> None:
        self.output.insert(tk.END, text)
        self.output.see(tk.END)

    def _refresh_history(self) -> None:
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for row in self.history.recent():
            error = row["error"] or ""
            if row["screenshot"]:
                error = f"{error} [screenshot: {row['screenshot']}]".strip()
            self.history_tree.insert(
                "",
                tk.END,
                values=(
                    row["created_at"].replace("T", " ").replace("+00:00", " UTC"),
                    row["campaign_id"],
                    row["recipient"],
                    row["profile_label"],
                    row["mode"],
                    row["status"],
                    row["subject"],
                    error,
                ),
            )


if __name__ == "__main__":
    app = MailerApp()
    app.mainloop()
