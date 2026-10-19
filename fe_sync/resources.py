"""Strictly read-only discovery of single-file Moodle resource activities.

Resource activities may redirect to pluginfile.php or embed a file in their HTML.
Never follows redirects, sends cookies off-origin, or treats arbitrary links as files.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
import time
import requests
from urllib.parse import parse_qs, urljoin, urlsplit

from .client import BASE, MoodleError, RemoteFile, safe_remote_path

RESOURCE_FILE_RE = re.compile(r'^/pluginfile\.php/\d+/mod_resource/content/\d+/(.+)$')
VIEW_RE = re.compile(r'^/mod/resource/view\.php$')
MAX_RESOURCE_HTML = 4_000_000


def portable_part(part: str, max_length: int = 180) -> str:
    """Portable on Android shared storage and Windows, without changing remote URLs."""
    # Each original path component is first validated by safe_remote_path.
    result = re.sub(r'[<>:"/\\|?*]', '', part)
    result = ''.join(ch for ch in result if ord(ch) >= 32 and ord(ch) != 127)
    result = result.strip().rstrip('. ')
    if not result or result in ('.', '..') or result.startswith('.'):
        raise ValueError('unportable resource filename')
    return result[:max_length].rstrip('. ')


def resource_local_path(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(portable_part(p) for p in parts)


def parse_resource_file_url(url: str, base: str = BASE) -> RemoteFile | None:
    if not isinstance(url, str) or len(url) > 2500:
        return None
    u = urlsplit(urljoin(base + '/', url))
    origin = urlsplit(base)
    if (u.scheme != 'https' or u.hostname != origin.hostname or u.port not in (None, 443)
            or u.username or u.password or u.fragment):
        return None
    match = RESOURCE_FILE_RE.fullmatch(u.path)
    if not match:
        return None
    query = parse_qs(u.query, keep_blank_values=True)
    if query and query != {'forcedownload': ['1']}:
        return None
    try:
        parts = safe_remote_path(match[1])
        resource_local_path(parts)
    except (ValueError, UnicodeError):
        return None
    return RemoteFile(parts, u.path + ('?forcedownload=1' if query else ''))


class ResourceMarkup(HTMLParser):
    """Inspect only file-bearing attributes; never execute inline Moodle JS."""
    def __init__(self, base: str = BASE):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.candidates: list[RemoteFile] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr = dict(attrs)
        fields = {
            'object': ('data',),
            'iframe': ('src',),
            'embed': ('src',),
            'a': ('href',),
        }.get(tag, ())
        for field in fields:
            remote = parse_resource_file_url(attr.get(field, ''), self.base)
            if remote:
                self.candidates.append(remote)


def discover_resource_file(client, module_id: int) -> RemoteFile | None:
    """At most two GETs to the specific Moodle resource view; returns approved file URL.

    No pluginfile payload is read here. The regular bounded store_file handles it.
    An unsupported presentation is skipped without assuming a file exists.
    """
    if type(module_id) is not int or not (0 < module_id < 2**31):
        raise ValueError('invalid resource module id')
    view = f'/mod/resource/view.php?id={module_id}'
    for _ in range(2):
        for attempt in range(3):
            try:
                response=client.session.get(client.base + view, timeout=client.timeout,
                                            allow_redirects=False, stream=True)
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt==2:
                    raise
                time.sleep(1 << attempt)
        with response:
            code = response.status_code
            if code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location', '')
                file = parse_resource_file_url(location, client.base)
                if file:
                    return file
                # Only Moodle's internal redirect=1 on the exact module is allowed.
                u = urlsplit(urljoin(client.base + '/', location))
                q = parse_qs(u.query)
                if (u.scheme == 'https' and u.hostname == client.host and u.port in (None, 443)
                        and not u.username and not u.password and VIEW_RE.fullmatch(u.path)
                        and q.get('id') == [str(module_id)] and q.get('redirect') == ['1']
                        and set(q) == {'id', 'redirect'} and view != u.path + '?' + u.query):
                    view = u.path + '?' + u.query
                    continue
                raise MoodleError('resource redirect is not an approved e.FE file')
            if code in (401, 403):
                raise MoodleError('resource access denied or session expired')
            if code != 200:
                raise MoodleError(f'resource view HTTP {code}')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                return None
            total = 0
            chunks = []
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > MAX_RESOURCE_HTML:
                    raise MoodleError('resource HTML too large')
                chunks.append(chunk)
            html = b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')
            if '/login/index.php' in html and 'name="username"' in html:
                raise MoodleError('login page instead of resource')
            parser = ResourceMarkup(client.base)
            parser.feed(html)
            unique = {item.url_path: item for item in parser.candidates}
            if len(unique) == 1:
                return next(iter(unique.values()))
            if len(unique) > 1:
                raise MoodleError('multiple ambiguous resource file links')
            return None
    raise MoodleError('resource view redirects too many times')
