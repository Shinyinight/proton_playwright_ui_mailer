from pathlib import Path

from profile_store import ProfileStore


def test_add_find_and_remove_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    profile = store.add_profile("Sales Proton", "sales@proton.me")

    assert store.find("Sales Proton") == profile
    assert store.find("sales@proton.me") == profile
    assert store.find(profile.profile_id) == profile
    assert profile.user_data_dir.exists()

    store.remove_profile(profile.profile_id, delete_browser_data=True)
    assert store.list_profiles() == []
    assert not profile.user_data_dir.exists()


def test_duplicate_email_is_rejected(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.add_profile("One", "same@proton.me")

    try:
        store.add_profile("Two", "same@proton.me")
    except ValueError as exc:
        assert "already assigned" in str(exc)
    else:
        raise AssertionError("Expected duplicate email to be rejected")
