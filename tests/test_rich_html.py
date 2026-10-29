"""Security and structure tests for offline Moodle HTML rendering."""
from pathlib import Path
import re

import pytest

from fe_sync.client import MoodleError
from fe_sync.rich_html import approved_link, render_moodle_content
from fe_sync.content import archive_page, page_dir
from fe_sync.pages import STYLE


BASE = "https://ucilnica-fe.uni-lj.si"
HTML = """
<header>Moodle navigation and sesskey</header>
<div class="no-overflow">
  <h3>Video gradiva za posamezno predavanje</h3>
  <table class="generaltable" onclick="evil()">
    <thead><tr>
      <th>Naslovi tem</th><th>Učbenik</th>
      <th>Zajeta predavanja</th><th>Eksperimenti</th>
    </tr></thead>
    <tbody>
      <tr><td rowspan="2">0</td><td>Uvod</td><td>
      <a href="/mod/url/view.php?id=321" onclick="steal()">Predavanje 0, ura 1</a>
      </td><td></td></tr>
      <tr><td><a href="https://www.youtube.com/watch?v=abc123">Lorentzova sila</a>
      </td><td><a href="javascript:alert(document.cookie)">Električna sila</a></td></tr>
      <tr><td colspan="4">&lt;test&gt; &amp; varno</td></tr>
    </tbody>
  </table>
  <script>doBadThings()</script>
  <form>Geslo <input type="password"></form>
</div>
<footer>Secret in Moodle footer</footer>
"""


def test_moodle_table_shape_and_real_https_links():
    output = render_moodle_content(HTML)
    assert output is not None
    assert '<table>' in output and '</table></div>' in output
    assert output.count('<tr>') == 4
    assert '<th>Naslovi tem</th>' in output
    assert '<th>Eksperimenti</th>' in output
    assert '<td rowspan="2">0</td>' in output
    assert '<td colspan="4">&lt;test&gt; &amp; varno</td>' in output
    assert 'href="https://ucilnica-fe.uni-lj.si/mod/url/view.php?id=321"' in output
    assert 'href="https://www.youtube.com/watch?v=abc123"' in output
    assert 'target="_blank" rel="noopener noreferrer"' in output
    assert 'Električna sila' in output
    assert 'javascript:' not in output
    assert 'onclick' not in output
    assert 'doBadThings' not in output
    assert 'Geslo' not in output
    assert 'Secret in Moodle footer' not in output
    assert 'Moodle navigation' not in output


@pytest.mark.parametrize('href', [
    'javascript:alert(1)',
    'data:text/html,<svg onload=alert(1)>',
    '//attacker.test/x?token=secret',
    'http://example.com/watch?v=123',
    'https://ucilnica-fe.uni-lj.si/mod/url/view.php?id=7&sesskey=SECRET',
    'https://ucilnica-fe.uni-lj.si/login/logout.php',
    'https://u:p@ucilnica-fe.uni-lj.si/x',
    '/mod/page/view.php?id=6&wstoken=secret',
    'https://example.com:8443/test',
    'https://example.com/?api_key=private',
])
def test_unsafe_links_dropped(href):
    assert approved_link(href) is None


def test_markup_escapes_injected_html_and_attribute_payloads():
    bad = ('<div class="no-overflow"><table><tr>'
           '<td style="background:url(evil)" onmouseenter="malware()">'
           '<a href="javascript:alert(1)" onclick="evil()">Dokument</a>'
           '</td></tr></table>'
           '<img src="x" onerror="steal()" alt="skica &amp; formula">'
           '<iframe src="https://evil.example">FAKE CONTENT</iframe>'
           '<p>&lt;script&gt;not real&lt;/script&gt;</p></div>')
    out = render_moodle_content(bad)
    assert out is not None
    for dangerous in ('onmouseenter','onclick','onerror','<iframe','FAKE CONTENT',
                      'javascript:', 'background:url'):
        assert dangerous not in out
    assert '&lt;script&gt;not real&lt;/script&gt;' in out
    assert '[Slika: skica &amp; formula]' in out


def test_safe_fallback_for_unknown_and_login_layouts():
    assert render_moodle_content('<div class="unrecognized">Nothing</div>') is None
    with pytest.raises(MoodleError):
        render_moodle_content('<form action="/login/index.php"><input name="username"></form>')
    with pytest.raises(MoodleError):
        render_moodle_content('x' * 4_000_001)


def test_text_blocks_and_list_without_table():
    out = render_moodle_content(
        '<div class="no-overflow"><h2>Pravila</h2>'
        '<p>Poglavje</p><ul><li>Prva</li><li>Druga</li></ul>'
        '<pre>x &lt; 2</pre></div>')
    assert '<h2>Pravila</h2>' in out
    assert '<ul><li>Prva</li><li>Druga</li></ul>' in out
    assert '<pre>x &lt; 2</pre>' in out


def test_responsive_table_styles_and_plain_fallback():
    assert '.moodle-table-scroll' in STYLE
    assert 'overflow-x:auto' in STYLE
    assert '.moodle-content.moodle-plain{white-space:pre-wrap}' in STYLE


class Stub:
    base = BASE
    def html(self, path, params):
        assert path == '/mod/page/view.php'
        assert params == {'id': 123}
        return HTML


def test_archived_page_reuses_original_table_but_md_stays_plain(tmp_path):
    root = tmp_path / 'FE'
    root.mkdir()
    course = {'id': 4151, 'title': 'Osnove elektrotehnike I'}
    module = {'id': 123, 'type': 'page',
              'title': 'Video gradiva za posamezno predavanje Stran'}
    assert archive_page(Stub(), root, course, module)
    base = (root / '1-semester/04_ELE/Spletna-ucilnica' / page_dir(module))
    html = (base / 'index.html').read_text()
    notes = (base / 'vsebina.md').read_text()
    assert '<table>' in html
    assert '<th>Naslovi tem</th>' in html
    assert 'Predavanje 0, ura 1</a>' in html
    assert '<table>' not in notes
    assert 'Video gradiva za posamezno predavanje' in notes
    assert 'Moodle navigation' not in html
    assert 'javascript:' not in html
