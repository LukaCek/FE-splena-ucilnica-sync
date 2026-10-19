from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
import pytest
from fe_sync.pages import build_site, valid_stored_files, write_generated, module_url, MARKER
from fe_sync.sync import course_dir, folder_dir

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            self.hrefs.append(dict(attrs).get('href',''))

def test_local_navigation_links_and_activity_types(tmp_path):
    root=tmp_path/'FE'
    root.mkdir()
    course={'id':4149,'title':'Matematika I','shortname':'MAT'}
    modules=[{'type':'folder','id':144497,'title':'Predavanja Mapa'},
             {'type':'forum','id':144492,'title':'Obvestila Forum'}]
    filename='Matematika #1 & primer.pdf'
    rel=course_dir(course)/folder_dir(modules[0])/filename
    local=root/rel
    local.parent.mkdir(parents=True)
    local.write_bytes(b'%PDF-test')
    site=build_site(root,[course],{4149:modules},[{'rel_path':str(rel)}],'2026-10-19')
    assert site['pages']==2 and site['local_files_linked']==1
    home=root/'Spletna-ucilnica/index.html'
    detail=root/course_dir(course)/'index.html'
    for document in (home,detail):
        html=document.read_text('utf-8')
        assert MARKER in html
        links=Links();links.feed(html)
        for href in links.hrefs:
            if not href.startswith(('http','#')):
                assert (document.parent/unquote(href)).resolve().is_file()
    assert 'Matematika%20%231%20%26%20primer.pdf' in detail.read_text()
    assert 'Samo na e.FE' in detail.read_text()
    assert '1 lokalnih datotek' in home.read_text()

def test_html_escapes_remote_content_and_no_remote_scripts(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    course={'id':12,'title':'<img src=x onerror=alert(1)>','shortname':'x'}
    modules=[{'type':'page','id':5,'title':'<svg onload=alert(1)>'}]
    build_site(root,[course],{12:modules},[],'2026')
    detail=(root/course_dir(course)/'index.html').read_text()
    assert '<svg onload' not in detail
    assert '<img src=x' not in detail
    assert '&lt;svg' in detail
    assert '<script src=' not in detail
    assert module_url({'type':'javascript:','id':3})==''
    assert module_url({'type':'assign','id':'3'})==''

def test_no_overwriting_manually_created_html(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    dest=root/'Spletna-ucilnica/index.html'
    dest.parent.mkdir()
    dest.write_text('My own homepage')
    with pytest.raises(ValueError,match='Refusing'):
        write_generated(root,Path('Spletna-ucilnica/index.html'),'generated')
    assert dest.read_text()=='My own homepage'

def test_reject_file_links_outside_module_or_via_symlink(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    base=Path('1-semester/01_MAT/Spletna-ucilnica/Gradiva/Predavanja__144497')
    good=root/base/'one.pdf'
    good.parent.mkdir(parents=True)
    good.write_bytes(b'%PDF-')
    outside=tmp_path/'private.pdf'
    outside.write_bytes(b'private')
    linked=root/base/'link.pdf'
    linked.symlink_to(outside)
    rows=[{'rel_path':str(base/'one.pdf')},
          {'rel_path':str(base/'link.pdf')},
          {'rel_path':'../private.pdf'},
          {'rel_path':'1-semester/01_MAT/Zapiski/notes.pdf'}]
    assert valid_stored_files(root,rows,base)==[good]
