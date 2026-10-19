"""Regression tests for read-only local pages, forum announcements and attachments."""
from pathlib import Path

import pytest

from fe_sync.client import MoodleError
from fe_sync.content import (
    Attachments, DiscussionLinks, extract_body, is_announcement,
    md_escape, read_forum, read_discussion, page_dir, forum_dir,
)
from fe_sync.sync import run_sync

BASE='https://ucilnica-fe.uni-lj.si'


def test_page_extraction_discards_navigation_scripts_and_escapes_html():
    markup=('<nav>MOODLE MENI</nav>'
            '<div class="no-overflow"><p>Pravila predmeta</p>'
            '<script>alert("evil")</script><form><button>Oddaj</button></form>'
            '<p>Kolokvij je v sredo &amp; četrtek.</p></div>'
            '<footer>USER COOKIE</footer>')
    body=extract_body(markup,'page')
    assert 'Pravila predmeta' in body and 'sredo & četrtek' in body
    assert 'MOODLE MENI' not in body and 'evil' not in body
    assert 'Oddaj' not in body and 'USER COOKIE' not in body
    assert '&lt;img' in md_escape('<img onerror=evil>')


def test_unrecognized_or_login_markup_rejected():
    assert extract_body('<nav>Not authenticated chrome</nav>','page') is None
    with pytest.raises(MoodleError,match='login page'):
        extract_body('<form action="/login/index.php"><input name="username"></form>','page')




def test_forum_prefers_first_original_post_not_longest_reply():
    html=('<div class="post-content-container">Originalno obvestilo.</div>'
          '<div class="post-content-container">Daljši odgovor ' + ('nepomemben ' * 50) + '</div>')
    assert extract_body(html,'post')=='Originalno obvestilo.'

def test_forum_discover_only_same_host_token_free_discussions():
    parser=DiscussionLinks(BASE)
    parser.feed(
        '<a href="/mod/forum/discuss.php?d=124">Nova vaja</a>'
        '<a href="/mod/forum/discuss.php?d=125&amp;sesskey=secret">Token</a>'
        '<a href="https://evil.example/mod/forum/discuss.php?d=126">Evil</a>'
        '<a href="http://ucilnica-fe.uni-lj.si/mod/forum/discuss.php?d=127">HTTP</a>'
        '<a href="/mod/forum/discuss.php?d=124">Duplicate</a>')
    assert parser.items==[(124,'Nova vaja')]


def test_attachments_validate_host_and_source():
    p=Attachments(BASE,124)
    p.feed(
        '<a href="/pluginfile.php/77/mod_forum/attachment/321/Gradivo%20za%20vaje.pdf">OK</a>'
        '<a href="/pluginfile.php/77/mod_assign/attachment/321/secret.pdf">NO</a>'
        '<a href="https://evil.example/pluginfile.php/77/mod_forum/attachment/321/secret.pdf">NO</a>'
        '<a href="/pluginfile.php/77/mod_forum/attachment/321/evil.pdf?sesskey=secret">NO</a>')
    assert len(p.files)==1
    assert p.files[0].path==('tema-124','Gradivo za vaje.pdf')
    assert p.files[0].url_path.startswith('/pluginfile.php/77/mod_forum/attachment/')


def test_only_announcement_forums_mirrored():
    assert is_announcement({'type':'forum','title':'Obvestila Forum'})
    assert is_announcement({'type':'forum','title':'Forum novic'})
    assert not is_announcement({'type':'forum','title':'Razprava o nalogah'})
    assert not is_announcement({'type':'url','title':'Obvestila'})


class Pdf:
    status_code=200
    headers={'Content-Type':'application/pdf'}
    def iter_content(self,n):
        yield b'%PDF-1.7\nSafe archive test'
    def close(self):pass


class Client:
    def __init__(self,secret):pass
    base=BASE
    def courses(self):
        return [{'id':4149,'title':'Matematika I','shortname':'MAT'}]
    def modules(self,ident):
        return [
            {'id':144495,'type':'page','title':'Pravila in obveznosti Stran','section_title':'Splošno'},
            {'id':144492,'type':'forum','title':'Obvestila (VS) Forum','section_title':'Splošno'},
        ]
    def html(self,path,params):
        if path=='/mod/page/view.php':
            return '<div class="no-overflow"><p>Kolokvij ob 10:00 <b>v soboto</b>.</p></div>'
        if path=='/mod/forum/view.php':
            return '<table><a href="/mod/forum/discuss.php?d=52">Novo gradivo za vaje</a></table>'
        if path=='/mod/forum/discuss.php':
            return ('<div class="post-content-container"><p>Priloženo gradivo.</p></div>'
                    '<a href="/pluginfile.php/8/mod_forum/attachment/9/Kolokvij.pdf">PDF</a>')
        raise AssertionError(path)
    def get(self,path,headers=None,stream=False):
        assert '/mod_forum/attachment/' in path and stream is True
        return Pdf()


def test_end_to_end_page_and_announcement_attachment(tmp_path):
    root=tmp_path/'FE'
    root.mkdir()
    state=tmp_path/'state'
    state.mkdir()
    run=run_sync(root,state/'files.sqlite',tmp_path/'secret',
                 client_factory=Client,delay=0)
    assert run['status']=='ok',run
    assert run['pages_found']==run['pages_archived']==1
    assert run['announcements_found']==run['announcement_posts_archived']==1
    assert run['announcement_attachments_processed']==1
    assert run['files']['downloaded']==1
    assert run['navigation']['local_files_linked']==2  # One page and one forum entry; the PDF is nested in the forum
    base=root/'1-semester/01_MAT/Spletna-ucilnica'
    page_html=base/page_dir({'id':144495,'title':'Pravila in obveznosti Stran'})/'index.html'
    forum_path=base/forum_dir({'id':144492,'title':'Obvestila (VS) Forum'})
    assert 'Kolokvij' in page_html.read_text()
    assert 'index.html' in (base/'index.html').read_text()
    assert (forum_path/'tema-52.md').exists()
    assert (forum_path/'tema-52/Kolokvij.pdf').read_bytes().startswith(b'%PDF')
    assert 'Priloženo gradivo' in (forum_path/'tema-52.html').read_text()
    assert 'href="tema-52/Kolokvij.pdf"' in (forum_path/'tema-52.html').read_text()


def test_do_not_overwrite_user_owned_moodle_page(tmp_path):
    root=tmp_path/'FE'
    root.mkdir()
    state=tmp_path/'state'
    state.mkdir()
    base=root/'1-semester/01_MAT/Spletna-ucilnica'
    dest=base/page_dir({'id':144495,'title':'Pravila in obveznosti Stran'})
    dest.mkdir(parents=True)
    (dest/'index.html').write_text('user authored notes')
    result=run_sync(root,state/'files.sqlite',tmp_path/'secret',
                    client_factory=Client,delay=0)
    assert result['status']=='partial'
    assert result['pages_errors']==1
    assert (dest/'index.html').read_text()=='user authored notes'
