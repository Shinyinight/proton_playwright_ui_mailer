from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from models import BrowserProfile

PROTON_LOGIN_URL = "https://account.proton.me/mail"
GMAIL_LOGIN_URL = "https://mail.google.com/"


class LoginBrowserError(RuntimeError):
    pass


def login_url_for_provider(provider: str) -> str:
    if (provider or "proton").strip().lower() == "gmail":
        return GMAIL_LOGIN_URL
    return PROTON_LOGIN_URL


def _candidate_paths(channel: str) -> list[Path]:
    channel = channel.strip().lower()
    program_files = [
        os.environ.get("PROGRAMFILES", ""),
        os.environ.get("PROGRAMFILES(X86)", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]

    if channel == "msedge":
        relative = [
            Path("Microsoft/Edge/Application/msedge.exe"),
        ]
        command_names = ["msedge.exe", "msedge"]
    else:
        relative = [
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Chromium/Application/chrome.exe"),
        ]
        command_names = ["chrome.exe", "chrome"]

    candidates: list[Path] = []
    for command in command_names:
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    for root in program_files:
        if not root:
            continue
        for item in relative:
            candidates.append(Path(root) / item)
    return candidates


def find_installed_browser(channel: str) -> Path:
    for candidate in _candidate_paths(channel):
        if candidate.is_file():
            return candidate.resolve()
    display = "Microsoft Edge" if channel.strip().lower() == "msedge" else "Google Chrome"
    raise LoginBrowserError(
        f"{display} was not found. Install it or select another installed browser in the application."
    )


def open_normal_login_browser(profile: BrowserProfile, channel: str) -> subprocess.Popen[bytes]:
    """Open the provider login page in an ordinary browser using the app's profile folder.

    Login is intentionally not performed in a Playwright-controlled browser. Once the user
    closes this browser, Playwright can reopen the same user-data directory for UI automation.
    """
    normalized = channel.strip().lower()
    if normalized == "chromium":
        # The bundled browser is intended for Playwright automation, not manual account setup.
        # Use an installed Chrome browser for login instead.
        normalized = "chrome"

    executable = find_installed_browser(normalized)
    profile.user_data_dir.mkdir(parents=True, exist_ok=True)
    provider = getattr(profile, "provider", "proton")
    args = [
        str(executable),
        f"--user-data-dir={profile.user_data_dir}",
        "--profile-directory=Default",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        login_url_for_provider(provider),
    ]
    try:
        return subprocess.Popen(args, cwd=str(executable.parent))
    except OSError as exc:
        raise LoginBrowserError(f"Could not start {executable}: {exc}") from exc
