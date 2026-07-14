from pathlib import Path
from unittest.mock import patch

from template_engine import compose_message, load_recipients, load_templates, pick_template, render_text


def test_render_text_keeps_unknown_placeholders() -> None:
    assert render_text("Hi {{name}} {{missing}}", {"name": "Ava"}) == "Hi Ava {{missing}}"


def test_load_recipients_enforces_consent_and_blocks_do_not_contact(tmp_path: Path) -> None:
    csv_path = tmp_path / "recipients.csv"
    csv_path.write_text(
        "email,name,opt_in,do_not_contact,sender_profile\n"
        "good@example.com,Good,yes,no,Sales Proton\n"
        "no-consent@example.com,No,no,no,Sales Proton\n"
        "blocked@example.com,Blocked,yes,yes,Sales Proton\n",
        encoding="utf-8",
    )

    recipients, issues = load_recipients(csv_path)
    assert [recipient.email for recipient in recipients] == ["good@example.com"]
    assert recipients[0].sender_profile == "Sales Proton"
    assert recipients[0].template_key == "random"
    assert len(issues) == 2


def test_compose_message_adds_footer(tmp_path: Path) -> None:
    templates_path = tmp_path / "templates.json"
    templates_path.write_text(
        '{"default":{"subject":"Hello {{name}}","body":"Hi {{name}}"},'
        '"alt":{"subject":"Alt {{name}}","body":"Other {{name}}"}}',
        encoding="utf-8",
    )
    csv_path = tmp_path / "recipients.csv"
    csv_path.write_text(
        "email,name,template_key,opt_in\na@example.com,Ada,default,yes\n",
        encoding="utf-8",
    )

    templates = load_templates(templates_path)
    recipients, _ = load_recipients(csv_path)
    subject, body = compose_message(recipients[0], templates, "Reply unsubscribe to stop.")

    assert subject == "Hello Ada"
    assert body == "Hi Ada\n\nReply unsubscribe to stop."


def test_compose_message_picks_random_template(tmp_path: Path) -> None:
    templates_path = tmp_path / "templates.json"
    templates_path.write_text(
        '{"a":{"subject":"Subject A","body":"Body A"},'
        '"b":{"subject":"Subject B","body":"Body B"}}',
        encoding="utf-8",
    )
    csv_path = tmp_path / "recipients.csv"
    csv_path.write_text("email,name,opt_in\na@example.com,Ada,yes\n", encoding="utf-8")

    templates = load_templates(templates_path)
    recipients, _ = load_recipients(csv_path)

    with patch("template_engine.random.choice", return_value=templates["b"]):
        subject, body = compose_message(recipients[0], templates, "")

    assert subject == "Subject B"
    assert body == "Body B"
    assert pick_template("a", templates).key == "a"
