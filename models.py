from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BrowserProfile:
    profile_id: str
    label: str
    expected_email: str
    user_data_dir: Path


@dataclass(frozen=True)
class Recipient:
    email: str
    name: str = ""
    company: str = ""
    template_key: str = "random"
    subject: str = ""
    body: str = ""
    sender_profile: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageTemplate:
    key: str
    subject: str
    body: str
