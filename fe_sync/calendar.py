"""Read-only Moodle calendar action events; strictly validated timestamps.

The official core_calendar_get_action_events_by_timesort method is marked as
read-only by Moodle. Never fetch arbitrary URLs in returned event metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import unescape
import re
from urllib.parse import urlsplit

from .client import BASE, MoodleError

CALENDAR_METHOD='core_calendar_get_action_events_by_timesort'
MAX_EVENTS=200
MAX_RESPONSE=2_000_000


def _clean(value:object, limit:int=160)->str:
    if not isinstance(value,str):return ''
    # Moodle returns event names as text/escaped HTML depending on version.
    text=unescape(re.sub(r'<[^>]*>','',value))
    return re.sub(r'\s+',' ',text).strip()[:limit]


def parse_events(data:object, courses:list[dict])->list[dict]:
    """Return only bounded, verified, enrolled-course events with UTC instants."""
    if not isinstance(data,dict) or not isinstance(data.get('events'),list):
        raise MoodleError('invalid calendar event format')
    allowed={c['id']:c['title'] for c in courses if type(c.get('id')) is int}
    out=[]
    seen=set()
    for raw in data['events'][:MAX_EVENTS]:
        if not isinstance(raw,dict):continue
        ident=raw.get('id')
        course_id=raw.get('courseid')
        if type(course_id) is not int and isinstance(raw.get('course'),dict):
            course_id=raw['course'].get('id')
        when=raw.get('timesort')
        if type(when) is not int or when<=0:when=raw.get('timestart')
        name=_clean(raw.get('name'))
        if (type(ident) is not int or ident<=0 or ident>=2**31 or
            type(course_id) is not int or course_id not in allowed or
            type(when) is not int or when<946684800 or when>4102444800 or
            not name or ident in seen):continue
        seen.add(ident)
        # timesort is the Moodle action instant. Only explicitly due-labelled
        # events are called deadlines; others are calendar events.
        eventtype=_clean(raw.get('eventtype',''),40).casefold()
        module=_clean(raw.get('modulename',''),40).casefold()
        kind='rok' if eventtype in ('due','submission','submissiondue') and module in ('assign','quiz') else 'dogodek'
        out.append({'id':ident,'course_id':course_id,'course':allowed[course_id],
                    'title':name,'when':when,'kind':kind,
                    'source':f'{BASE}/course/view.php?id={course_id}'})
    return sorted(out,key=lambda row:(row['when'],row['id']))


def fetch_calendar(client,courses:list[dict],now:int|None=None)->tuple[list[dict],bool]:
    """Fetch up to four batches; truncation is explicitly reported."""
    if now is None:now=int(datetime.now(timezone.utc).timestamp())
    if not callable(getattr(client,'html',None)) or not hasattr(client,'session'):
        raise MoodleError('calendar method unavailable on this reader')
    home=client.html('/my/courses.php')
    token=re.search(r'["\x27]sesskey["\x27]\s*:\s*["\x27]([A-Za-z0-9_-]{6,128})["\x27]',home)
    if not token:raise MoodleError('missing session key for read-only calendar API')
    start=now-7*86400
    end=now+183*86400
    records=[]
    last_id=0
    truncated=False
    for batch in range(4):
        params={'timesortfrom':start,'timesortto':end,
                'aftereventid':last_id,'limitnum':50,
                'limittononsuspendedevents':True}
        r=client.session.post(client.base+'/lib/ajax/service.php',
            params={'sesskey':token[1],'info':CALENDAR_METHOD},
            json=[{'index':0,'methodname':CALENDAR_METHOD,'args':params}],
            timeout=client.timeout,allow_redirects=False)
        try:
            if r.status_code!=200:raise MoodleError('calendar read HTTP failure')
            if len(r.content)>MAX_RESPONSE:raise MoodleError('calendar response too large')
            payload=r.json()
        finally:
            r.close()
        if not isinstance(payload,list) or len(payload)!=1 or not isinstance(payload[0],dict):
            raise MoodleError('invalid calendar API envelope')
        entry=payload[0]
        if entry.get('exception'):raise MoodleError('calendar API unavailable or access denied')
        data=entry.get('data')
        if not isinstance(data,dict) or not isinstance(data.get('events'),list):
            raise MoodleError('calendar API missing event list')
        rows=data['events']
        if len(rows)>50:raise MoodleError('calendar API exceeded requested page size')
        records+=rows
        if len(rows)<50:break
        ids=[x.get('id') for x in rows if isinstance(x,dict)]
        if any(type(x) is not int or x<=last_id for x in ids) or len(ids)!=len(rows):
            truncated=True
            break
        last_id=ids[-1]
        if batch==3:truncated=True
    return parse_events({'events':records},courses),truncated


def ics_escape(value:str)->str:
    value=_clean(value,200).replace('\\','\\\\').replace(';','\\;').replace(',','\\,')
    return value.replace('\r','').replace('\n','\\n')


def calendar_ics(events:list[dict],stamp:datetime)->str:
    utc=stamp.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//FE Moodle Sync//Offline Calendar//SL',
           'CALSCALE:GREGORIAN','METHOD:PUBLISH',
           'X-FE-MOODLE-SYNC:1','X-WR-CALNAME:Lokalna e.FE']
    for row in events:
        when=datetime.fromtimestamp(row['when'],timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        lines+=['BEGIN:VEVENT',f'UID:moodle-{row["id"]}@ucilnica-fe.uni-lj.si',
                f'DTSTAMP:{utc}',f'DTSTART:{when}',
                f'SUMMARY:{ics_escape(row["title"])}',
                f'DESCRIPTION:{ics_escape("Predmet: "+row["course"]+"; vir: "+row["source"])}',
                f'URL:{row["source"]}','END:VEVENT']
    lines+=['END:VCALENDAR','']
    return '\r\n'.join(lines)


def write_calendar_ics(root,body:str)->None:
    from .sync import ensure_safe_parent
    import os,tempfile
    from pathlib import Path
    dest=ensure_safe_parent(root,Path('Spletna-ucilnica/KOLEDAR.ics'))
    if dest.exists():
        if not dest.is_file() or dest.is_symlink():
            raise ValueError('calendar destination not an owned file')
        existing=dest.read_text(encoding='utf-8')
        if 'X-FE-MOODLE-SYNC:1' not in existing:
            raise ValueError('refusing to overwrite user-owned calendar')
        if existing==body:return
        # DTSTAMP reflects the export time, but an unchanged calendar must
        # not trigger a new Syncthing transfer on every scheduled cycle.
        def without_export_stamp(value):
            return re.sub(r'^DTSTAMP:[0-9]{8}T[0-9]{6}Z$', '',
                          value.replace('\r\n','\n'), flags=re.M)
        if without_export_stamp(existing)==without_export_stamp(body):
            return
    fd,temp=tempfile.mkstemp(prefix='.fe-calendar-',dir=dest.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as stream:
            os.fchmod(stream.fileno(),0o644)
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,dest)
    finally:
        if os.path.exists(temp):os.unlink(temp)


SLOVENIA = ZoneInfo('Europe/Ljubljana')
WEEKDAYS = ('ponedeljek','torek','sreda','četrtek','petek','sobota','nedelja')
MONTHS = ('januar','februar','marec','april','maj','junij',
          'julij','avgust','september','oktober','november','december')


def local_datetime(epoch:int)->datetime:
    """DST-aware Slovenian civil time, independent of the NAS time zone."""
    return datetime.fromtimestamp(epoch,timezone.utc).astimezone(SLOVENIA)


def render_calendar(root,events:list[dict]|None,truncated:bool=False)->dict:
    from .pages import page,write_generated,e
    from pathlib import Path
    now=datetime.now(timezone.utc)
    if events is None:
        main=('<div class="breadcrumb"><a href="index.html">Moji predmeti</a> / Koledar</div>'
              '<main class="course-shell" id="content"><h1>Koledar e.FE</h1>'
              '<p>Koledarski podatki niso na voljo. Oglej si roke neposredno na e.FE.</p>'
              '<p class="muted-info">Če že obstaja datoteka KOLEDAR.ics iz prejšnje osvežitve, '
              'je lahko zastarela.</p>'
              '<p><a href="https://ucilnica-fe.uni-lj.si/calendar/view.php">Odpri koledar e.FE ↗</a></p></main>')
    else:
        groups={}
        for item in sorted(events,key=lambda row:(row['when'],row['id'])):
            dt=local_datetime(item['when'])
            group_key=dt.date().isoformat()
            groups.setdefault(group_key,[]).append((item,dt))
        sections=[]
        for date_key,items in groups.items():
            dt=items[0][1]
            header=f'{WEEKDAYS[dt.weekday()].capitalize()}, {dt.day}. {MONTHS[dt.month-1]} {dt.year}'
            blocks=[]
            for event,event_dt in items:
                kind='rok' if event['kind']=='rok' else 'dogodek'
                category='Rok oddaje' if kind=='rok' else 'Dogodek'
                clock=event_dt.strftime('%H:%M')
                tz=event_dt.tzname()  # Europe/Ljubljana => CET/CEST
                iso=event_dt.isoformat()
                blocks.append(
                    f'<div class="calendar-event" data-calendar-event data-calendar-kind="{e(kind)}">'
                    f'<time class="calendar-time" datetime="{e(iso)}">{e(clock)} {e(tz)}</time>'
                    '<span class="calendar-event-info">'
                    f'<span><a href="{e(event["source"])}" target="_blank" rel="noopener noreferrer">'
                    f'{e(event["title"])}</a>'
                    f'<span class="calendar-kind {"due" if kind=="rok" else ""}">{e(category)}</span></span>'
                    f'<small>{e(event["course"])}</small></span></div>')
            sections.append(f'<section class="calendar-day" data-calendar-day>'
                            f'<h2>{e(header)}</h2>{"".join(blocks)}</section>')
        alert=('<p class="muted-info">Opozorilo: Moodle API je vrnil več dogodkov, '
               'kot jih lahko prikažemo; seznam ni nujno popoln.</p>' if truncated else '')
        note=('<p>Prikazani so preverjeni dogodki tvojih predmetov za zadnjih 7 in naslednjih '
              '183 dni. Ure so pretvorjene v <strong>slovenski čas (Europe/Ljubljana)</strong> '
              'z upoštevanjem premika ure (CET/CEST). Vsi dogodki niso roki oddaje in ta '
              'seznam ne nadomešča uradne e.FE.</p>'
              '<p><a href="KOLEDAR.ics">Izvoz koledarja (.ics)</a> · '
              '<a href="https://ucilnica-fe.uni-lj.si/calendar/view.php" '
              'target="_blank" rel="noopener noreferrer">Izvirni koledar e.FE ↗</a></p>')
        controls=('<div class="calendar-controls" role="group" aria-label="Filtriraj koledar">'
                  '<button type="button" data-cal-filter="all" aria-pressed="true" '
                  'onclick="filterCalendarEvents(\'all\')">Vse</button>'
                  '<button type="button" data-cal-filter="rok" aria-pressed="false" '
                  'onclick="filterCalendarEvents(\'rok\')">Roki oddaje</button>'
                  '<button type="button" data-cal-filter="dogodek" aria-pressed="false" '
                  'onclick="filterCalendarEvents(\'dogodek\')">Ostali dogodki</button></div>')
        main=('<div class="breadcrumb"><a href="index.html">Moji predmeti</a> / Koledar</div>'
              '<main class="course-shell" id="content">'
              '<div class="page-title"><h1>Koledar e.FE</h1></div>'
              +note+alert+controls+
              f'<p id="calendarCount" role="status" aria-live="polite">{len(events)} dogodkov</p>'
              +''.join(sections)+
              ('<p>Za to obdobje ni preverjenih dogodkov.</p>' if not events else '')
              +'</main>')
        write_calendar_ics(root,calendar_ics(events,now))
    write_generated(root,Path('Spletna-ucilnica/KOLEDAR.html'),
                    page('Koledar',main,now.strftime('%Y-%m-%d %H:%M'),
                         home='index.html',status_href='STANJE.html',
                         search_href='ISKANJE.html',updates_href='NOVOSTI.html',
                         calendar_href='KOLEDAR.html'))
    return {'events':len(events) if events is not None else 0,
            'available':events is not None,'truncated':bool(truncated),
            'timezone':'Europe/Ljubljana'}
