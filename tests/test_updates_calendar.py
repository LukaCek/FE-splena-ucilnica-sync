"""Offline calendar dates and 30-day change history never infer missing facts."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fe_sync.calendar import (
    parse_events,fetch_calendar,calendar_ics,render_calendar,write_calendar_ics
)
from fe_sync.client import MoodleError, RemoteFile
from fe_sync.sync import State, run_sync
from fe_sync.updates import capture,record_changes,history,render_history

BASE='https://ucilnica-fe.uni-lj.si'
COURSES=[{'id':4149,'title':'Matematika I','shortname':'MAT'}]
STAMP=datetime(2026,10,19,15,0,tzinfo=timezone.utc)


def test_calendar_accepts_only_verified_times_and_known_courses():
    rows=[
        {'id':11,'courseid':4149,'timesort':1792520000,
         'name':'Oddaja <strong>naloge</strong>','eventtype':'due','modulename':'assign'},
        {'id':12,'courseid':4149,'timestart':1792610000,
         'name':'Kolokvij','eventtype':'other','modulename':'quiz'},
        {'id':13,'courseid':3791,'timesort':1792520000,'name':'Unknown course'},
        {'id':14,'courseid':4149,'timesort':'tomorrow','name':'Unverified date'},
        {'id':15,'courseid':4149,'timesort':0,'name':'Wrong timestamp'},
        {'id':11,'courseid':4149,'timesort':1792530000,'name':'Duplicate'},
    ]
    result=parse_events({'events':rows},COURSES)
    assert len(result)==2
    assert result[0]['kind']=='rok'
    assert result[0]['title']=='Oddaja naloge'
    assert result[1]['kind']=='dogodek'
    assert result[1]['source']==BASE+'/course/view.php?id=4149'


def test_calendar_missing_events_does_not_masquerade_as_empty():
    with pytest.raises(MoodleError):
        parse_events({'other':[]},COURSES)


class Response:
    status_code=200
    content=b'{"safe":true}'
    def __init__(self,events):self.events=events
    def json(self):return [{'data':{'events':self.events}}]
    def close(self):pass


class CalendarSession:
    def __init__(self):self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return Response([{'id':44,'courseid':4149,'timesort':1792520000,
                          'eventtype':'due','modulename':'assign','name':'Naloga'}])


class CalendarClient:
    base=BASE
    timeout=15
    def __init__(self):
        self.session=CalendarSession()
    def html(self,path):
        assert path=='/my/courses.php'
        return '<script>M.cfg={"sesskey":"abcdef123456"};</script>'


def test_calendar_api_is_official_read_only_and_never_follows_redirects():
    c=CalendarClient()
    found,truncated=fetch_calendar(c,COURSES,now=1792440000)
    assert len(found)==1 and not truncated
    url,kwargs=c.session.calls[0]
    assert url==BASE+'/lib/ajax/service.php'
    assert kwargs['json'][0]['methodname']=='core_calendar_get_action_events_by_timesort'
    assert kwargs['allow_redirects'] is False
    assert kwargs['json'][0]['args']['limitnum']==50


def test_calendar_exports_ics_without_untrusted_html(tmp_path):
    rows=parse_events({'events':[{'id':5,'courseid':4149,
        'timesort':1792520000,'name':'Naloga <img src=x onerror=alert(1)>',
        'eventtype':'due','modulename':'assign'}]},COURSES)
    calendar=calendar_ics(rows,STAMP)
    assert calendar.startswith('BEGIN:VCALENDAR\r\n')
    assert 'BEGIN:VEVENT' in calendar and 'DTSTART:' in calendar
    assert '<img' not in calendar
    root=tmp_path/'FE';root.mkdir()
    output=render_calendar(root,rows)
    assert output['events']==1 and output['available']
    assert (root/'Spletna-ucilnica/KOLEDAR.ics').exists()
    assert 'Koledar e.FE' in (root/'Spletna-ucilnica/KOLEDAR.html').read_text()


def test_calendar_unavailable_is_not_falsely_empty(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    info=render_calendar(root,None)
    assert not info['available']
    assert not (root/'Spletna-ucilnica/KOLEDAR.ics').exists()
    assert 'niso na voljo' in (root/'Spletna-ucilnica/KOLEDAR.html').read_text()


def test_calendar_does_not_overwrite_personal_ics(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    target=root/'Spletna-ucilnica/KOLEDAR.ics'
    target.parent.mkdir()
    target.write_text('My handwritten personal calendar')
    with pytest.raises(ValueError,match='user-owned'):
        write_calendar_ics(root,calendar_ics([],STAMP))
    assert target.read_text()=='My handwritten personal calendar'


class File:
    status_code=200
    headers={'Content-Type':'application/pdf'}
    def __init__(self,data):self.data=data
    def iter_content(self,n):yield self.data
    def close(self):pass


class Client:
    payload=b'%PDF-1.7\nv1'
    def __init__(self,secret):pass
    def courses(self):return COURSES
    def modules(self,course_id):
        return [{'id':144497,'type':'folder','title':'Vaje Mapa'}]
    def folder_files(self,folder_id):
        return [RemoteFile(('Vaje.pdf',),'/pluginfile.php/7/mod_folder/content/0/Vaje.pdf')]
    def get(self,*args,**kwargs):
        return File(self.payload)


def test_change_history_stays_after_unchanged_cycles(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    Client.payload=b'%PDF-1.7\nv1'
    first=run_sync(root,state/'files.sqlite',state/'secret',client_factory=Client,delay=0)
    assert first['status']=='ok',first
    assert first['changes']['new']==1
    assert first['changes']['updated']==0
    second=run_sync(root,state/'files.sqlite',state/'secret',client_factory=Client,delay=0)
    assert second['changes']['new']==0
    assert second['changes']['updated']==0
    assert second['changes']['recent']==1
    Client.payload=b'%PDF-1.7\nv2'
    third=run_sync(root,state/'files.sqlite',state/'secret',client_factory=Client,delay=0)
    assert third['changes']['updated']==1
    assert third['changes']['recent']==2
    html=(root/'Spletna-ucilnica/NOVOSTI.html').read_text()
    assert 'Vaje.pdf' in html and 'posodobljeno' in html
    assert 'NOVOSTI.html' in (root/'Spletna-ucilnica/index.html').read_text()
    assert 'KOLEDAR.html' in (root/'Spletna-ucilnica/index.html').read_text()


def test_history_ignores_path_traversal(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    info=render_history(root,[{'seen_at':STAMP.isoformat(),
        'kind':'novo','course':'F','label':'Secret',
        'rel_path':'../private-data'}])
    assert info['shown']==0
    assert 'Secret</a>' not in (root/'Spletna-ucilnica/NOVOSTI.html').read_text()
