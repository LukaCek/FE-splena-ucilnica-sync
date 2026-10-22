"""Private sign-in portal security and end-to-end state tests (no real SSO)."""
import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fe_sync import login_portal as p


@pytest.mark.parametrize("url", [
    "http://100.76.20.2:17899",
    "http://192.168.1.120:17899",
    "http://10.0.0.5:17899",
])
def test_permit_private_ipv4_only(url):
    assert p.private_base_url(url) == url


@pytest.mark.parametrize("url", [
    "http://0.0.0.0:17899", "http://127.0.0.1:17899",
    "https://192.168.1.120:17899", "http://8.8.8.8:17899",
    "http://example.com:17899", "http://192.168.1.120:17900",
    "http://192.168.1.120:17899/path", "http://user:pass@192.168.1.120:17899",
    "not-a-url", "http://192.168.1.120:bad",
])
def test_public_malformed_or_unexpected_url_rejected(url):
    with pytest.raises(ValueError):
        p.private_base_url(url)


def test_launcher_has_one_click_redirect_but_no_moodle_secrets(tmp_path):
    state = tmp_path / "state"
    a = p.load_or_make_access(state)
    assert len(a) >= 40
    assert a == p.load_or_make_access(state)
    assert state.joinpath(p.LAUNCH_TOKEN_FILE).stat().st_mode & 0o077 == 0
    root = tmp_path / "FE"
    (root / "Spletna-ucilnica").mkdir(parents=True)
    path = p.write_launcher(root, "http://100.76.20.2:17899", a)
    text = (root / path).read_text()
    assert 'http-equiv="refresh"' in text
    assert "#access=" + a in text
    assert "/connect" in text
    assert "MoodleSession" not in text
    assert "referrer" in text
    # Rerunning doesn't replace the user's non-generated files.
    assert p.write_launcher(root, "http://100.76.20.2:17899", a) == path
    (root / path).write_text("user owned file")
    with pytest.raises(ValueError):
        p.write_launcher(root, "http://100.76.20.2:17899", a)


def test_token_file_requires_private_permissions(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    target = state / p.LAUNCH_TOKEN_FILE
    target.write_text("x" * 50)
    target.chmod(0o644)
    with pytest.raises(ValueError):
        p.load_or_make_access(state)


def test_filter_moodle_cookies_and_refuse_sso(tmp_path):
    cookies = p.filtered_cookies([
        {"domain": ".ucilnica-fe.uni-lj.si", "name": "MoodleSession",
         "value": "secure", "path": "/"},
        {"domain": "idp.uni-lj.si", "name": "SSO", "value": "bad"},
        {"domain": "evil.com", "name": "MoodleSession", "value": "bad"},
        {"domain": "ucilnica-fe.uni-lj.si", "name": "bad\ncookie", "value": "bad"},
    ])
    assert cookies == [{"domain": "ucilnica-fe.uni-lj.si",
                        "name": "MoodleSession", "value": "secure", "path": "/"}]
    assert p.filtered_cookies([{"domain": "idp.uni-lj.si", "name": "MoodleSession",
                                "value": "bad"}]) == []


def test_successful_login_triggers_sync_without_changing_cookie_inode(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    secret = tmp_path / "moodle-session.json"
    secret.write_text(json.dumps({"site": p.BASE, "cookies": [
        {"domain": "ucilnica-fe.uni-lj.si", "name": "MoodleSession",
         "value": "old", "path": "/"}]}))
    secret.chmod(0o600)
    inode = secret.stat().st_ino
    calls = []
    class Cookies:
        def clear(self): calls.append("clear")
        def set(self, name, value, **kwargs): calls.append(("set", name, value, kwargs))
    class FakeClient:
        def __init__(self, path):
            assert path == secret
            self.session = type("Session", (), {"cookies": Cookies()})()
        def courses(self): return [{"id": 4149, "title": "Matematika I"}]
    monkeypatch.setattr(p, "MoodleClient", FakeClient)
    portal = p.Portal(state, secret, tmp_path, "http://100.80.1.1:17899", "token")
    portal.verify_and_persist([{"domain": "ucilnica-fe.uni-lj.si",
                                "name": "MoodleSession", "value": "new", "path": "/"}])
    assert secret.stat().st_ino == inode
    assert json.loads(secret.read_text())["cookies"][0]["value"] == "new"
    assert (state / "sync.request").is_file()
    assert calls[0] == "clear"


def test_failed_verification_keeps_original_secret(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    secret = tmp_path / "moodle-session.json"
    secret.write_text("do-not-overwrite")
    secret.chmod(0o600)
    class Cookies:
        def clear(self): pass
        def set(self, *_args, **_kwargs): pass
    class FakeClient:
        def __init__(self, path):
            self.session = type("Session", (), {"cookies": Cookies()})()
        def courses(self): raise RuntimeError("not authenticated")
    monkeypatch.setattr(p, "MoodleClient", FakeClient)
    portal = p.Portal(state, secret, tmp_path, "http://100.80.1.1:17899", "token")
    with pytest.raises(RuntimeError):
        portal.verify_and_persist([{"domain": "ucilnica-fe.uni-lj.si",
                                    "name": "MoodleSession", "value": "new", "path": "/"}])
    assert secret.read_text() == "do-not-overwrite"
    assert not (state / "sync.request").exists()


def test_gateway_denies_browser_and_status_without_private_capability(tmp_path):
    async def run():
        root = tmp_path / "FE"
        (root / "Spletna-ucilnica").mkdir(parents=True)
        state = tmp_path / "state"
        secret = tmp_path / "cookie.json"
        app = await p.create_app(base_url="http://100.75.1.2:17899",
                                  state=state, fe_root=root, secret=secret)
        portal = app["portal"]
        async def fake_browser():
            portal.sid = "test-browser"
            portal.current_status = "login"
        portal.ensure_browser = fake_browser
        portal.close_browser = lambda: asyncio.sleep(0)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            r = await client.get("/api/status")
            assert r.status == 401
            r = await client.get("/viewer/vnc.html")
            assert r.status == 403
            r = await client.post("/api/begin", json={"access": "invalid"})
            assert r.status == 403
            token = p.load_or_make_access(state)
            r = await client.post("/api/begin", json={"access": token})
            assert r.status == 200
            assert "HttpOnly" in r.headers["Set-Cookie"]
            assert "SameSite=Strict" in r.headers["Set-Cookie"]
            r = await client.get("/api/status")
            assert r.status == 200
            assert (await r.json())["status"] == "login"
        finally:
            await client.close()
    asyncio.run(run())
