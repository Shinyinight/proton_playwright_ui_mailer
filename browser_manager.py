from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright

from models import BrowserProfile


class MailUiError(RuntimeError):
    pass


class LoginRequiredError(MailUiError):
    pass


@dataclass
class OpenProfile:
    profile: BrowserProfile
    context: BrowserContext
    page: Page


class BrowserManager:
    def __init__(
        self,
        playwright: Playwright,
        *,
        browser_channel: str = "chrome",
        operation_timeout_ms: int = 30_000,
    ) -> None:
        self.playwright = playwright
        self.browser_channel = browser_channel.strip().lower()
        self.operation_timeout_ms = operation_timeout_ms
        self.open_profiles: dict[str, OpenProfile] = {}

    def open_profile(self, profile: BrowserProfile) -> OpenProfile:
        existing = self.open_profiles.get(profile.profile_id)
        if existing and not existing.page.is_closed():
            return existing

        profile.user_data_dir.mkdir(parents=True, exist_ok=True)
        launch_args: dict[str, object] = {
            "user_data_dir": str(profile.user_data_dir),
            "headless": False,
            "no_viewport": True,
            "args": ["--start-maximized"],
        }
        if self.browser_channel in {"chrome", "msedge"}:
            launch_args["channel"] = self.browser_channel

        try:
            context = self.playwright.chromium.launch_persistent_context(**launch_args)
        except Exception as first_error:
            if "channel" not in launch_args:
                raise MailUiError(f"Could not launch the browser: {first_error}") from first_error
            launch_args.pop("channel", None)
            try:
                context = self.playwright.chromium.launch_persistent_context(**launch_args)
            except Exception as fallback_error:
                raise MailUiError(
                    "Could not launch Chrome, Edge, or Playwright Chromium. Run install_browser.bat, "
                    f"then try again. Details: {fallback_error}"
                ) from fallback_error

        context.set_default_timeout(self.operation_timeout_ms)
        page = context.pages[0] if context.pages else context.new_page()
        opened = OpenProfile(profile, context, page)
        self.open_profiles[profile.profile_id] = opened
        return opened

    def close_all(self) -> None:
        for opened in list(self.open_profiles.values()):
            try:
                opened.context.close()
            except Exception:
                pass
        self.open_profiles.clear()
