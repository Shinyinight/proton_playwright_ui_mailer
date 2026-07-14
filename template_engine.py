from __future__ import annotations

import csv
import json
import random
import re
from pathlib import Path

from email_validator import EmailNotValidError, validate_email

from models import MessageTemplate, Recipient

_RANDOM_TEMPLATE_KEYS = {"", "random"}

_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z0-9_\-]+)\s*}}")
_TRUE_VALUES = {"1", "true", "yes", "y", "confirmed", "opted-in", "opted_in"}
_BLOCK_VALUES = {"1", "true", "yes", "y", "blocked", "unsubscribe", "unsubscribed"}


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def is_true(value: object) -> bool:
    return _clean(value).lower() in _TRUE_VALUES


def render_text(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return _PLACEHOLDER_RE.sub(replace, text)


def load_templates(path: str | Path) -> dict[str, MessageTemplate]:
    template_path = Path(path)
    with template_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if not isinstance(raw, dict) or not raw:
        raise ValueError("The template file must contain at least one template object.")

    templates: dict[str, MessageTemplate] = {}
    for key, item in raw.items():
        if not isinstance(item, dict):
            raise ValueError(f"Template {key!r} must be a JSON object.")
        subject = _clean(item.get("subject"))
        body = _clean(item.get("body"))
        if not subject or not body:
            raise ValueError(f"Template {key!r} requires both subject and body.")
        templates[str(key)] = MessageTemplate(str(key), subject, body)
    return templates


def load_recipients(path: str | Path) -> tuple[list[Recipient], list[str]]:
    recipients: list[Recipient] = []
    issues: list[str] = []
    seen: set[str] = set()

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("The CSV file has no header row.")

        normalized_headers = {header.strip().lower() for header in reader.fieldnames if header}
        if "email" not in normalized_headers:
            raise ValueError("The CSV file must contain an 'email' column.")
        if "opt_in" not in normalized_headers and "consent" not in normalized_headers:
            raise ValueError("The CSV file must contain an 'opt_in' or 'consent' column.")

        for row_number, original_row in enumerate(reader, start=2):
            row = {(_clean(key).lower()): _clean(value) for key, value in original_row.items() if key}
            raw_email = row.get("email", "")
            consent = row.get("opt_in", row.get("consent", ""))
            do_not_contact = row.get("do_not_contact", "")

            if not is_true(consent):
                issues.append(f"Row {row_number}: skipped because opt-in is not confirmed.")
                continue
            if _clean(do_not_contact).lower() in _BLOCK_VALUES:
                issues.append(f"Row {row_number}: skipped because do_not_contact is enabled.")
                continue

            try:
                validated = validate_email(raw_email, check_deliverability=False)
                email = validated.normalized.lower()
            except EmailNotValidError as exc:
                issues.append(f"Row {row_number}: invalid email ({exc}).")
                continue

            if email in seen:
                issues.append(f"Row {row_number}: duplicate recipient {email} skipped.")
                continue
            seen.add(email)

            fields = dict(row)
            fields["email"] = email
            recipients.append(
                Recipient(
                    email=email,
                    name=row.get("name", ""),
                    company=row.get("company", ""),
                    template_key=row.get("template_key", "random") or "random",
                    subject=row.get("subject", ""),
                    body=row.get("body", ""),
                    sender_profile=row.get("sender_profile", row.get("profile", "")),
                    fields=fields,
                )
            )

    return recipients, issues


def pick_template(
    template_key: str,
    templates: dict[str, MessageTemplate],
) -> MessageTemplate:
    key = _clean(template_key).lower()
    if key in _RANDOM_TEMPLATE_KEYS:
        return random.choice(list(templates.values()))

    template = templates.get(template_key) or templates.get(key)
    if template is not None:
        return template
    template = templates.get("default")
    if template is not None:
        return template
    return next(iter(templates.values()))


def compose_message(
    recipient: Recipient,
    templates: dict[str, MessageTemplate],
    unsubscribe_text: str,
) -> tuple[str, str]:
    template = pick_template(recipient.template_key, templates)

    values = {
        **recipient.fields,
        "email": recipient.email,
        "name": recipient.name,
        "company": recipient.company,
        "sender_profile": recipient.sender_profile,
        "template_key": template.key,
    }
    subject = render_text(recipient.subject or template.subject, values).strip()
    body = render_text(recipient.body or template.body, values).strip()

    if not subject:
        raise ValueError(f"No subject was produced for {recipient.email}.")
    if not body:
        raise ValueError(f"No body was produced for {recipient.email}.")

    if unsubscribe_text and "unsubscribe" not in body.lower():
        body = f"{body}\n\n{unsubscribe_text.strip()}"
    return subject, body
