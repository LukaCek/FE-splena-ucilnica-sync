"""Regression tests for read-only Moodle resource discovery and local links."""
from pathlib import Path

import pytest

from fe_sync.client import MoodleError
from fe_sync.resources import (
    RESOURCE_FILE_RE, discover_resource_file, parse_resource_file_url, portable_part,
)
from fe_sync.sync import resource_dir, run_sync
from fe_sync.pages import build_site
from fe_sync.client import RemoteFile


BASE='https://ucilnica-fe.uni-lj.si'


class ViewResponse:
    def __init__(self, code=200, *, html=b'', location='', ctype='text/html'):
        self.status_code=code
        self.headers={'Content-Type': ctype}
        if location:
            self.headers['Location']=location
        self.encoding='utf-8'
        self.data=html
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def iter_content(self, _):
        yield self.data


class Browser:
    def __init__(self, responses):
        self.responses=list(responses)
        self.visited=[]
    def get(self,url,**kwargs):
        self.visited.append((url,kwargs))
        return self.responses.pop(0)


class Reader:
    base=BASE
    host='ucilnica-fe.uni-lj.si'
    timeout=30
    def __init__(self, responses):
        self.session=Browser(responses)


def test_redirect_from_moodle_resource_to_pluginfile():
    reader=Reader([ViewResponse(303, location=
        BASE+'/pluginfile.php/987/mod_resource/content/8/uvod%20v%20OE.pdf?forcedownload=1')])
    remote=discover_resource_file(reader,144651)
    assert remote.path==('uvod v OE.pdf',)
    assert remote.url_path.startswith('/pluginfile.php/987/mod_resource/content/8/')
    assert reader.session.visited[0][0] == BASE+'/mod/resource/view.php?id=144651'
    assert reader.session.visited[0][1]['allow_redirects'] is False


def test_embedded_resource_and_encoded_slovenian_filename():
    html=b'<div><iframe src="/pluginfile.php/17/mod_resource/content/0/Matemati%C4%8Dni%20uvod.pdf"></iframe></div>'
    remote=discover_resource_file(Reader([ViewResponse(html=html)]),123)
    assert remote.path==('Matematični uvod.pdf',)


@pytest.mark.parametrize('url',[
    'https://login.uni-lj.si/pluginfile.php/17/mod_resource/content/0/a.pdf',
    'http://ucilnica-fe.uni-lj.si/pluginfile.php/17/mod_resource/content/0/a.pdf',
    '/pluginfile.php/17/mod_assign/content/0/a.pdf',
    '/pluginfile.php/17/mod_resource/content/0/../a.pdf',
    '/pluginfile.php/17/mod_resource/content/0/%2e%2e/a.pdf',
    '/pluginfile.php/17/mod_resource/content/0/a.exe',
    '/pluginfile.php/17/mod_resource/content/0/a.pdf?sesskey=secret',
    'https://ucilnica-fe.uni-lj.si:8443/pluginfile.php/17/mod_resource/content/0/a.pdf',
])
def test_unapproved_resource_paths_rejected(url):
    assert parse_resource_file_url(url) is None


def test_remote_off_origin_redirect_rejected_without_following():
    reader=Reader([ViewResponse(303,location='https://evil.example/steal')])
    with pytest.raises(MoodleError,match='not an approved'):
        discover_resource_file(reader,9)
    assert len(reader.session.visited)==1


def test_ambiguous_links_rejected():
    html=(b'<a href="/pluginfile.php/1/mod_resource/content/0/a.pdf">A</a>'
          b'<a href="/pluginfile.php/1/mod_resource/content/0/b.pdf">B</a>')
    with pytest.raises(MoodleError,match='ambiguous'):
        discover_resource_file(Reader([ViewResponse(html=html)]),9)


def test_login_and_unrelated_html_not_synced():
    login=b'<form action="/login/index.php"><input name="username"></form>'
    with pytest.raises(MoodleError,match='login'):
        discover_resource_file(Reader([ViewResponse(html=login)]),9)
    html=b'<a href="https://evil.example/mod/resource/view.php?id=1">wrong</a>'
    assert discover_resource_file(Reader([ViewResponse(html=html)]),9) is None


def test_portable_names_and_per_resource_unique_id():
    assert portable_part('1. kolokvij? 2026.pdf')=='1. kolokvij 2026.pdf'
    x=resource_dir({'id':144651,'title':'E-učbenik? Datoteka'})
    assert str(x).endswith('E-učbenik__144651')
    assert '?' not in str(x)
    assert str(resource_dir({'id':144652,'title':'E-učbenik? Datoteka'}))!=str(x)


class Download:
    status_code=200
    headers={'Content-Type':'application/pdf'}
    def __init__(self,payload=b'%PDF-1.7\nStudy material'):
        self.data=payload
    def iter_content(self,_):
        yield self.data
    def close(self):pass


class FakeClient:
    downloaded=0
    def __init__(self,session_file):
        self.base=BASE
        self.host='ucilnica-fe.uni-lj.si'
        self.timeout=30
        self.session=Browser([ViewResponse(303, location=
            '/pluginfile.php/123/mod_resource/content/0/E-u%C4%8Dbenik.pdf')])
    def courses(self):
        return [{'id':4152,'title':'Programiranje I','shortname':'PROG'}]
    def modules(self,course_id):
        assert course_id==4152
        return [{'type':'resource','id':144657,'title':'E-učbenik Datoteka','section_title':'Predavanja'}]
    def folder_files(self,module_id):
        raise AssertionError('Not a folder')
    def get(self,path,headers=None,stream=False):
        assert RESOURCE_FILE_RE.fullmatch(path.split('?',1)[0])
        self.downloaded+=1
        return Download()


def test_end_to_end_single_resource_and_offline_link(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    FakeClient.downloaded=0
    first=run_sync(root,state/'files.sqlite',tmp_path/'secret',
                   delay=0,client_factory=FakeClient)
    assert first['status']=='ok',first
    assert first['files']['downloaded']==1
    assert first['resources_found']==first['resources_processed']==1
    assert first['navigation']['local_files_linked']==1
    course_dir=root/'1-semester/03_PROG/Spletna-ucilnica'
    files=list(course_dir.glob('Gradiva/Samostojne-datoteke/**/*'))
    assert any(p.name=='E-učbenik.pdf' for p in files)
    html=(course_dir/'index.html').read_text()
    assert 'href="Gradiva/Samostojne-datoteke/E-u%C4%8Dbenik__144657/E-u%C4%8Dbenik.pdf"' in html
    assert 'data-availability="local"' in html
    assert 'MoodleSession' not in html and 'sesskey=' not in html


def test_do_not_overwrite_locally_modified_resource(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    FakeClient.downloaded=0
    run_sync(root,state/'files.sqlite',tmp_path/'secret',delay=0,client_factory=FakeClient)
    file=next(root.glob('**/E-učbenik.pdf'))
    file.write_bytes(b'my local notes')
    run=run_sync(root,state/'files.sqlite',tmp_path/'secret',delay=0,client_factory=FakeClient)
    assert run['files']['skipped']==1 and file.read_bytes()==b'my local notes'
