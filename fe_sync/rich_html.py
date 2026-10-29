"""Allowlisted rendering for offline Moodle page content.

The source HTML is untrusted: only explicitly identified content containers
are selected, scripts/forms/embed media are removed, and all output HTML is
rebuilt from a small list of safe tags. No source attributes are copied.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from .client import BASE, MoodleError

MAX_SOURCE = 4_000_000
MAX_OUTPUT = 250_000
VOID = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                  'input', 'link', 'meta', 'source', 'track', 'wbr'})
CONTAINERS = {'no-overflow': 0, 'page-content': 1, 'generalbox': 2}
SAFE_TAGS = frozenset({
    'p', 'br', 'div', 'span', 'strong', 'b', 'em', 'i', 'u',
    'small', 'sub', 'sup', 'code', 'pre', 'blockquote',
    'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
    'caption', 'colgroup', 'col', 'dl', 'dt', 'dd', 'a', 'hr',
})
# Scripts and controls may contain credentials or interactive Moodle state.
DROP_SUBTREE = frozenset({
    'script', 'style', 'form', 'button', 'select', 'textarea', 'iframe',
    'object', 'embed', 'svg', 'nav', 'noscript', 'template', 'canvas',
    'video', 'audio',
})
SECRET_QUERY = frozenset({
    'sesskey', 'token', 'wstoken', 'password', 'secret', 'auth',
    'session', 'ticket', 'access_token', 'apikey', 'api_key',
})


def approved_link(href: str | None, base: str = BASE) -> str | None:
    """Make explicitly clicked HTTPS links usable offline without copying tokens."""
    if not href or not isinstance(href, str) or len(href) > 1800:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in href):
        return None
    try:
        u = urlsplit(urljoin(base + '/', href))
        if (u.scheme != 'https' or not u.hostname or u.username or u.password
            or u.port not in (None, 443) or len(u.query) > 850):
            return None
        keys = {key.casefold() for key, _ in parse_qsl(u.query, keep_blank_values=True)}
        if keys & SECRET_QUERY:
            return None
        if any(token in u.path.casefold() for token in ('/login/logout', '/auth/logout')):
            return None
        return u.geturl()
    except ValueError:
        return None


class MoodleContentSelector(HTMLParser):
    """Capture only one identified Moodle activity body (never site chrome)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []
        self.preference = 99
        self.candidates: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text() or '')
            if tag not in VOID:
                self.depth += 1
            return
        if tag not in ('div', 'section', 'article'):
            return
        classes = set((dict(attrs).get('class') or '').split())
        priorities = [value for key, value in CONTAINERS.items() if key in classes]
        if priorities:
            self.depth = 1
            self.preference = min(priorities)
            self.parts = []

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text() or '')

    def handle_data(self, data):
        if self.depth:
            self.parts.append(escape(data))

    def handle_entityref(self, name):
        if self.depth:
            self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        if self.depth:
            self.parts.append('&#' + name + ';')

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in VOID:
            return
        self.depth -= 1
        if self.depth:
            self.parts.append('</' + tag + '>')
        else:
            self.candidates.append((self.preference, ''.join(self.parts)))
            self.parts = []

    def best(self):
        if not self.candidates:
            return None
        best_priority = min(p for p, _ in self.candidates)
        return max((part for p, part in self.candidates if p == best_priority),
                   key=len)


class SafeFragment(HTMLParser):
    """Rebuild structured HTML; never expose Moodle's raw markup or JS."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.open_tags: list[tuple[str, str]] = []
        self.dropped = 0

    def handle_starttag(self, tag, attrs):
        if self.dropped:
            if tag not in VOID:
                self.dropped += 1
            return
        if tag in DROP_SUBTREE:
            if tag not in VOID:
                self.dropped = 1
            return
        attr = dict(attrs)
        if tag == 'img':
            label = (attr.get('alt') or '').strip()[:160]
            if label:
                self.parts.append('<span class="moodle-image-alt">[Slika: ' +
                                  escape(label) + ']</span>')
            return
        if tag not in SAFE_TAGS:
            if tag not in VOID:
                self.open_tags.append((tag, ''))
            return

        start = '<' + tag + '>'
        end = '</' + tag + '>'
        if tag == 'a':
            href = approved_link(attr.get('href'), self.base)
            if href:
                start = ('<a href="' + escape(href, quote=True) +
                         '" target="_blank" rel="noopener noreferrer">')
            else:
                start, end = '', ''
        elif tag in ('td', 'th'):
            pieces = []
            for key in ('rowspan', 'colspan'):
                val = str(attr.get(key) or '')
                if val.isascii() and val.isdecimal() and 1 <= int(val) <= 100:
                    pieces.append(' ' + key + '="' + val + '"')
            if tag == 'th' and attr.get('scope') in ('row', 'col'):
                pieces.append(' scope="' + attr['scope'] + '"')
            start = '<' + tag + ''.join(pieces) + '>'
        elif tag == 'table':
            start = '<div class="moodle-table-scroll"><table>'
            end = '</table></div>'
        elif tag in ('div', 'span'):
            # Drop the remote CSS classes and inline styling entirely.
            start, end = '', ''
        elif tag == 'col':
            start, end = '<col>', ''
        elif tag in ('br', 'hr'):
            start, end = '<' + tag + '>', ''

        if start:
            self.parts.append(start)
        if tag not in VOID:
            self.open_tags.append((tag, end))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.dropped:
            self.parts.append(escape(data))

    def handle_endtag(self, tag):
        if self.dropped:
            if tag not in VOID:
                self.dropped -= 1
            return
        if tag in VOID:
            return
        # Close only through a matching open tag. Malformed input cannot emit
        # unbalanced closing tags or break out of the enclosing offline page.
        for pos in range(len(self.open_tags)-1, -1, -1):
            if self.open_tags[pos][0] == tag:
                while len(self.open_tags) > pos:
                    _, closing = self.open_tags.pop()
                    if closing:
                        self.parts.append(closing)
                break

    def finish(self):
        while self.open_tags:
            _, closing = self.open_tags.pop()
            if closing:
                self.parts.append(closing)
        result = ''.join(self.parts).strip()
        return result if result and len(result) <= MAX_OUTPUT else None


def render_moodle_content(markup: str, base: str = BASE) -> str | None:
    """Return a safe rich fragment, or None for unknown/unsafe Moodle layouts."""
    if not isinstance(markup, str) or len(markup) > MAX_SOURCE:
        raise MoodleError('content HTML too large')
    if '/login/index.php' in markup and 'name="username"' in markup:
        raise MoodleError('login page instead of content')
    capture = MoodleContentSelector()
    capture.feed(markup)
    fragment = capture.best()
    if fragment is None:
        return None
    safe = SafeFragment(base)
    safe.feed(fragment)
    return safe.finish()
