"""Security and compatibility checks for private NAS phone login."""
import json
import pytest
from scripts import nas_phone_login as phone


@pytest.mark.parametrize("address", [
    "100.64.12.4", "100.127.255.254",
    "192.168.1.120", "172.16.1.2", "10.1.2.3",
])
def test_private_bind_address(address):
    assert phone.private_ip(address) == address


@pytest.mark.parametrize("address", [
    "0.0.0.0", "127.0.0.1", "8.8.8.8",
    "169.254.3.5", "100.128.1.2", "example.com",
])
def test_refuse_public_or_invalid_bind(address):
    with pytest.raises(phone.LoginError):
        phone.private_ip(address)


def test_cookie_collection_keeps_only_official_moodle_domain(monkeypatch):
    def fake_wd(method, endpoint, payload=None, timeout=25):
        if endpoint.endswith("/url") and method == "GET":
            return {"value": "https://ucilnica-fe.uni-lj.si/my/courses.php"}
        if endpoint.endswith("/cookie"):
            return {"value": [
                {"domain": "ucilnica-fe.uni-lj.si", "name": "MoodleSession",
                 "value": "test-session", "path": "/"},
                {"domain": "idp.uni-lj.si", "name": "SSO", "value": "forbidden"},
                {"domain": "evil.example", "name": "MoodleSession", "value": "forbidden"},
            ]}
        return {"value": None}
    monkeypatch.setattr(phone, "webdriver", fake_wd)
    monkeypatch.setattr(phone.time, "sleep", lambda _: None)
    cookies = phone.get_moodle_cookies("test-id")
    assert cookies == [{
        "domain": "ucilnica-fe.uni-lj.si", "name": "MoodleSession",
        "value": "test-session", "path": "/",
    }]


def test_missing_moodle_session_is_rejected(monkeypatch):
    def fake_wd(method, endpoint, payload=None, timeout=25):
        if endpoint.endswith("/url") and method == "GET":
            return {"value": "https://ucilnica-fe.uni-lj.si/my/courses.php"}
        if endpoint.endswith("/cookie"):
            return {"value": [{"domain": "ucilnica-fe.uni-lj.si",
                               "name": "irrelevant", "value": "1"}]}
        return {"value": None}
    monkeypatch.setattr(phone, "webdriver", fake_wd)
    monkeypatch.setattr(phone.time, "sleep", lambda _: None)
    with pytest.raises(phone.LoginError, match="MoodleSession"):
        phone.get_moodle_cookies("test-id")
