from html.parser import HTMLParser
from pathlib import Path
import re
import urllib.parse
import pytest

from fe_sync.pages import build_site, valid_stored_files, write_generated, module_url
from fe_sync.sync import State, course_dir, folder_dir, run_sync

class TargetLinks(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.append(dict(attrs).get('href',''))

class FileResponse:
    status_code=200
    headers={'Content-Type':'application/pdf'}
    def iter_content(self,size):yield b'%PDF-1.7 test PDF bytes'
    def close(self):pass

class FakeClient:
    def __init__(self,cookie):pass
    def courses(self):
        return [{'id':4149,'title':'Matematika I','shortname':'MAT'},
                {'id':4152,'title':'Programiranje I','shortname':'PROG'}]
    def modules(self,course_id):
        if course_id==4149:
            return [{'type':'folder','id':144497,'title':'Predavanja Mapa'},
                    {'type':'forum','id':144492,'title':'Obvestila (VS) Forum'},
                    {'type':'page','id':144495,'title':'Pravila in obveznosti Stran'}]
        return [{'type':'resource','id':12345,'title':'Gradivo PDF'},
                {'type':'assign','id':456,'title':'Domača naloga'}]
    def folder_files(self,folder_id):
        from fe_sync.client import RemoteFile
        return [RemoteFile(('2026','M1vs-2026-uvod.pdf'),'/pluginfile.php/12/mod_folder/content/0/2026/M1vs-2026-uvod.pdf'),
                RemoteFile(('2026','Matematika #1 & primer.pdf'),'/pluginfile.php/12/mod_folder/content/0/2026/Matematika%20%231%20%26%20primer.pdf')]
    def get(self,*args,**kwargs):return FileResponse()


def test_generated_index_links_are_valid_and_offline(tmp_path,monkeypatch):
    monkeypatch.setattr("fe_sync.sync.discover_resource_file",lambda client,ident:None)
    root=tmp_path/'FE';root.mkdir();state=tmp_path/'state';state.mkdir()
    result=run_sync(root,state/'data.sqlite',tmp_path/'secret',client_factory=FakeClient,delay=0)
    assert result['status']=='ok',result
    assert result['navigation']['pages']==3
    assert result['navigation']['local_files_linked']==2
    assert result['navigation']['home']=='Spletna-ucilnica/index.html'
    assert result['navigation']['search']['embedded'] is True
    assert result['navigation']['search']['items'] >= 1
    assert (root/'Spletna-ucilnica/ISKANJE.html').is_file()
    home=root/'Spletna-ucilnica/index.html'
    math=root/'1-semester/01_MAT/Spletna-ucilnica/index.html'
    code=root/'1-semester/03_PROG/Spletna-ucilnica/index.html'
    for file in (home,math,code):
        assert file.is_file()
        data=file.read_text()
        assert 'Lokalna e.FE' in data
        assert not any(x in data for x in ('MoodleSession','sesskey','<iframe','<script src='))
        for href in extract_links(data):
            if not href.startswith(('http','#')):
                target=(file.parent/urllib.parse.unquote(href.split('#')[0])).resolve()
                assert target.is_file(),f'Missing target: {file} => {href}'
    assert 'M1vs-2026-uvod.pdf' in math.read_text()
    assert 'Matematika%20%231%20%26%20primer.pdf' in math.read_text()
    assert 'Samo na e.FE' in code.read_text()
    assert '/mod/assign/view.php?id=456' in code.read_text()
    assert '2 lokalnih datotek' in home.read_text()


def extract_links(data):
    parser=TargetLinks();parser.feed(data)
    return parser.links


def test_escaping_untrusted_titles_and_module_ids(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    c={'id':111,'title':'<img src=x onerror=alert(1)>','shortname':'<script>alert(2)</script>'}
    # Custom title is a path component on the fallback course destination.
    output=build_site(root,[c],{111:[{'type':'forum','id':5,'title':'<svg onload=alert(1)>'}]},[], '2026-10-19')
    html=(root/'Spletna-ucilnica/index.html').read_text()
    from fe_sync.sync import course_dir
    subject=(root/course_dir(c)/'index.html').read_text()
    assert '<img src=x' not in html and '<img src=x' not in subject
    assert '<script>alert(2)</script>' not in html
    assert '<svg onload' not in subject
    assert '&lt;svg' in subject
    assert module_url({'type':'javascript:','id':123}) == ''
    assert module_url({'type':'assign','id':'123'}) == ''
    assert output['pages']==2


def test_index_html_owned_by_user_is_not_overwritten(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    dest=root/'Spletna-ucilnica/index.html';dest.parent.mkdir()
    dest.write_text('My handwritten homepage')
    with pytest.raises(ValueError,match='Refusing'):
        write_generated(root,Path('Spletna-ucilnica/index.html'),'injected')
    assert dest.read_text()=='My handwritten homepage'


def test_tampered_database_links_not_shown(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    good=root/'1-semester/01_MAT/Spletna-ucilnica/Gradiva/Predavanja__144497/one.pdf'
    good.parent.mkdir(parents=True);good.write_bytes(b'%PDF-hi')
    bad=root/'1-semester/01_MAT/Spletna-ucilnica/Gradiva/Predavanja__144497/link.pdf'
    bad.symlink_to(tmp_path/'secret.pdf')
    (tmp_path/'secret.pdf').write_text('sensitive')
    base=Path('1-semester/01_MAT/Spletna-ucilnica/Gradiva/Predavanja__144497')
    records=[{'rel_path':str(good.relative_to(root))},{'rel_path':'../../etc/passwd'},
             {'rel_path':str(bad.relative_to(root))},{'rel_path':'1-semester/01_MAT/Zapiski/private.pdf'}]
    assert valid_stored_files(root,records,base)==[good]
