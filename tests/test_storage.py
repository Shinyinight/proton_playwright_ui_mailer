from pathlib import Path

from storage import HistoryStore


def test_history_duplicate_and_profile_count(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.sqlite3")
    assert not store.already_completed("campaign", "person@example.com")
    assert store.operations_last_24h("profile-1") == 0

    store.record(
        campaign_id="campaign",
        recipient="person@example.com",
        profile_id="profile-1",
        profile_label="Sales Proton",
        subject="Hello",
        mode="draft",
        status="draft_created",
    )

    assert store.already_completed("campaign", "person@example.com")
    assert store.operations_last_24h("profile-1") == 1
    rows = store.recent()
    assert rows[0]["profile_label"] == "Sales Proton"
