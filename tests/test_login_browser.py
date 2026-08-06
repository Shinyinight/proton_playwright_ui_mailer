from pathlib import Path

import login_browser


def test_candidate_paths_include_chrome_executable(monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", r"C:\\Program Files")
    monkeypatch.setenv("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\\Users\\Test\\AppData\\Local")
    monkeypatch.setattr(login_browser.shutil, "which", lambda _name: None)

    paths = [str(path) for path in login_browser._candidate_paths("chrome")]
    assert any("Google/Chrome/Application/chrome.exe" in path.replace("\\", "/") for path in paths)


def test_chromium_manual_login_uses_chrome(monkeypatch, tmp_path):
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    captured = {}

    class DummyProcess:
        pass

    monkeypatch.setattr(login_browser, "find_installed_browser", lambda channel: executable)

    def fake_popen(args, cwd):
        captured["args"] = args
        captured["cwd"] = cwd
        return DummyProcess()

    monkeypatch.setattr(login_browser.subprocess, "Popen", fake_popen)

    class Profile:
        user_data_dir = tmp_path / "profile"

    login_browser.open_normal_login_browser(Profile(), "chromium")
    assert f"--user-data-dir={Profile.user_data_dir}" in captured["args"]
    assert login_browser.PROTON_LOGIN_URL in captured["args"]


def test_gmail_manual_login_uses_gmail_url(monkeypatch, tmp_path):
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    captured = {}

    class DummyProcess:
        pass

    monkeypatch.setattr(login_browser, "find_installed_browser", lambda channel: executable)

    def fake_popen(args, cwd):
        captured["args"] = args
        captured["cwd"] = cwd
        return DummyProcess()

    monkeypatch.setattr(login_browser.subprocess, "Popen", fake_popen)

    class Profile:
        user_data_dir = tmp_path / "profile"
        provider = "gmail"

    login_browser.open_normal_login_browser(Profile(), "chrome")
    assert login_browser.GMAIL_LOGIN_URL in captured["args"]
