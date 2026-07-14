# Proton Mail Playwright UI Mailer

A Windows desktop application that opens a visible Chrome, Edge, or Playwright Chromium window and automates the Proton Mail web interface. It enters the recipient, subject, and message body, then either leaves the message as a draft or clicks **Send**.

## Appropriate use

Use this program only for recipients who specifically agreed to receive the messages or with whom you have a valid existing business relationship. Do not use it for deceptive or unsolicited email, account-limit evasion, CAPTCHA bypass, or attempts to work around Proton restrictions.

The application deliberately:

- requires an `opt_in` or `consent` column;
- skips `do_not_contact=yes` rows;
- defaults to draft mode;
- prevents duplicate completion for the same campaign and recipient;
- applies a configurable rolling 24-hour local cap per browser profile;
- uses one fixed sender, an even split across all configured profiles, or an explicit `sender_profile` value from the CSV;
- does not automatically rotate accounts to evade provider limits.

## How it works

Each Proton Mail account receives a separate persistent browser-data folder under `data\browser_profiles`. You sign in manually once in the visible browser. Playwright then reuses the local cookies and session for later runs.

For each eligible CSV row, the automation:

1. opens `mail.proton.me`;
2. clicks **New message**;
3. enters the recipient in **To**;
4. enters the subject;
5. writes the body in Proton Mail's composer editor;
6. closes the composer after autosave in draft mode, or clicks **Send**;
7. records the result in `data\history.sqlite3`.

## 1. Install and start

1. Install Python 3.11 or newer on Windows and enable **Add Python to PATH**.
2. Extract this folder.
3. Double-click `run.bat`.
4. Use **Google Chrome** or **Microsoft Edge** when available.
5. When neither works, run `install_browser.bat` and select **Playwright Chromium** in the app.

`run.bat` creates a local `.venv` and installs the required Python packages.

## 2. Create Proton browser profiles

1. Open the **Browser profiles** tab.
2. Click **Add Proton browser profile**.
3. Enter a profile name, such as `Sales Proton`.
4. Enter the Proton Mail address expected in that profile.
5. Select the row and click **Open normal browser to sign in**.
6. Sign in manually and complete MFA or security checks.
7. Wait for the inbox and the **New message** button to appear.
8. Close the browser window.
9. Repeat for each authorized Proton Mail account.

Use one Proton identity per browser profile. The program does not request or save your password. Browser cookies and sessions are sensitive, so protect `data\browser_profiles`.

## 3. Prepare the recipient CSV

Required columns:

- `email`
- `opt_in` or `consent`

Recommended columns:

- `name`
- `company`
- `template_key`
- `sender_profile`
- `subject`
- `body`
- `do_not_contact`

Example:

```csv
email,name,company,template_key,sender_profile,subject,body,opt_in,do_not_contact
alice@example.com,Alice,Example Company,random,Sales Proton,,,yes,no
bob@example.com,Bob,Example Labs,follow_up,Support Proton,,,yes,no
```

`template_key` may be a specific template name from `templates.json`, or `random` (the default) to pick one of the templates at send time. Leave the column empty for the same random behavior.

`sender_profile` may contain the configured profile name, expected Proton address, or internal profile ID. It is required only when **CSV sender_profile** mode is selected.

A row is skipped when consent is missing, the address is invalid, `do_not_contact` is enabled, or the recipient is duplicated in the CSV.

## 4. Edit templates

`templates.json` contains reusable subject and message templates. Add as many named entries as you need; campaigns with `template_key=random` (or a blank key) choose one at random for each recipient:

```json
{
  "default": {
    "subject": "A quick note for {{company}}",
    "body": "Hi {{name}},\n\nYour approved content here."
  },
  "follow_up": {
    "subject": "Following up with {{name}}",
    "body": "Hi {{name}},\n\nYour approved follow-up here."
  }
}
```

Any CSV column can be referenced as `{{column_name}}`. A row-level `subject` or `body` overrides the selected template.

## 5. Run a campaign

1. Open the **Campaign** tab.
2. Select the recipient CSV and template JSON.
3. Enter a unique campaign ID.
4. Begin with **Drafts** mode.
5. Choose one fixed browser profile, **Split across profiles**, or **CSV sender_profile**.
6. Set a conservative local cap and delay.
7. Confirm the recipient-consent checkbox.
8. Click **Preview first eligible email**.
9. Click **Start UI automation**.

The browser remains visible while Playwright controls Proton Mail. Do not click inside the automated browser during an operation. Direct-send mode requires typing `SEND` before it begins.

## 6. History and failure screenshots

The **History** tab records draft creation, sends, skips, and failures. UI failures attempt to save a screenshot under:

```text
data\screenshots
```

Common failure causes include:

- the profile is signed out;
- Proton Mail is not using English;
- MFA or a security prompt is displayed;
- the wrong Proton account is active;
- the composer is blocked by another dialog;
- Proton changed the web interface;
- Proton refused or delayed the send operation.

## Sender-profile assignment

### Fixed profile

Every eligible recipient uses the browser profile selected in the application.

### Split across profiles

Eligible CSV rows are divided in list order into equal contiguous slices across every configured browser profile. With 3 profiles and 9 recipients, the first three go to the first profile, the next three to the second, and the last three to the third. Profiles are used in the order you added them (same order as the Browser profiles tab). Each profile still respects its own local rolling 24-hour cap.

### CSV sender_profile

Each row explicitly chooses its authorized sender profile. This supports separate brands, teams, or identities; it is not intended to rotate accounts around restrictions.

## Build an optional Windows application folder

Run `build_exe.bat`. The output is created under:

```text
dist\ProtonPlaywrightMailer
```

Playwright still needs Chrome, Edge, or its installed Chromium browser.

## Tests

```bat
.venv\Scripts\activate
python -m pip install -r requirements-dev.txt
pytest -q
```

## Important limitation

Proton Mail is a frequently updated web application. UI automation is inherently less stable than a supported API. The project uses accessibility labels, roles, placeholders, and Proton `data-testid` attributes instead of screen coordinates, but interface changes may still require selector updates in `proton_ui.py`.
