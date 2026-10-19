"""Read-only snapshots of Moodle pages and announcement forum posts.

Only explicitly identified Moodle body containers are copied. Moodle HTML and
scripts are NEVER embedded in generated files: captured text is escaped.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .client import BASE, MoodleError, RemoteFile, safe_remote_path
from .resources import portable_part

MAX_HTML=4_000_000
MAX_CHARS=60000
MAX_DISCUSSIONS=5
MAX_ATTACHMENTS=12
FORUM_FILE_RE=re.compile(r'^/pluginfile\.php/\d+/mod_forum/attachment/\d+/(.+)$')
PAGE_CLASSES={'no-overflow','generalbox','page-content'}
POST_CLASSES={'post-content-container','posting'}
VOID={'br','img','input','hr','meta','link','source','wbr','area','base','embed','track','col'}
BLOCK={'p','div','section','article','li','h1','h2','h3','h4','blockquote','tr'}
SKIP={'script','style','form','button','nav','svg','noscript'}


class TextBlocks(HTMLParser):
    def __init__(self,targets):
        super().__init__(convert_charrefs=True)
        self.targets=targets
        self.depth=0
        self.capture=0
        self.ignore=0
        self.parts=[]
        self.results=[]
    def handle_starttag(self,tag,attrs):
        if tag=='br' and self.capture and not self.ignore:self.parts.append('\n')
        if tag in VOID:return
        self.depth+=1
        classes=set((dict(attrs).get('class') or '').split())
        if not self.capture and tag in ('div','section','article') and classes & self.targets:
            self.capture=self.depth
            self.parts=[]
        elif self.capture and tag in BLOCK and not self.ignore:
            self.parts.append('\n')
        if self.capture and tag in SKIP and not self.ignore:
            self.ignore=self.depth
    def handle_data(self,data):
        if self.capture and not self.ignore:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag in VOID:return
        if self.ignore==self.depth:self.ignore=0
        if self.capture and not self.ignore and tag in BLOCK:self.parts.append('\n')
        if self.capture==self.depth:
            lines=[re.sub(r'[ \t\xa0]+',' ',s).strip() for s in ''.join(self.parts).splitlines()]
            cleaned=re.sub(r'\n{3,}','\n\n','\n'.join(s for s in lines if s)).strip()
            if cleaned:self.results.append(cleaned[:MAX_CHARS])
            self.capture=0
            self.parts=[]
        self.depth=max(0,self.depth-1)


def extract_body(markup,kind):
    if not isinstance(markup,str) or len(markup)>MAX_HTML:
        raise MoodleError('content HTML too large')
    if '/login/index.php' in markup and 'name="username"' in markup:
        raise MoodleError('login page instead of content')
    parser=TextBlocks(PAGE_CLASSES if kind=='page' else POST_CLASSES)
    parser.feed(markup)
    return max(parser.results,key=len) if parser.results else None


def is_announcement(module):
    return module.get('type')=='forum' and bool(re.search(
        r'obvestil|novic|announcement|news',str(module.get('title','')),re.I))


def safe_title(module):
    value=re.sub(r'\s+(?:Stran|Forum)$','',str(module.get('title',''))).strip() or 'Vsebina'
    return portable_part(value[:90],90)


def page_dir(module):
    return Path('Gradiva')/'Moodle-strani'/f'{safe_title(module)}__{module["id"]}'


def forum_dir(module):
    return Path('Gradiva')/'Obvestila'/f'{safe_title(module)}__{module["id"]}'


def read_page(client,module_id):
    if type(module_id) is not int or not 0<module_id<2**31:raise ValueError('invalid page id')
    return extract_body(client.html('/mod/page/view.php',{'id':module_id}),'page')


class DiscussionLinks(HTMLParser):
    def __init__(self,base):
        super().__init__(convert_charrefs=True)
        self.base=base
        self.items=[]
        self.active=None
    def handle_starttag(self,tag,attrs):
        if tag!='a':return
        link=dict(attrs).get('href') or ''
        if len(link)>1800:return
        origin=urlsplit(self.base)
        u=urlsplit(urljoin(self.base+'/',link))
        if (u.scheme!='https' or u.hostname!=origin.hostname or u.port not in (None,443)
            or u.username or u.password or u.fragment or u.path!='/mod/forum/discuss.php'):
            return
        q=parse_qs(u.query)
        ids=q.get('d',[])
        if set(q)!={'d'} or len(ids)!=1 or not ids[0].isascii() or not ids[0].isdecimal():return
        ident=int(ids[0])
        if 0<ident<2**31:self.active=(ident,[])
    def handle_data(self,data):
        if self.active:self.active[1].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.active:
            ident,text=self.active
            title=re.sub(r'\s+',' ',' '.join(text)).strip()[:180]
            if title and ident not in [x[0] for x in self.items]:
                self.items.append((ident,title))
            self.active=None


class Attachments(HTMLParser):
    def __init__(self,base,discussion_id):
        super().__init__(convert_charrefs=True)
        self.base=base
        self.discussion_id=discussion_id
        self.files=[]
    def handle_starttag(self,tag,attrs):
        if tag!='a':return
        link=dict(attrs).get('href') or ''
        if len(link)>2500:return
        u=urlsplit(urljoin(self.base+'/',link))
        origin=urlsplit(self.base)
        if (u.scheme!='https' or u.hostname!=origin.hostname or u.port not in (None,443)
            or u.username or u.password or u.fragment):return
        m=FORUM_FILE_RE.fullmatch(u.path)
        if not m:return
        q=parse_qs(u.query,keep_blank_values=True)
        if q and q!={'forcedownload':['1']}:return
        try:
            parts=safe_remote_path(m[1])
            for part in parts:portable_part(part)
        except (ValueError,UnicodeError):return
        item=RemoteFile((f'tema-{self.discussion_id}',)+parts,
                        u.path+('?forcedownload=1' if q else ''))
        if item.url_path not in {x.url_path for x in self.files}:
            self.files.append(item)


def read_forum(client,module_id):
    if type(module_id) is not int or not 0<module_id<2**31:raise ValueError('invalid forum id')
    html=client.html('/mod/forum/view.php',{'id':module_id})
    if len(html)>MAX_HTML:raise MoodleError('forum HTML too large')
    parser=DiscussionLinks(client.base)
    parser.feed(html)
    return parser.items[:MAX_DISCUSSIONS]


def read_discussion(client,discussion_id):
    if type(discussion_id) is not int or not 0<discussion_id<2**31:
        raise ValueError('invalid discussion id')
    html=client.html('/mod/forum/discuss.php',{'d':discussion_id})
    content=extract_body(html,'post')
    parser=Attachments(client.base,discussion_id)
    parser.feed(html)
    return content,parser.files[:MAX_ATTACHMENTS]


def md_escape(s):
    # An exported Markdown file must not execute embedded Moodle HTML/links.
    return re.sub(r'([\\*_{}\[\]()#+!>|~])',r'\\\1',s).replace(chr(96),'\\'+chr(96)).replace('<','&lt;').replace('>','&gt;')


def write_note(root,relative,title,body,source):
    from .status import write_text,TEXT_MARKER
    txt=f'{TEXT_MARKER}\n\n# {md_escape(title)}\n\n{md_escape(body)}\n\nVir: {source}\n'
    write_text(root,relative,txt)


def render_text_html(title,body,doc_dir,course_index,source,files=()):
    from .pages import e,local_href,page
    href=local_href(doc_dir,course_index)
    links=''.join(f'<li><a href="{e(local_href(doc_dir,p))}">{e(p.name)}</a></li>' for p in files)
    contents=(f'<div class="breadcrumb"><a href="{e(href)}">← Nazaj na predmet</a></div>'
              f'<main class="course-shell" id="content"><h1>{e(title)}</h1>'
              f'<div style="white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7">{e(body)}</div>'
              +(f'<h2>Priloge</h2><ul>{links}</ul>' if links else '')
              +f'<p class="footer"><a href="{e(source)}" target="_blank" rel="noopener noreferrer">'
              'Izvirnik na e.FE ↗</a></p></main>')
    return page(title,contents,'lokalni arhiv',home=href,subject=True)


def archive_page(client,root,course,module):
    from .sync import course_dir
    from .pages import write_generated
    body=read_page(client,module['id'])
    if not body:return False
    relative=course_dir(course)/page_dir(module)
    title=safe_title(module)
    source=f'{BASE}/mod/page/view.php?id={module["id"]}'
    write_note(root,relative/'vsebina.md',title,body,source)
    write_generated(root,relative/'index.html',
        render_text_html(title,body,root/relative,
                         root/course_dir(course)/'index.html',source))
    return True


def archive_forum(client,state,root,course,module,store_file,result,delay=0):
    from time import sleep
    from .sync import course_dir
    from .pages import e,local_href,page,write_generated
    import requests

    discussions=read_forum(client,module['id'])
    if not discussions:return 0,0
    relative=course_dir(course)/forum_dir(module)
    home=root/course_dir(course)/'index.html'
    pages=[];attachments=0
    for ident,title in discussions:
        if delay:sleep(delay)
        try:
            body,remotes=read_discussion(client,ident)
        except (MoodleError,ValueError,OSError,requests.RequestException):
            continue
        if not body:continue
        for remote in remotes:
            if delay:sleep(delay)
            try:
                outcome=store_file(client,state,root,course,module,remote,result)
                if outcome in ('ok','not_modified'):attachments+=1
            except (MoodleError,ValueError,OSError,requests.RequestException) as exc:
                result.errors+=1
                print(f'Forum attachment error: course={course["id"]} module={module["id"]} type={type(exc).__name__}',flush=True)
        prefix=(relative/f'tema-{ident}').as_posix()+'/'
        stored=[]
        for record in state.list_files():
            if record['rel_path'].replace('\\','/').startswith(prefix):
                path=root/record['rel_path']
                if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                    stored.append(path)
        source=f'{BASE}/mod/forum/discuss.php?d={ident}'
        write_note(root,relative/f'tema-{ident}.md',title,body,source)
        write_generated(root,relative/f'tema-{ident}.html',
            render_text_html(title,body,root/relative,home,source,stored))
        pages.append((ident,title))
    if not pages:return 0,attachments
    source=f'{BASE}/mod/forum/view.php?id={module["id"]}'
    href=local_href(root/relative,home)
    links=''.join(f'<li><a href="tema-{ident}.html">{e(title)}</a></li>' for ident,title in pages)
    inner=(f'<div class="breadcrumb"><a href="{e(href)}">← Nazaj na predmet</a></div>'
           f'<main class="course-shell" id="content"><h1>{e(safe_title(module))}</h1>'
           f'<p>Arhiv prvih {MAX_DISCUSSIONS} razprav v vrstnem redu na e.FE.</p><ul>'
           f'{links}</ul><p><a href="{e(source)}">Izvirni forum ↗</a></p></main>')
    write_generated(root,relative/'index.html',
        page(safe_title(module),inner,'lokalni arhiv',home=href,subject=True))
    return len(pages),attachments
