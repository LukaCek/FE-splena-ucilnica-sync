import json
import os
from pathlib import Path
from unittest.mock import Mock
import pytest

from fe_sync.client import MoodleClient, MoodleError, RemoteFile, safe_remote_path, parse_modules, parse_folder_files
from fe_sync.sync import course_dir, folder_dir, State, Result, store_file, run_sync, ensure_safe_parent


BASE='https://ucilnica-fe.uni-lj.si'
MODULE_HTML='''<a href="/mod/folder/view.php?id=144497">Predavanja Mapa</a>
<a href="/mod/folder/view.php?id=144501">Kolokviji Mapa</a>
<a href="/mod/assign/view.php?id=123">Oddaja naloge</a>
<a href="https://evil.test/mod/folder/view.php?id=678">evil</a>'''
FOLDER_HTML='''<a href="/pluginfile.php/12/mod_folder/content/0/2026/kol_mat1vs_2025_1.pdf?forcedownload=1">first</a>
<a href="/pluginfile.php/12/mod_folder/content/0/2026/kol_mat1vs_2025_1.pdf?forcedownload=1">duplicate</a>
<a href="https://evil.test/pluginfile.php/12/mod_folder/content/0/a.pdf">bad host</a>
<a href="/pluginfile.php/12/mod_folder/content/0/../bad.pdf">traversal</a>
<a href="/pluginfile.php/12/mod_folder/content/0/a.pdf?sesskey=SECRET">token</a>
<a href="/pluginfile.php/12/mod_folder/content/0/x.exe">exe</a>'''


def test_module_list_and_folder_parser():
    assert [(m['type'],m['id']) for m in parse_modules(MODULE_HTML)] == [('folder',144497),('folder',144501),('assign',123)]
    result=parse_folder_files(FOLDER_HTML)
    assert len(result)==1
    assert result[0].path==('2026','kol_mat1vs_2025_1.pdf')
    assert result[0].url_path.endswith('?forcedownload=1')

@pytest.mark.parametrize('value', ['../test.pdf','%2e%2e/test.pdf','foo/%2Fpasswd.pdf',
    '.env','/test.pdf','a\\b.pdf','x.pdf/','thing.exe','a%0A.pdf'])
def test_reject_unsafe_remote_path(value):
    with pytest.raises(ValueError):safe_remote_path(value)


def test_course_mapping_and_folder_naming():
    a={'title':'Matematika I','id':4149}
    b={'title':'Programiranje II','id':4161}
    assert str(course_dir(a))=='1-semester/01_MAT/Spletna-ucilnica'
    assert 'Ostali-predmeti/4161_' in str(course_dir(b))
    assert str(folder_dir({'id':144497,'title':'Predavanja Mapa'}))=='Gradiva/Predavanja__144497'


def test_output_symlink_is_rejected(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    (root/'evil').symlink_to(tmp_path)
    with pytest.raises(ValueError):ensure_safe_parent(root,Path('evil/attack.pdf'))
    with pytest.raises(ValueError):ensure_safe_parent(root,Path('../attack.pdf'))


def test_session_cookie_domain_filter_and_mode(tmp_path):
    cookie=tmp_path/'moodle-session-cookies.json'
    cookie.write_text(json.dumps({'site':BASE,'cookies':[
        {'name':'MoodleSession','value':'xxx','domain':'ucilnica-fe.uni-lj.si','path':'/'},
        {'name':'SsoSecret','value':'do-not-forward','domain':'id.uni-lj.si','path':'/'},
    ]}))
    cookie.chmod(0o600)
    client=MoodleClient(cookie)
    assert list(client.session.cookies.keys())==['MoodleSession']
    cookie.chmod(0o644)
    with pytest.raises(MoodleError, match='0600'):MoodleClient(cookie)


def test_courses_single_read_only_ajax(tmp_path,monkeypatch):
    cookie=tmp_path/'cookies.json';cookie.write_text(json.dumps({'site':BASE,'cookies':[
        {'name':'MoodleSession','value':'xxx','domain':'ucilnica-fe.uni-lj.si','path':'/'}]}));cookie.chmod(0o600)
    client=MoodleClient(cookie)
    response=Mock(status_code=200,text='<script>M.cfg={"sesskey":"abcdef1234"};</script>',
                  headers={'Content-Type':'text/html'},content=b'html')
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=None)
    monkeypatch.setattr(client,'get',lambda path,params=None:response)
    ajax=Mock(status_code=200,json=lambda:[{'data':{'courses':[{'id':4149,'fullname':'Matematika I','shortname':'47_90338'}],'nextoffset':1}}])
    monkeypatch.setattr(client.session,'post',lambda url,**kw:check_post(url,kw,ajax))
    assert client.courses()==[{'id':4149,'title':'Matematika I','shortname':'47_90338'}]

def check_post(url,kwargs,response):
    assert url==BASE+'/lib/ajax/service.php'
    assert kwargs['json'][0]['methodname']=='core_course_get_enrolled_courses_by_timeline_classification'
    assert kwargs['params']['sesskey']=='abcdef1234'
    assert kwargs['allow_redirects'] is False
    return response

class FileResponse:
    def __init__(self,data=b'%PDF-1.7\nDATA',status=200,headers=None):
        self.data=data;self.status_code=status
        self.headers=headers or {'Content-Type':'application/pdf'}
        self.closed=False
    def iter_content(self,_):
        yield self.data
    def close(self):self.closed=True

class FakeClient:
    responses=[]
    def __init__(self,cookie_file):pass
    def courses(self):return [{'id':4149,'title':'Matematika I','shortname':'47_90338'}]
    def modules(self,cid):
        assert cid==4149
        return [{'type':'folder','id':144497,'title':'Predavanja Mapa'},
                {'type':'forum','id':144492,'title':'Obvestila'}]
    def folder_files(self,cid):
        return [RemoteFile(('M1vs-2026-uvod.pdf',),'/pluginfile.php/123/mod_folder/content/0/M1vs-2026-uvod.pdf')]
    def get(self,url_path,headers=None,stream=False):
        assert url_path.startswith('/pluginfile.php/')
        return self.responses.pop(0)


def test_first_sync_incremental_modified_and_collision(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    FakeClient.responses=[FileResponse()]
    a=run_sync(root,state/'files.sqlite',tmp_path/'no-secret',client_factory=FakeClient,delay=0)
    assert a['status']=='ok' and a['files']['downloaded']==1
    dest=root/'1-semester/01_MAT/Spletna-ucilnica/Gradiva/Predavanja__144497/M1vs-2026-uvod.pdf'
    assert dest.read_bytes()==b'%PDF-1.7\nDATA'
    manifest=root/'1-semester/01_MAT/Spletna-ucilnica/predmet.json'
    assert json.loads(manifest.read_text())['modules'][1]['type']=='forum'
    FakeClient.responses=[FileResponse(status=304)]
    b=run_sync(root,state/'files.sqlite',tmp_path/'no-secret',client_factory=FakeClient,delay=0)
    assert b['files']['unchanged']==1
    FakeClient.responses=[FileResponse(b'%PDF-1.7\nNEW')]
    c=run_sync(root,state/'files.sqlite',tmp_path/'no-secret',client_factory=FakeClient,delay=0)
    assert c['files']['updated']==1 and dest.read_bytes().endswith(b'NEW')
    dest.write_bytes(b'private personal changes')
    FakeClient.responses=[]
    d=run_sync(root,state/'files.sqlite',tmp_path/'no-secret',client_factory=FakeClient,delay=0)
    assert d['files']['skipped']==1 and dest.read_bytes()==b'private personal changes'


def test_existing_user_file_is_never_replaced(tmp_path):
    root=tmp_path/'FE';root.mkdir();state=tmp_path/'state';state.mkdir()
    path=root/'1-semester/01_MAT/Spletna-ucilnica/Gradiva/Predavanja__144497/M1vs-2026-uvod.pdf'
    path.parent.mkdir(parents=True);path.write_bytes(b'user data')
    FakeClient.responses=[]
    summary=run_sync(root,state/'db.sqlite',tmp_path/'no-secret',client_factory=FakeClient,delay=0)
    assert summary['files']['skipped']==1 and path.read_bytes()==b'user data'


def test_download_rejects_fake_pdf(tmp_path):
    root=tmp_path/'FE';root.mkdir();state=tmp_path/'state';state.mkdir()
    FakeClient.responses=[FileResponse(b'<html>login</html>')]
    summary=run_sync(root,state/'db.sqlite',tmp_path/'no-secret',client_factory=FakeClient,delay=0)
    assert summary['status']=='partial' and summary['files']['errors']==1
    assert not list(root.rglob('*.pdf'))
