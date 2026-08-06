from pathlib import Path

from models import BrowserProfile, Recipient
from profile_store import ProfileStore, assign_profiles_evenly


def test_assign_profiles_evenly_splits_into_contiguous_thirds() -> None:
    profiles = [
        BrowserProfile("p1", "One", "one@proton.me", Path("one")),
        BrowserProfile("p2", "Two", "two@proton.me", Path("two")),
        BrowserProfile("p3", "Three", "three@proton.me", Path("three")),
    ]
    recipients = [Recipient(email=f"user{i}@example.com") for i in range(9)]

    assigned = assign_profiles_evenly(recipients, profiles)

    assert [assigned[f"user{i}@example.com"].label for i in range(9)] == [
        "One",
        "One",
        "One",
        "Two",
        "Two",
        "Two",
        "Three",
        "Three",
        "Three",
    ]


def test_assign_profiles_evenly_handles_remainder() -> None:
    profiles = [
        BrowserProfile("p1", "One", "one@proton.me", Path("one")),
        BrowserProfile("p2", "Two", "two@proton.me", Path("two")),
        BrowserProfile("p3", "Three", "three@proton.me", Path("three")),
    ]
    recipients = [Recipient(email=f"user{i}@example.com") for i in range(10)]

    assigned = assign_profiles_evenly(recipients, profiles)
    labels = [assigned[recipient.email].label for recipient in recipients]

    assert labels.count("One") == 4
    assert labels.count("Two") == 3
    assert labels.count("Three") == 3


def test_add_find_and_remove_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    profile = store.add_profile("Sales Proton", "sales@proton.me")

    assert store.find("Sales Proton") == profile
    assert store.find("sales@proton.me") == profile
    assert store.find(profile.profile_id) == profile
    assert profile.provider == "proton"
    assert profile.user_data_dir.exists()

    store.remove_profile(profile.profile_id, delete_browser_data=True)
    assert store.list_profiles() == []
    assert not profile.user_data_dir.exists()


def test_add_gmail_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    profile = store.add_profile("Sales Gmail", "sales@gmail.com", provider="gmail")

    assert profile.provider == "gmail"
    assert profile.provider_label == "Gmail"
    assert store.find("Sales Gmail") == profile


def test_legacy_profiles_default_to_proton(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.registry_path.write_text(
        '[{"profile_id":"legacy-1","label":"Legacy","expected_email":"old@proton.me","user_data_dir":"'
        + str(tmp_path / "legacy").replace("\\", "/")
        + '"}]\n',
        encoding="utf-8",
    )

    profiles = store.list_profiles()
    assert len(profiles) == 1
    assert profiles[0].provider == "proton"


def test_duplicate_email_is_rejected(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.add_profile("One", "same@proton.me")

    try:
        store.add_profile("Two", "same@proton.me")
    except ValueError as exc:
        assert "already assigned" in str(exc)
    else:
        raise AssertionError("Expected duplicate email to be rejected")
