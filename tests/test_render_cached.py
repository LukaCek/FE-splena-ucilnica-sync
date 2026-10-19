import json
import sqlite3
from pathlib import Path
import pytest
from fe_sync.client import clean_section_title, parse_modules
from fe_sync.pages import MARKER
from fe_sync.sync import render_cached, course_dir

@pytest.mark.parametrize("raw,expected",[
 ("Izberi poglavje Splošno Skrči Razširi Splošno Skrči vse Razširi vse","Splošno"),
 ("Izberi poglavje Predavanja in vaje Skrči Razširi Predavanja in vaje","Predavanja in vaje"),
 ("Skrči Razširi Literatura","Literatura"),
 ("Gradiva","Gradiva"),
])
def test_clean_section(raw,expected):
    assert clean_section_title(raw)==expected

def test_parser_cleans_controls():
    html='<li id="section-0"><h3 class="sectionname">Izberi poglavje Splošno Skrči Razširi Splošno</h3><a href="/mod/forum/view.php?id=144492">Obvestila</a></li>'
    assert parse_modules(html)[0]["section_title"]=="Splošno"

def cache(tmp_path,html):
    root=tmp_path/"FE";root.mkdir()
    state=tmp_path/"state";state.mkdir()
    course={"id":4149,"title":"Matematika I","shortname":"MAT"}
    folder=root/course_dir(course);folder.mkdir(parents=True)
    (folder/"predmet.json").write_text(json.dumps({**course,"modules":[{"id":144492,"type":"forum","title":"Obvestila (VS) Forum","section_title":"Izberi poglavje Splošno Skrči Razširi Splošno"}]}))
    (state/"status.json").write_text(json.dumps({"subjects":[{"id":4149,"title":"Matematika I"}]}))
    db=sqlite3.connect(state/"files.sqlite")
    db.execute("CREATE TABLE files (rel_path TEXT NOT NULL)")
    db.commit();db.close()
    if html is not None:(folder/"index.html").write_text(html)
    return root,state,folder/"index.html"

def test_upgrade_and_repeat(tmp_path):
    root,state,html=cache(tmp_path,'<!doctype html><html><body>'+MARKER+'old</body></html>')
    assert render_cached(root,state)["pages"]==2
    assert 'class="redbar"' in html.read_text()
    assert 'Izberi poglavje' not in html.read_text()
    assert render_cached(root,state)["pages"]==2

def test_own_html_protection(tmp_path):
    root,state,html=cache(tmp_path,"PERSONAL")
    with pytest.raises(ValueError,match="Refusing to replace user-owned HTML"):
        render_cached(root,state)
    assert html.read_text()=="PERSONAL"
