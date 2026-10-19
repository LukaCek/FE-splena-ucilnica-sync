"""Transient GET disruptions must not erase previously synchronized Moodle data."""
import json
from pathlib import Path

import pytest
import requests

from fe_sync.client import MoodleClient
from fe_sync.resources import discover_resource_file
from fe_sync.sync import atomic_json, run_sync

BASE = 'https://ucilnica-fe.uni-lj.si'


class Response:
    status_code = 304
    headers = {}
    def close(self):
        pass


def test_read_only_get_retries_connection_reset(tmp_path, monkeypatch):
    cookie = tmp_path / 'session.json'
    cookie.write_text(json.dumps({'site': BASE, 'cookies': [
        {'domain': 'ucilnica-fe.uni-lj.si', 'name': 'MoodleSession', 'value': 'test'}]}))
    cookie.chmod(0o600)
    client = MoodleClient(cookie)
    results = [requests.ConnectionError('reset'), requests.Timeout('timeout'), Response()]
    hits = []

    def get(*args, **kwargs):
        hits.append(kwargs)
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client.session, 'get', get)
    monkeypatch.setattr('fe_sync.client.time.sleep', lambda _: None)
    assert client.get('/course/view.php', {'id': 3791}).status_code == 304
    assert len(hits) == 3
    assert all(h['allow_redirects'] is False for h in hits)


def test_failed_get_retries_are_bounded(tmp_path, monkeypatch):
    cookie = tmp_path / 'session.json'
    cookie.write_text(json.dumps({'site': BASE, 'cookies': [
        {'domain': 'ucilnica-fe.uni-lj.si', 'name': 'MoodleSession', 'value': 'test'}]}))
    cookie.chmod(0o600)
    client = MoodleClient(cookie)
    hits = []
    def fail(*args, **kwargs):
        hits.append(1)
        raise requests.ConnectionError('offline')
    monkeypatch.setattr(client.session, 'get', fail)
    monkeypatch.setattr('fe_sync.client.time.sleep', lambda _: None)
    with pytest.raises(requests.ConnectionError):
        client.get('/course/view.php', {'id': 3791})
    assert len(hits) == 3


class Redirect:
    status_code = 302
    headers = {'Location': '/pluginfile.php/123/mod_resource/content/0/uvod.pdf'}
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass


class ResourceSession:
    def __init__(self):
        self.calls = 0
    def get(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls <= 2:
            raise requests.ConnectionError('server closed connection')
        return Redirect()


def test_resource_view_retries_without_following_redirects(monkeypatch):
    session = ResourceSession()
    client = type('Client', (), dict(session=session, base=BASE, timeout=30,
                                     host='ucilnica-fe.uni-lj.si'))()
    monkeypatch.setattr('fe_sync.resources.time.sleep', lambda _: None)
    remote = discover_resource_file(client, 144657)
    assert remote.path == ('uvod.pdf',)
    assert session.calls == 3


class FlakyCourseClient:
    interrupt_last = False
    disconnect_folder = False
    def __init__(self, *_):
        pass
    def courses(self):
        return [
            {'id': 4152, 'title': 'Programiranje I', 'shortname': 'PROG'},
            {'id': 3791, 'title': 'Varstvo pri delu', 'shortname': 'VZD'},
        ]
    def modules(self, ident):
        if ident == 3791 and self.interrupt_last:
            raise requests.ConnectionError('RemoteDisconnected')
        if ident == 4152:
            return [{'id': 144497, 'type': 'folder', 'title': 'Predavanja Mapa'}]
        return [{'id': 128162, 'type': 'forum', 'title': 'Obvestila Forum'}]
    def folder_files(self, ident):
        if self.disconnect_folder:
            raise requests.Timeout('temporary folder timeout')
        return []


def test_last_course_disconnect_retains_all_navigation_and_state(tmp_path):
    root = tmp_path / 'FE'
    root.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    FlakyCourseClient.interrupt_last = False
    first = run_sync(root, state/'files.sqlite', state/'secret', delay=0,
                     client_factory=FlakyCourseClient)
    assert first['status'] == 'ok'
    assert first['navigation']['pages'] == 3
    atomic_json(state, Path('status.json'), first)

    FlakyCourseClient.interrupt_last = True
    try:
        second = run_sync(root, state/'files.sqlite', state/'secret', delay=0,
                          client_factory=FlakyCourseClient)
        assert second['status'] == 'partial'
        assert second['courses_unavailable'] == second['courses_from_cache'] == 1
        assert second['navigation']['pages'] == 3  # Not 2: cached VZD retained
        assert second['last_success_at'] == first['finished_at']
        assert (root/'Spletna-ucilnica/Ostali-predmeti/3791_Varstvo_pri_delu/index.html').exists()
        assert any(s.get('cached') for s in second['subjects'])
    finally:
        FlakyCourseClient.interrupt_last = False


def test_folder_connection_error_keeps_navigation(tmp_path):
    root = tmp_path / 'FE'
    root.mkdir()
    FlakyCourseClient.interrupt_last = False
    FlakyCourseClient.disconnect_folder = True
    try:
        out = run_sync(root, tmp_path/'files.sqlite', tmp_path/'secret', delay=0,
                       client_factory=FlakyCourseClient)
        assert out['status'] == 'partial'
        assert out['folders_unavailable'] == 1
        assert out['navigation']['pages'] == 3
    finally:
        FlakyCourseClient.disconnect_folder = False


def test_uncached_course_skipped_not_invented(tmp_path):
    root = tmp_path / 'FE'
    root.mkdir()
    FlakyCourseClient.interrupt_last = True
    try:
        out = run_sync(root, tmp_path/'files.sqlite', tmp_path/'secret', delay=0,
                       client_factory=FlakyCourseClient)
        assert out['status'] == 'partial'
        assert out['courses_unavailable'] == 1
        assert out.get('courses_from_cache', 0) == 0
        assert out['navigation']['pages'] == 2
    finally:
        FlakyCourseClient.interrupt_last = False
