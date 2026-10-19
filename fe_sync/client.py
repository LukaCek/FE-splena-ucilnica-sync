"""Strict, source-limited Moodle reader. No submission or mutation APIs."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, parse_qs, unquote, urlencode
import json
import re
import requests

BASE = 'https://ucilnica-fe.uni-lj.si'
COURSES_FUNCTION = 'core_course_get_enrolled_courses_by_timeline_classification'
MODULE_RE = re.compile(r'^/mod/([a-z][a-z0-9_]*)/view\.php$')
FOLDER_FILE_RE = re.compile(r'^/pluginfile\.php/\d+/mod_folder/content/\d+/(.+)$')
ALLOWED_SUFFIX = frozenset({'.pdf','.doc','.docx','.ppt','.pptx','.xls','.xlsx','.txt',
    '.zip','.rar','.7z','.odt','.ods','.odp','.csv','.png','.jpg','.jpeg','.gif',
    '.svg','.py','.ipynb','.c','.cpp','.h','.java','.tex','.md','.json'})

class MoodleError(RuntimeError):
    pass


def safe_part(name: str, max_length: int = 140) -> str:
    if not isinstance(name, str) or not name or len(name) > 250:
        raise ValueError('invalid path component')
    name = name.strip().rstrip('. ')
    if (not name or name in ('.','..') or name.startswith('.') or
        '/' in name or '\\' in name or ':' in name or
        any(ord(c)<32 or ord(c)==127 for c in name)):
        raise ValueError('unsafe path component')
    return name[:max_length]


def safe_remote_path(path: str) -> tuple[str, ...]:
    # Must reject traversal after percent-decoding (including encoded slashes).
    parts = tuple(safe_part(unquote(p), 180) for p in path.split('/'))
    if len(parts) > 18 or not parts or len(path) > 1200:
        raise ValueError('remote path too long')
    if any(p != unquote(raw) for p,raw in zip(parts,path.split('/'))):
        raise ValueError('truncated or modified remote path')
    if PurePosixPath(parts[-1]).suffix.lower() not in ALLOWED_SUFFIX:
        raise ValueError('unsupported file type')
    return parts


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href=None;self.label=[];self.items=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.href=dict(attrs).get('href');self.label=[]
    def handle_data(self,data):
        if self.href is not None:self.label.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.href is not None:
            label=re.sub(r'\s+',' ',' '.join(self.label)).strip()[:180]
            self.items.append((self.href,label));self.href=None;self.label=[]


def clean_section_title(value: str) -> str:
    """Strip Moodle control labels from course section headings."""
    label=re.sub(r'\s+', ' ', value or '').strip()
    prefix='Izberi poglavje '
    if label.startswith(prefix):
        label=label[len(prefix):]
        label=label.split(' Skrči Razširi ',1)[0]
    elif label.startswith('Skrči Razširi '):
        label=label[len('Skrči Razširi '):]
    if not label or label in ('Skrči','Razširi','Skrči vse','Razširi vse'):
        return ''
    return label[:180].strip()


class CourseOutlineParser(HTMLParser):
    """Extract Moodle course sections and module links without executing remote JS.

    Moodle 3/4/5 themes vary; recognize explicit section wrappers rather than
    assigning guessed section titles. Empty section metadata is not required by
    the downloader, and modules keep the order seen on the original page.
    """
    def __init__(self, base: str = BASE):
        super().__init__(convert_charrefs=True)
        self.host=urlsplit(base).hostname
        self.base=base
        self.stack=[]
        self.sections=[]
        self.modules={}
        self.link=None
        self.heading=None

    def _current_section(self):
        for frame in reversed(self.stack):
            if frame['section'] is not None:
                return frame['section']
        return None

    @staticmethod
    def _section_id(attrs):
        classes=set(attrs.get('class','').casefold().split())
        identity=attrs.get('id','')
        is_section=(bool(re.fullmatch(r'section-[0-9]+',identity)) or
                    attrs.get('data-for')=='section' or
                    ('course-section' in classes and attrs.get('data-sectionnum','').isdigit()) or
                    (attrs.get('data-sectionid','').isdigit() and 'section' in classes))
        return identity if is_section else None

    def handle_starttag(self,tag,raw_attrs):
        # HTML void elements (img, input, br, ...) never receive closing tags.
        # Without this they would corrupt nested section and link scopes.
        if tag in {'img','br','hr','input','meta','link','source','wbr','area','base','embed','track','col'}:
            return
        attrs=dict(raw_attrs)
        sid=self._section_id(attrs)
        section=None
        if sid is not None:
            section={'title':'','order':len(self.sections)}
            self.sections.append(section)
        self.stack.append({'tag':tag,'section':section})
        current=self._current_section()
        classes=set(attrs.get('class','').casefold().split())
        if (current is not None and self.heading is None and tag in ('h2','h3','h4','span','div') and
            (classes.intersection({'sectionname','section-title','sectionname-wrapper'}) or
             attrs.get('data-for') in ('section_title','section-title'))):
            self.heading={'tag':tag,'section':current,'depth':len(self.stack),'text':[]}
        if tag=='a':
            self.link={'depth':len(self.stack),'href':attrs.get('href',''),'text':[], 'section':current}

    def handle_data(self,data):
        if self.heading is not None:self.heading['text'].append(data)
        if self.link is not None:self.link['text'].append(data)

    def handle_endtag(self,tag):
        # Maintain nested tags and prevent a stray tag from clearing outer frames.
        if self.link is not None and tag=='a' and len(self.stack)==self.link['depth']:
            entry=self.link
            u=urlsplit(urljoin(self.base+'/',entry['href']))
            if (u.scheme=='https' and u.hostname==self.host and u.port in (None,443)
                and not u.username and not u.password):
                matched=MODULE_RE.fullmatch(u.path)
                cid=parse_qs(u.query).get('id',[''])[0]
                if matched and cid.isascii() and cid.isdecimal() and 0<int(cid)<2**31:
                    key=(matched[1],int(cid))
                    label=re.sub(r'\s+',' ',' '.join(entry['text'])).strip()[:180]
                    data={'type':matched[1],'id':int(cid),'title':label}
                    if entry['section'] is not None:
                        data['_section']=entry['section']
                    self.modules[key]=data
            self.link=None
        if (self.heading is not None and tag==self.heading['tag'] and
            len(self.stack)==self.heading['depth']):
            label=re.sub(r'\s+',' ',' '.join(self.heading['text'])).strip()
            if label and not self.heading['section']['title']:
                self.heading['section']['title']=label[:180]
            self.heading=None
        if self.stack and self.stack[-1]['tag']==tag:self.stack.pop()

    def result(self):
        output=[]
        for value in self.modules.values():
            current=value.pop('_section',None)
            if current is not None and clean_section_title(current['title']):
                value['section_title']=clean_section_title(current['title'])
                value['section_order']=current['order']
            output.append(value)
        return output


def parse_modules(markup: str, base: str = BASE) -> list[dict]:
    parser=CourseOutlineParser(base)
    parser.feed(markup)
    return parser.result()


@dataclass(frozen=True)
class RemoteFile:
    path: tuple[str,...]
    url_path: str


def parse_folder_files(markup: str, base: str = BASE) -> list[RemoteFile]:
    parser=Links();parser.feed(markup)
    host=urlsplit(base).hostname
    result={}
    for href,_ in parser.items:
        u=urlsplit(urljoin(base+'/',href))
        if (u.scheme!='https' or u.hostname!=host or u.port not in (None,443)
            or u.username or u.password or u.fragment):continue
        m=FOLDER_FILE_RE.fullmatch(u.path)
        if not m:continue
        q=parse_qs(u.query,keep_blank_values=True)
        if q and q!={'forcedownload':['1']}:continue
        try: parts=safe_remote_path(m[1])
        except (ValueError,UnicodeError):continue
        # Carry only the validated path and optional forcedownload flag.
        tail='?forcedownload=1' if q else ''
        result[parts]=RemoteFile(parts,u.path+tail)
    return list(result.values())


class MoodleClient:
    def __init__(self, session_file, base: str = BASE, timeout: int = 30):
        self.base=base.rstrip('/')
        p=urlsplit(self.base)
        if p.scheme!='https' or p.hostname!='ucilnica-fe.uni-lj.si' or p.path not in ('','/') or p.port not in (None,443):
            raise ValueError('only the configured official e.FE HTTPS host is supported')
        self.host=p.hostname
        self.timeout=timeout
        self.session=requests.Session()
        self.session.headers.update({'User-Agent':'FE-Moodle-Sync/beta1.1 (personal read-only archive)',
            'Accept':'text/html,application/json,application/octet-stream,*/*'})
        self.read_cookies(session_file)

    def read_cookies(self,session_file):
        from pathlib import Path
        path=Path(session_file)
        if not path.is_file():raise MoodleError('session file not found; copy from local diagnostic profile')
        if path.stat().st_mode & 0o077:raise MoodleError('session file must have permissions 0600')
        payload=json.loads(path.read_text(encoding='utf-8'))
        if payload.get('site')!=self.base:raise MoodleError('session site mismatch')
        cookies=payload.get('cookies')
        if not isinstance(cookies,list) or not 0<len(cookies)<100:raise MoodleError('no valid session cookies')
        self.session.cookies.clear()
        for c in cookies:
            domain=str(c.get('domain','')).lstrip('.').lower()
            if domain != self.host:continue
            name=c.get('name');value=c.get('value');cp=c.get('path','/')
            if (not isinstance(name,str) or not isinstance(value,str) or not isinstance(cp,str)
                or not cp.startswith('/') or not re.fullmatch(r'[A-Za-z0-9_\-]+',name)):
                continue
            # Explicitly use only e.FE domain, never forward SSO cookies elsewhere.
            self.session.cookies.set(name,value,domain=self.host,path=cp,secure=True)
        if not self.session.cookies:raise MoodleError('no site-specific cookies found')

    def get(self,path,params=None,headers=None,stream=False):
        if not path.startswith('/') or '//' in path[:2] or urlsplit(path).scheme or urlsplit(path).netloc:
            raise ValueError('off-origin GET prohibited')
        url=self.base+path
        response=self.session.get(url,params=params,headers=headers,timeout=self.timeout,
                                  allow_redirects=False,stream=stream)
        if response.status_code in (301,302,303,307,308):
            response.close();raise MoodleError('redirect: session expired or restricted resource')
        if response.status_code in (401,403):
            response.close();raise MoodleError('access denied or session expired')
        return response

    def html(self,path,params=None):
        with self.get(path,params) as r:
            if r.status_code!=200:raise MoodleError(f'HTTP {r.status_code} on allowed page')
            if 'text/html' not in r.headers.get('Content-Type','').lower():
                raise MoodleError('unexpected page content type')
            if len(r.content)>8_000_000:raise MoodleError('HTML too large')
            body=r.text
            # Detect login page even if it answers with HTTP 200.
            if ('/login/index.php' in body and 'name="username"' in body):
                raise MoodleError('login page instead of requested content')
            return body

    def courses(self):
        page=self.html('/my/courses.php')
        match=re.search(r'["\']sesskey["\']\s*:\s*["\']([A-Za-z0-9_-]{6,128})["\']',page)
        if not match:raise MoodleError('no Moodle sesskey found in authenticated page')
        # POST is used ONLY for the documented Moodle READ operation below.
        response=self.session.post(self.base+'/lib/ajax/service.php',
            params={'sesskey':match[1],'info':COURSES_FUNCTION},
            json=[{'index':0,'methodname':COURSES_FUNCTION,
                   'args':{'classification':'all','limit':100,'offset':0}}],
            timeout=self.timeout,allow_redirects=False)
        try:
            if response.status_code!=200:raise MoodleError('course read AJAX failed')
            data=response.json()
        finally:response.close()
        if not isinstance(data,list) or not data or not isinstance(data[0],dict):
            raise MoodleError('unexpected course response')
        if data[0].get('exception'):raise MoodleError('course API denied')
        raw=data[0].get('data',{})
        if not isinstance(raw,dict) or not isinstance(raw.get('courses'),list):
            raise MoodleError('no courses in API response')
        if len(raw['courses'])>=100 or raw.get('nextoffset',0) not in (0,len(raw['courses']),None):
            # Fail closed instead of silently claiming a complete account mirror.
            raise MoodleError('course pagination requires implementation; 100-course cap')
        result=[]
        for row in raw['courses']:
            if not isinstance(row,dict) or type(row.get('id')) is not int:continue
            if row['id']<=0 or row['id']>=2**31:continue
            name=re.sub(r'<[^>]+>','',str(row.get('fullname',row.get('displayname',''))))[:180]
            result.append({'id':row['id'],'title':name,'shortname':str(row.get('shortname',''))[:100]})
        return result

    def modules(self,course_id:int):
        body=self.html('/course/view.php',{'id':course_id})
        return parse_modules(body,self.base)

    def folder_files(self,folder_id:int):
        body=self.html('/mod/folder/view.php',{'id':folder_id})
        return parse_folder_files(body,self.base)