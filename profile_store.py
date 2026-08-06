from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from models import PROVIDERS, BrowserProfile, Recipient


def normalize_provider(provider: str) -> str:
    value = (provider or "proton").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"Provider must be one of: {', '.join(PROVIDERS)}.")
    return value


def assign_profiles_evenly(
    recipients: list[Recipient],
    profiles: list[BrowserProfile],
) -> dict[str, BrowserProfile]:
    """Map each recipient email to a profile in contiguous equal slices.

    With 3 profiles and 9 recipients, the first third use profiles[0], the next
    third use profiles[1], and the last third use profiles[2]. Remainder rows
    are distributed across the earlier slices.
    """
    if not profiles:
        raise ValueError("Create at least one browser profile first.")
    if not recipients:
        return {}

    total = len(recipients)
    count = len(profiles)
    assignments: dict[str, BrowserProfile] = {}
    for index, recipient in enumerate(recipients):
        profile_index = min((index * count) // total, count - 1)
        assignments[recipient.email] = profiles[profile_index]
    return assignments


class ProfileStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "browser_profiles"
        self.registry_path = self.data_dir / "profiles.json"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self.registry_path.write_text("[]\n", encoding="utf-8")

    def list_profiles(self) -> list[BrowserProfile]:
        records = self._read()
        return [self._to_profile(record) for record in records]

    def add_profile(self, label: str, expected_email: str, provider: str = "proton") -> BrowserProfile:
        clean_label = label.strip()
        clean_email = expected_email.strip().lower()
        clean_provider = normalize_provider(provider)
        if not clean_label:
            raise ValueError("Enter a profile name.")
        if not clean_email or "@" not in clean_email:
            raise ValueError(f"Enter the {clean_provider} address expected in this browser profile.")

        profiles = self.list_profiles()
        if any(profile.label.lower() == clean_label.lower() for profile in profiles):
            raise ValueError("A browser profile with that name already exists.")
        if any(profile.expected_email == clean_email for profile in profiles):
            raise ValueError("That email address is already assigned to another browser profile.")

        slug = re.sub(r"[^a-z0-9]+", "-", clean_label.lower()).strip("-") or clean_provider
        profile_id = f"{slug}-{uuid.uuid4().hex[:8]}"
        user_data_dir = (self.profiles_dir / profile_id).resolve()
        user_data_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "profile_id": profile_id,
            "label": clean_label,
            "expected_email": clean_email,
            "user_data_dir": str(user_data_dir),
            "provider": clean_provider,
        }
        records = self._read()
        records.append(record)
        self._write(records)
        return BrowserProfile(profile_id, clean_label, clean_email, user_data_dir, clean_provider)

    def remove_profile(self, profile_id: str, delete_browser_data: bool = False) -> None:
        records = self._read()
        target = next((record for record in records if record.get("profile_id") == profile_id), None)
        if target is None:
            return
        records = [record for record in records if record.get("profile_id") != profile_id]
        self._write(records)
        if delete_browser_data:
            shutil.rmtree(Path(target["user_data_dir"]), ignore_errors=True)

    def find(self, key: str) -> BrowserProfile | None:
        value = key.strip().lower()
        for profile in self.list_profiles():
            if value in {profile.profile_id.lower(), profile.label.lower(), profile.expected_email.lower()}:
                return profile
        return None

    def _to_profile(self, record: dict[str, object]) -> BrowserProfile:
        provider = str(record.get("provider") or "proton").strip().lower()
        if provider not in PROVIDERS:
            provider = "proton"
        return BrowserProfile(
            profile_id=str(record["profile_id"]),
            label=str(record["label"]),
            expected_email=str(record.get("expected_email", "")).lower(),
            user_data_dir=Path(record["user_data_dir"]),
            provider=provider,
        )

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Could not read {self.registry_path}: {exc}") from exc
        if not isinstance(raw, list):
            raise RuntimeError("profiles.json must contain a JSON list.")
        return [item for item in raw if isinstance(item, dict)]

    def _write(self, records: list[dict[str, object]]) -> None:
        temp = self.registry_path.with_suffix(".tmp")
        temp.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.registry_path)
