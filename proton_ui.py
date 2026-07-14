from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from models import BrowserProfile

PROTON_MAIL_URL = "https://mail.proton.me/u/0/inbox"


class ProtonUiError(RuntimeError):
    pass


class LoginRequiredError(ProtonUiError):
    pass


@dataclass
class OpenProfile:
    profile: BrowserProfile
    context: BrowserContext
    page: Page


class ProtonBrowserManager:
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
                raise ProtonUiError(f"Could not launch the browser: {first_error}") from first_error
            launch_args.pop("channel", None)
            try:
                context = self.playwright.chromium.launch_persistent_context(**launch_args)
            except Exception as fallback_error:
                raise ProtonUiError(
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


class ProtonUiAutomator:
    def __init__(self, screenshot_dir: str | Path) -> None:
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def open_for_manual_login(self, opened: OpenProfile) -> None:
        opened.page.goto(PROTON_MAIL_URL, wait_until="domcontentloaded")
        opened.page.bring_to_front()

    def ensure_ready(self, opened: OpenProfile) -> None:
        page = opened.page
        if page.is_closed():
            raise ProtonUiError("The browser window was closed.")
        if "mail.proton.me" not in page.url:
            page.goto(PROTON_MAIL_URL, wait_until="domcontentloaded")

        try:
            self._compose_button(page).wait_for(state="visible", timeout=60_000)
        except PlaywrightTimeoutError as exc:
            if self._looks_like_sign_in(page):
                raise LoginRequiredError(
                    f"Sign in manually to {opened.profile.expected_email} in this browser profile, "
                    "complete any Proton security checks, and try again."
                ) from exc
            raise ProtonUiError(
                "Proton Mail did not become ready. Confirm the inbox is fully loaded, Proton Mail is using "
                "English, and no dialog or security prompt is covering the page."
            ) from exc

        actual_email = self._detect_logged_in_email(page)
        expected = opened.profile.expected_email.strip().lower()
        if actual_email and expected and actual_email != expected:
            raise LoginRequiredError(
                f"This profile appears to be signed in as {actual_email}, but it is configured for {expected}."
            )

    def create_message(
        self,
        opened: OpenProfile,
        *,
        recipient: str,
        subject: str,
        body: str,
        mode: str,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.ensure_ready(opened)
        if stop_requested and stop_requested():
            raise ProtonUiError("Stopped before opening the composer.")

        page = opened.page
        page.bring_to_front()
        self._compose_button(page).click()

        subject_box = self._subject_input(page)
        subject_box.wait_for(state="visible", timeout=20_000)
        root = self._compose_root(subject_box)

        to_box = self._to_input(root, page)
        to_box.click()
        to_box.fill(recipient)
        to_box.press("Enter")

        subject_box.fill(subject)
        self._fill_body(root, page, body)

        if stop_requested and stop_requested():
            self._wait_for_autosave(page)
            self._save_and_close(root, page)
            raise ProtonUiError("Stopped after composing; the message was left as a Proton Mail draft.")

        if mode == "draft":
            self._wait_for_autosave(page)
            self._save_and_close(root, page)
            return
        if mode != "send":
            raise ValueError(f"Unsupported mode: {mode}")

        send_button = self._send_button(root, page)
        send_button.click()
        self._wait_for_send_result(page, subject_box, root)

    def capture_failure(self, opened: OpenProfile, label: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")[:80] or "failure"
        path = self.screenshot_dir / f"{int(time.time())}-{safe}.png"
        try:
            opened.page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            return ""

    @staticmethod
    def _compose_button(page: Page) -> Locator:
        candidates = [
            page.locator('[data-testid="sidebar:compose"]'),
            page.locator('button[data-testid*="compose" i]').filter(has_text=re.compile(r"New message|Compose", re.I)),
            page.get_by_role("button", name=re.compile(r"^New message$", re.I)),
            page.get_by_role("button", name=re.compile(r"^Compose$", re.I)),
            page.locator('button:has-text("New message")'),
        ]
        return ProtonUiAutomator._first_existing(candidates)

    @staticmethod
    def _subject_input(page: Page) -> Locator:
        candidates = [
            page.locator('[data-testid="composer:subject"] input').last,
            page.locator('input[data-testid="composer:subject"]').last,
            page.locator('input[placeholder="Subject"]').last,
            page.locator('input[aria-label="Subject"]').last,
            page.locator('input[name="subject"]').last,
        ]
        return ProtonUiAutomator._first_existing(candidates)

    @staticmethod
    def _compose_root(subject_box: Locator) -> Locator:
        candidates = [
            subject_box.locator('xpath=ancestor::*[@data-testid="composer"][1]'),
            subject_box.locator('xpath=ancestor::*[contains(@data-testid,"composer")][1]'),
            subject_box.locator('xpath=ancestor::*[@role="dialog"][1]'),
            subject_box.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," composer ")][1]'),
            subject_box.locator("xpath=ancestor::section[1]"),
        ]
        return ProtonUiAutomator._first_existing(candidates)

    @staticmethod
    def _to_input(root: Locator, page: Page) -> Locator:
        candidates = [
            root.locator('[data-testid="composer:to"] input'),
            root.locator('[data-testid*="composer" i][data-testid*="to" i] input'),
            root.locator('input[aria-label="To"]'),
            root.locator('input[placeholder="To"]'),
            root.locator('input[placeholder*="Email address" i]'),
            root.locator('input[autocomplete="email"]'),
            page.locator('[data-testid="composer:to"] input').last,
            page.locator('input[placeholder*="Email address" i]').last,
        ]
        locator = ProtonUiAutomator._first_existing(candidates)
        locator.wait_for(state="visible", timeout=15_000)
        return locator

    @staticmethod
    def _fill_body(root: Locator, page: Page, body: str) -> None:
        direct_candidates = [
            root.locator('[data-testid="composer:body"] [contenteditable="true"]'),
            root.locator('[data-testid*="editor" i][contenteditable="true"]'),
            root.locator('[role="textbox"][contenteditable="true"]'),
            root.locator('div[contenteditable="true"]'),
        ]
        for candidate in direct_candidates:
            try:
                count = candidate.count()
            except Exception:
                count = 0
            for index in range(count):
                editor = candidate.nth(index)
                try:
                    if editor.is_visible():
                        editor.click()
                        editor.fill(body)
                        return
                except Exception:
                    continue

        iframe_candidates = [
            root.locator('iframe[data-testid*="editor" i]'),
            root.locator('iframe[title*="body" i]'),
            root.locator('iframe'),
        ]
        for frames in iframe_candidates:
            try:
                count = frames.count()
            except Exception:
                count = 0
            for index in range(count):
                try:
                    handle = frames.nth(index).element_handle()
                    frame = handle.content_frame() if handle else None
                    if frame is None:
                        continue
                    editor_candidates = [
                        frame.locator('[contenteditable="true"]').first,
                        frame.locator('[role="textbox"]').first,
                        frame.locator("body").first,
                    ]
                    for editor in editor_candidates:
                        if editor.count() == 0 or not editor.is_visible():
                            continue
                        editor.click()
                        try:
                            editor.fill(body)
                        except Exception:
                            page.keyboard.press("Control+A")
                            page.keyboard.insert_text(body)
                        return
                except Exception:
                    continue

        raise ProtonUiError(
            "The Proton Mail message editor was not found. Proton may have changed the composer interface."
        )

    @staticmethod
    def _send_button(root: Locator, page: Page) -> Locator:
        candidates = [
            root.locator('[data-testid="composer:send-button"]'),
            root.locator('button[data-testid*="send" i]'),
            root.get_by_role("button", name=re.compile(r"^Send$", re.I)),
            root.locator('button:has-text("Send")'),
            page.locator('[data-testid="composer:send-button"]').last,
        ]
        locator = ProtonUiAutomator._first_existing(candidates)
        locator.wait_for(state="visible", timeout=15_000)
        return locator

    @staticmethod
    def _save_and_close(root: Locator, page: Page) -> None:
        candidates = [
            root.locator('[data-testid="composer:close-button"]'),
            root.locator('button[data-testid*="composer" i][data-testid*="close" i]'),
            root.get_by_role("button", name=re.compile(r"^(Save and close|Close)$", re.I)),
            root.locator('button[aria-label="Close"]'),
            root.locator('button[title="Close"]'),
        ]
        locator = ProtonUiAutomator._first_existing(candidates)
        try:
            locator.click(timeout=10_000)
        except Exception as exc:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
                if root.is_visible():
                    raise exc
            except Exception:
                raise ProtonUiError(
                    "The email was composed, but Proton Mail's composer close button was not found."
                ) from exc

    @staticmethod
    def _wait_for_autosave(page: Page) -> None:
        saving = page.get_by_text(re.compile(r"Saving", re.I))
        try:
            saving.wait_for(state="hidden", timeout=15_000)
        except Exception:
            page.wait_for_timeout(2500)

    @staticmethod
    def _wait_for_send_result(page: Page, subject_box: Locator, root: Locator) -> None:
        error_patterns = [
            r"message could not be sent",
            r"message not sent",
            r"sending failed",
            r"unable to send",
            r"sending has been temporarily disabled",
            r"reached.*limit",
        ]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            for pattern in error_patterns:
                try:
                    if page.get_by_text(re.compile(pattern, re.I)).count() > 0:
                        raise ProtonUiError(f"Proton Mail reported a sending error matching: {pattern}")
                except ProtonUiError:
                    raise
                except Exception:
                    pass
            try:
                if page.get_by_text(re.compile(r"Message sent|Email sent", re.I)).count() > 0:
                    return
            except Exception:
                pass
            try:
                if not subject_box.is_visible() or not root.is_visible():
                    return
            except Exception:
                return
            page.wait_for_timeout(350)
        raise ProtonUiError("Proton Mail did not confirm that the message was sent.")

    @staticmethod
    def _detect_logged_in_email(page: Page) -> str:
        email_re = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
        selectors = [
            'button[aria-label*="@"]',
            '[aria-label*="@"]',
            'button[title*="@"]',
            '[title*="@"]',
            '[data-testid*="account" i]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 20)
            except Exception:
                count = 0
            for index in range(count):
                item = locator.nth(index)
                for attribute in ("aria-label", "title", "data-testid"):
                    try:
                        value = item.get_attribute(attribute) or ""
                    except Exception:
                        value = ""
                    match = email_re.search(value)
                    if match:
                        return match.group(0).lower()
                try:
                    text = item.inner_text(timeout=500)
                except Exception:
                    text = ""
                match = email_re.search(text)
                if match:
                    return match.group(0).lower()
        return ""

    @staticmethod
    def _looks_like_sign_in(page: Page) -> bool:
        url = page.url.lower()
        if "account.proton.me" in url or "auth.proton.me" in url or "/login" in url:
            return True
        try:
            title = page.title().lower()
            body = page.locator("body").inner_text(timeout=3_000).lower()
        except Exception:
            return False
        login_terms = ("sign in", "log in", "username", "password")
        return any(term in title or term in body[:3500] for term in login_terms)

    @staticmethod
    def _first_existing(candidates: list[Locator]) -> Locator:
        for candidate in candidates:
            try:
                if candidate.count() > 0:
                    return candidate.first
            except Exception:
                continue
        return candidates[0].first
