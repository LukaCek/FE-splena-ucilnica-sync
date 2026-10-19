"""beta1.7: offline global search, favorites, local calendar and safety."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fe_sync.calendar import local_datetime, parse_events, render_calendar
from fe_sync.content import page_dir, write_note
from fe_sync.pages import MARKER, SCRIPT, build_site, page, render_home, write_generated
from fe_sync.search import file_category, render_search, search_entries
from fe_sync.sync import course_dir

COURSE={'id':4149,'title':'Matematika I','shortname':'MAT'}
BASE='https://ucilnica-fe.uni-lj.si'


def setup_indexed_course(root):
    course_base=course_dir(COURSE)
    write_generated(root,course_base/'index.html',page('Matematika I','<main>Moodle</main>','2026-10-19'))
    pdf=course_base/'Gradiva/Predavanja__10/Škraba-kolokvij.pdf'
    pdf.parent.mkdir(parents=True,exist_ok=True)
    (root/pdf).write_bytes(b'%PDF-1.7\nexample')
    mod={'id':22,'type':'page','title':'Govorilne ure Stran'}
    note=course_base/page_dir(mod)
    write_note(root,note/'vsebina.md','Govorilne ure','Termini pri profesorju v kabinetu.',f'{BASE}/mod/page/view.php?id=22')
    write_generated(root,note/'index.html',page('Govorilne ure','<main>Ure</main>','2026-10-19'))
    modules=[mod,{'id':33,'type':'quiz','title':'Kviz preverjanja'},
             {'id':10,'type':'folder','title':'Predavanja Mapa'}]
    records=[{'rel_path':pdf.as_posix(),'remote_key':'4149/10/Škraba-kolokvij.pdf'}]
    return modules,records


def test_one_html_contains_searchable_pdf_page_and_online_quiz(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    modules,records=setup_indexed_course(root)
    info=render_search(root,[COURSE],{4149:modules},records,'2026-10-19T00:00:00Z')
    assert info['embedded'] and info['items']>=4
    text=(root/'Spletna-ucilnica/ISKANJE.html').read_text('utf-8')
    assert 'Škraba-kolokvij.pdf' in text
    assert 'Termini pri profesorju' in text
    assert 'Kviz preverjanja' in text
    assert 'data-kind="pdf"' in text and 'data-kind="notes"' in text
    assert 'data-kind="online"' in text
    assert 'id="globalSearch"' in text and 'oninput="filterGlobalResults()"' in text
    assert 'id="searchCourse"' in text and 'id="searchKind"' in text
    assert 'fetch(' not in text and 'XMLHttpRequest' not in text


def test_untracked_user_files_and_symlinks_never_become_results(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    modules,records=setup_indexed_course(root)
    private=tmp_path/'secrets.pdf'
    private.write_bytes(b'secret')
    base=root/course_dir(COURSE)
    (base/'private-user-notes.txt').write_text('Never index this')
    (base/'unsafe.pdf').symlink_to(private)
    records+=[{'rel_path':(course_dir(COURSE)/'unsafe.pdf').as_posix()}]
    entries=search_entries(root,[COURSE],{4149:modules},records)
    labels=' '.join(row['label'] for row in entries)
    assert 'secrets.pdf' not in labels and 'private-user-notes.txt' not in labels
    assert 'unsafe.pdf' not in labels


def test_global_search_never_overwrites_user_authored_html(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    directory=root/'Spletna-ucilnica'
    directory.mkdir()
    (directory/'ISKANJE.html').write_text('user edited notes')
    with pytest.raises(ValueError,match='user-owned HTML'):
        render_search(root,[],{},[],'2026-10-19')
    assert (directory/'ISKANJE.html').read_text()=='user edited notes'


def test_home_favorites_controls_and_offline_js(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    html=render_home(root,[COURSE],{4149:3},'2026-10-19')
    assert 'data-course-id="4149"' in html
    assert 'class="favorite-button"' in html
    assert 'aria-pressed="false"' in html
    assert 'id="courseGrid"' in html
    assert 'toggleFavorite' in html
    assert 'localStorage' in SCRIPT
    assert 'fetch(' not in SCRIPT
    assert 'ISKANJE.html' in html


def test_navigation_generates_static_search_without_extra_remote_call(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    modules,records=setup_indexed_course(root)
    result=build_site(root,[COURSE],{4149:modules},records,'2026-10-19')
    assert result['pages']==2
    assert result['search']['items']>=4
    assert 'Išči gradiva' in (root/course_dir(COURSE)/'index.html').read_text()
    assert (root/'Spletna-ucilnica/ISKANJE.html').exists()


def test_utc_events_render_dst_aware_in_slovenian_local_time(tmp_path):
    winter=int(datetime(2026,1,15,12,0,tzinfo=timezone.utc).timestamp())
    summer=int(datetime(2026,7,15,12,0,tzinfo=timezone.utc).timestamp())
    assert local_datetime(winter).strftime('%H:%M %Z')=='13:00 CET'
    assert local_datetime(summer).strftime('%H:%M %Z')=='14:00 CEST'
    events=parse_events({'events':[
        {'id':101,'courseid':4149,'timesort':winter,'name':'Vaja','eventtype':'other'},
        {'id':102,'courseid':4149,'timesort':summer,'name':'Oddaja','eventtype':'due','modulename':'assign'},
    ]},[COURSE])
    root=tmp_path/'FE';root.mkdir()
    result=render_calendar(root,events)
    assert result['timezone']=='Europe/Ljubljana'
    html=(root/'Spletna-ucilnica/KOLEDAR.html').read_text('utf-8')
    assert '13:00 CET' in html and '14:00 CEST' in html
    assert 'januar' in html and 'julij' in html
    assert 'filterCalendarEvents' in html
    assert 'ISKANJE.html' in html
    assert 'UTC' not in html.split('Izvoz koledarja')[0].split('Europe/Ljubljana')[-1]


def test_files_have_friendly_type_filters():
    assert file_category(Path('naloga.pdf'))==('pdf','PDF')
    assert file_category(Path('primer.py'))[0]=='code'
    assert file_category(Path('posnetek.zip'))[0]=='archive'
