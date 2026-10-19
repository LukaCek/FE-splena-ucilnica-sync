"""Regression tests for offline status files and safe expired-session display."""
import json
import pytest

from fe_sync.status import publish_status, last_successful
from fe_sync.pages import page, render_home


def summary(status='ok', finished='2026-10-19T11:05:00+00:00'):
    return {'status':status,'finished_at':finished,
            'files':{'downloaded':8,'unchanged':214,'errors':0}}


def test_offline_status_generated_without_credentials(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    good=summary()
    publish_status(root,state,good)
    html=(root/'Spletna-ucilnica/STANJE.html').read_text()
    txt=(root/'Spletna-ucilnica/STANJE.txt').read_text()
    assert '19. 10. 2026, 11:05 UTC' in html
    assert 'Zadnja uspešna osvežitev' in txt
    assert 'MoodleSession' not in html and 'sesskey' not in txt
    assert good['last_success_at']==good['finished_at']
    publish_status(root,state,good)


def test_previous_success_retained_when_sso_expires(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    prev=summary()
    (state/'status.json').write_text(json.dumps(prev))
    bad=summary('failed','2026-10-19T13:05:00+00:00')
    bad['error_type']='MoodleError'
    bad['secret']='NEVER-EXPORT'
    publish_status(root,state,bad)
    assert bad['last_success_at']==prev['finished_at']
    html=(root/'Spletna-ucilnica/STANJE.html').read_text()
    assert 'Napaka' in html and 'MoodleError' in html
    assert 'NEVER-EXPORT' not in html


def test_user_status_file_not_overwritten(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    manual=root/'Spletna-ucilnica/STANJE.html'
    manual.parent.mkdir()
    manual.write_text('MY NOTES')
    with pytest.raises(ValueError,match='user-owned'):
        publish_status(root,state,summary())
    assert manual.read_text()=='MY NOTES'


def test_offline_home_and_course_link_to_status(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    home=render_home(root,[],{},'2026-10-19')
    assert 'href="STANJE.html"' in home
    assert 'Zadnja osvežitev' in home
    course=page('MAT','<p>test</p>','2026-10-19',status_href='../../STANJE.html')
    assert 'href="../../STANJE.html"' in course


def test_partial_does_not_count_as_success():
    prev={'status':'ok','finished_at':'2026-10-19T11:00:00+00:00'}
    assert last_successful(prev,summary(status='partial'))==prev['finished_at']
