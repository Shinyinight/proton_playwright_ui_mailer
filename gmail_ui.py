from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from browser_manager import LoginRequiredError, MailUiError, OpenProfile

GMAIL_MAIL_URL = "https://mail.google.com/mail/u/0/#inbox"


class GmailUiAutomator:
    def __init__(self, screenshot_dir: str | Path) -> None:
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def open_for_manual_login(self, opened: OpenProfile) -> None:
        opened.page.goto(GMAIL_MAIL_URL, wait_until="domcontentloaded")
        opened.page.bring_to_front()

    def ensure_ready(self, opened: OpenProfile) -> None:
        page = opened.page
        if page.is_closed():
            raise MailUiError("The browser window was closed.")
        if "mail.google.com" not in page.url:
            page.goto(GMAIL_MAIL_URL, wait_until="domcontentloaded")

        try:
            self._compose_button(page).wait_for(state="visible", timeout=60_000)
        except PlaywrightTimeoutError as exc:
            if self._looks_like_sign_in(page):
                raise LoginRequiredError(
                    f"Sign in manually to {opened.profile.expected_email} in this browser profile, "
                    "complete any Google security checks, and try again."
                ) from exc
            raise MailUiError(
                "Gmail did not become ready. Confirm the inbox is fully loaded, Gmail is using English, "
                "and no dialog or security prompt is covering the page."
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
            raise MailUiError("Stopped before opening the composer.")

        page = opened.page
        page.bring_to_front()
        self._dismiss_popups(page)
        self._compose_button(page).click()

        subject_box = self._subject_input(page)
        subject_box.wait_for(state="visible", timeout=20_000)
        root = self._compose_root(subject_box)

        to_box = self._to_input(root, page)
        to_box.click()
        to_box.fill(recipient)
        to_box.press("Tab")

        subject_box.click()
        subject_box.fill(subject)
        self._fill_body(root, page, body)

        if stop_requested and stop_requested():
            self._wait_for_autosave(page)
            self._save_and_close(root, page)
            raise MailUiError("Stopped after composing; the message was left as a Gmail draft.")

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
    def _dismiss_popups(page: Page) -> None:
        dismiss_labels = (
            r"^Got it$",
            r"^Not now$",
            r"^No thanks$",
            r"^Dismiss$",
            r"^Close$",
        )
        for label in dismiss_labels:
            try:
                button = page.get_by_role("button", name=re.compile(label, re.I))
                if button.count() > 0 and button.first.is_visible():
                    button.first.click(timeout=1_500)
                    page.wait_for_timeout(300)
            except Exception:
                continue

    @staticmethod
    def _compose_button(page: Page) -> Locator:
        candidates = [
            page.locator('div[role="button"][gh="cm"]'),
            page.get_by_role("button", name=re.compile(r"^Compose$", re.I)),
            page.locator('div[role="button"]').filter(has_text=re.compile(r"^Compose$", re.I)),
            page.locator('[aria-label="Compose"]'),
        ]
        return GmailUiAutomator._first_existing(candidates)

    @staticmethod
    def _subject_input(page: Page) -> Locator:
        candidates = [
            page.locator('input[name="subjectbox"]').last,
            page.locator('input[aria-label="Subject"]').last,
            page.locator('input[placeholder="Subject"]').last,
        ]
        return GmailUiAutomator._first_existing(candidates)

    @staticmethod
    def _compose_root(subject_box: Locator) -> Locator:
        candidates = [
            subject_box.locator('xpath=ancestor::div[contains(@class,"M9")][1]'),
            subject_box.locator('xpath=ancestor::div[@role="dialog"][1]'),
            subject_box.locator('xpath=ancestor::form[1]'),
            subject_box.locator("xpath=ancestor::div[1]"),
        ]
        return GmailUiAutomator._first_existing(candidates)

    @staticmethod
    def _to_input(root: Locator, page: Page) -> Locator:
        candidates = [
            root.locator('textarea[name="to"]'),
            root.locator('input[name="to"]'),
            root.locator('textarea[aria-label*="To" i]'),
            root.locator('input[aria-label*="To" i]'),
            root.locator('[aria-label*="To recipients" i]'),
            page.locator('textarea[name="to"]').last,
            page.locator('input[aria-label*="To recipients" i]').last,
            page.locator('textarea[aria-label*="To" i]').last,
        ]
        locator = GmailUiAutomator._first_existing(candidates)
        locator.wait_for(state="visible", timeout=15_000)
        return locator

    @staticmethod
    def _fill_body(root: Locator, page: Page, body: str) -> None:
        candidates = [
            root.locator('div[aria-label="Message Body"][contenteditable="true"]'),
            root.locator('div[role="textbox"][contenteditable="true"]'),
            root.locator('div[g_editable="true"][contenteditable="true"]'),
            root.locator('div[contenteditable="true"]'),
            page.locator('div[aria-label="Message Body"][contenteditable="true"]').last,
            page.locator('div[role="textbox"][g_editable="true"]').last,
        ]
        for candidate in candidates:
            try:
                count = candidate.count()
            except Exception:
                count = 0
            for index in range(count):
                editor = candidate.nth(index)
                try:
                    if not editor.is_visible():
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
        raise MailUiError(
            "The Gmail message editor was not found. Gmail may have changed the composer interface."
        )

    @staticmethod
    def _send_button(root: Locator, page: Page) -> Locator:
        candidates = [
            root.locator('div[role="button"][aria-label*="Send" i]'),
            root.get_by_role("button", name=re.compile(r"^Send$", re.I)),
            root.locator('div[role="button"]').filter(has_text=re.compile(r"^Send$", re.I)),
            page.locator('div[role="button"][aria-label*="Send" i]').last,
            page.get_by_role("button", name=re.compile(r"^Send$", re.I)).last,
        ]
        locator = GmailUiAutomator._first_existing(candidates)
        locator.wait_for(state="visible", timeout=15_000)
        return locator

    @staticmethod
    def _save_and_close(root: Locator, page: Page) -> None:
        candidates = [
            root.locator('[aria-label="Save & close"]'),
            root.locator('[aria-label*="Save and close" i]'),
            root.locator('img[aria-label="Save & close"]'),
            root.get_by_role("button", name=re.compile(r"Save.*(close|draft)", re.I)),
            root.locator('[aria-label="Close"]'),
            page.locator('[aria-label="Save & close"]').last,
        ]
        locator = GmailUiAutomator._first_existing(candidates)
        try:
            locator.click(timeout=10_000)
        except Exception as exc:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception:
                raise MailUiError(
                    "The email was composed, but Gmail's composer close button was not found."
                ) from exc

    @staticmethod
    def _wait_for_autosave(page: Page) -> None:
        saving = page.get_by_text(re.compile(r"Saving", re.I))
        try:
            saving.wait_for(state="hidden", timeout=15_000)
        except Exception:
            page.wait_for_timeout(2000)
        # Gmail shows "Draft saved" briefly; give it a moment to finish.
        try:
            page.get_by_text(re.compile(r"Draft saved", re.I)).first.wait_for(state="visible", timeout=5_000)
        except Exception:
            page.wait_for_timeout(1000)

    @staticmethod
    def _wait_for_send_result(page: Page, subject_box: Locator, root: Locator) -> None:
        error_patterns = [
            r"message not sent",
            r"couldn't send",
            r"could not send",
            r"unable to send",
            r"sending failed",
        ]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            for pattern in error_patterns:
                try:
                    if page.get_by_text(re.compile(pattern, re.I)).count() > 0:
                        raise MailUiError(f"Gmail reported a sending error matching: {pattern}")
                except MailUiError:
                    raise
                except Exception:
                    pass
            try:
                if page.get_by_text(re.compile(r"Message sent|Your message has been sent", re.I)).count() > 0:
                    return
            except Exception:
                pass
            try:
                if not subject_box.is_visible() or not root.is_visible():
                    return
            except Exception:
                return
            page.wait_for_timeout(350)
        raise MailUiError("Gmail did not confirm that the message was sent.")

    @staticmethod
    def _detect_logged_in_email(page: Page) -> str:
        email_re = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
        selectors = [
            'a[aria-label*="@"]',
            'button[aria-label*="@"]',
            '[aria-label*="@gmail.com" i]',
            '[aria-label*="@"]',
            'img[aria-label*="@"]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 20)
            except Exception:
                count = 0
            for index in range(count):
                item = locator.nth(index)
                for attribute in ("aria-label", "title", "alt"):
                    try:
                        value = item.get_attribute(attribute) or ""
                    except Exception:
                        value = ""
                    match = email_re.search(value)
                    if match:
                        return match.group(0).lower()
        return ""

    @staticmethod
    def _looks_like_sign_in(page: Page) -> bool:
        url = page.url.lower()
        if "accounts.google.com" in url or "/signin" in url or "/ServiceLogin" in url:
            return True
        try:
            title = page.title().lower()
            body = page.locator("body").inner_text(timeout=3_000).lower()
        except Exception:
            return False
        login_terms = ("sign in", "log in", "choose an account", "forgot email", "password")
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
