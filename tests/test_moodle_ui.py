from pathlib import Path
from html.parser import HTMLParser
import urllib.parse

from fe_sync.client import parse_modules
from fe_sync.pages import render_course, render_home, group_modules, build_site
from fe_sync.sync import course_dir, folder_dir


HTML = '''
<ul class="topics">
<li id="section-0" class="section course-section main">
 <h3 class="sectionname"><span>Splošno</span></h3>
 <div class="activity"><a href="/mod/forum/view.php?id=144492"><img src="/img/icon.png">Obvestila (VS) Forum</a></div>
</li>
<li class="section course-section" data-sectionnum="1" data-sectionid="12404">
 <h3 class="sectionname">Pravila in obveznosti</h3>
 <a href="/mod/page/view.php?id=144495"><span>Pravila in obveznosti Stran</span></a>
</li>
<li data-for="section" id="section-2">
 <div data-for="section_title"><span>Predavanja in vaje</span></div>
 <a href="/mod/folder/view.php?id=144497"><span>Predavanja Mapa</span></a>
</li>
<li id="section-3"><h3 class="sectionname">Gradiva</h3>
<a href="/mod/folder/view.php?id=144501">Kolokviji Mapa</a></li>
</ul>
<a href="https://evil.example/mod/folder/view.php?id=4">nevarno</a>
'''


def test_parser_preserves_moodle_section_titles_and_order():
    rows=parse_modules(HTML)
    assert [(x['id'],x['section_title']) for x in rows] == [
        (144492,'Splošno'),(144495,'Pravila in obveznosti'),
        (144497,'Predavanja in vaje'),(144501,'Gradiva')]
    assert [x['section_order'] for x in rows]==[0,1,2,3]
    course={'id':4149,'title':'Matematika I'}
    assert list(group_modules(course,rows))==[
        'Splošno','Pravila in obveznosti','Predavanja in vaje','Gradiva']


def test_fallback_only_for_known_mathematics_ids():
    modules=[{'type':'folder','id':144497,'title':'Predavanja Mapa'},
             {'type':'forum','id':144492,'title':'Obvestila (VS) Forum'}]
    mat=group_modules({'id':4149,'title':'Matematika I'},modules)
    assert list(mat)[:4]==['Splošno','Pravila in obveznosti','Predavanja in vaje','Gradiva']
    other=group_modules({'id':4152,'title':'Programiranje I'},modules)
    assert list(other)==['Vsebina predmeta']


def test_generated_html_has_fe_nav_collapsible_folder_and_safe_links(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    course={'id':4149,'title':'Matematika I','shortname':'MAT'}
    mod={'type':'folder','id':144497,'title':'Predavanja Mapa','section_title':'Predavanja in vaje'}
    remote={'type':'forum','id':144492,'title':'Obvestila (VS) Forum','section_title':'Splošno'}
    p=root/course_dir(course)/folder_dir(mod)/'Matematični uvod & primer.pdf'
    p.parent.mkdir(parents=True)
    p.write_bytes(b'%PDF-1.6 test')
    metadata=[{'rel_path':str(p.relative_to(root))}]
    result=build_site(root,[course],{4149:[remote,mod]},metadata,'2026-10-19')
    html=(root/course_dir(course)/'index.html').read_text()
    assert result['local_files_linked']==1
    assert 'UL<span>|</span>FE' in html
    assert 'redbar' in html
    assert '<details class="section"' in html
    assert '<details class="folder-toggle">' in html
    assert 'Predavanja in vaje' in html
    assert 'value="local"' in html and 'value="web"' in html
    assert 'Matemati%C4%8Dni%20uvod%20%26%20primer.pdf' in html
    assert 'Obvestila (VS)' in html
    assert 'MoodleSession' not in html and 'sesskey' not in html
    assert 'https://ucilnica-fe.uni-lj.si/mod/forum/view.php?id=144492' in html
    assert (root/'Spletna-ucilnica/index.html').is_file()