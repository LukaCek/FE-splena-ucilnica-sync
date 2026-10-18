"""One-way synchronization of Moodle folder files into explicitly managed directories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import requests
import time

from .client import MoodleClient, MoodleError, RemoteFile, safe_part

# Existing first-semester FE organization. Everything else goes to the general
# Spletna-ucilnica directory, which is separately writable in Docker Compose.
COURSE_DESTINATIONS={
    'Matematika I':'1-semester/01_MAT/Spletna-ucilnica',
    'Mehanika in toplota':'1-semester/02_FIZ_MiT/Spletna-ucilnica',
    'Programiranje I':'1-semester/03_PROG/Spletna-ucilnica',
    'Osnove elektrotehnike I':'1-semester/04_ELE/Spletna-ucilnica',
}
MAX_CONTENT_BYTES=int(os.getenv('SYNC_MAX_FILE_MB','100'))*1024*1024


def utcnow():return datetime.now(timezone.utc).isoformat()


def course_dir(course:dict) -> Path:
    name=course['title']
    if name in COURSE_DESTINATIONS:
        return Path(COURSE_DESTINATIONS[name])
    # Include ID: similarly named courses cannot overwrite one another.
    slug=re.sub(r'\s+','_',safe_part(name or f'predmet-{course["id"]}',90))
    return Path('Spletna-ucilnica')/'Ostali-predmeti'/f'{course["id"]}_{slug}'


def folder_dir(module:dict) -> Path:
    title=module['title']
    # Moodle sometimes appends "Mapa" to the visible anchor label.
    title=re.sub(r'\s+Mapa$', '', title).strip() or 'Mapa'
    title=safe_part(title,90)
    return Path('Gradiva')/f'{title}__{module["id"]}'


def ensure_safe_parent(root:Path,relative:Path) -> Path:
    """Refuse directory symlinks, traversal, and paths outside the configured FE root."""
    if relative.is_absolute() or not relative.parts or any(p in ('','.', '..') for p in relative.parts):
        raise ValueError('unsafe output path')
    root=root.resolve(strict=True)
    cur=root
    for part in relative.parts[:-1]:
        safe_part(part,180)
        cur=cur/part
        if cur.is_symlink():raise ValueError('symlink in output path')
        if not cur.exists():cur.mkdir(mode=0o750)
        if not cur.is_dir() or cur.is_symlink():raise ValueError('invalid output directory')
    final=cur/relative.name
    if final.is_symlink():raise ValueError('destination is symlink')
    return final


class State:
    def __init__(self,db_file:Path):
        db_file.parent.mkdir(parents=True,exist_ok=True)
        self.c=sqlite3.connect(db_file)
        self.c.row_factory=sqlite3.Row
        self.c.execute('''CREATE TABLE IF NOT EXISTS files (
            remote_key TEXT PRIMARY KEY, rel_path TEXT UNIQUE NOT NULL,
            sha256 TEXT NOT NULL, etag TEXT, last_modified TEXT,
            size_bytes INTEGER NOT NULL, synced_at TEXT NOT NULL)''')
        self.c.commit()
    def lookup(self,key):
        row=self.c.execute('SELECT * FROM files WHERE remote_key=?',(key,)).fetchone()
        return dict(row) if row else None
    def save(self,key,rel_path,sha256,etag,last_modified,size):
        self.c.execute('''INSERT INTO files(remote_key,rel_path,sha256,etag,last_modified,size_bytes,synced_at)
         VALUES (?,?,?,?,?,?,?) ON CONFLICT(remote_key) DO UPDATE SET
          rel_path=excluded.rel_path,sha256=excluded.sha256,etag=excluded.etag,
          last_modified=excluded.last_modified,size_bytes=excluded.size_bytes,
          synced_at=excluded.synced_at''',
          (key,str(rel_path),sha256,etag,last_modified,size,utcnow()))
        self.c.commit()
    def close(self):self.c.close()


def hash_file(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for buf in iter(lambda:f.read(131072),b''):h.update(buf)
    return h.hexdigest()


def atomic_json(root:Path,path:Path,data:dict):
    dest=ensure_safe_parent(root,path)
    if dest.exists() and not dest.is_file():raise ValueError('metadata path not file')
    fd,tmp=tempfile.mkstemp(prefix='.sync-',dir=dest.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            os.fchmod(f.fileno(),0o640)
            json.dump(data,f,ensure_ascii=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,dest)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


@dataclass
class Result:
    downloaded:int=0
    unchanged:int=0
    updated:int=0
    skipped:int=0
    errors:int=0
    total_bytes:int=0


def store_file(client:MoodleClient,state:State,root:Path,course:dict,module:dict,
               remote:RemoteFile,result:Result,max_bytes:int=MAX_CONTENT_BYTES):
    key=f'{course["id"]}/{module["id"]}/'+ '/'.join(remote.path)
    base_path=course_dir(course)/folder_dir(module)
    rel=base_path.joinpath(*remote.path)
    # Check every remote component before any local file creation.
    for name in remote.path: safe_part(name,180)
    dest=ensure_safe_parent(root,rel)
    stored=state.lookup(key)
    # Never adopt/preempt files not originally created by this sync service.
    if dest.exists() and (not stored or stored['rel_path'] != str(rel)):
        result.skipped+=1;return 'collision'
    if stored and stored['rel_path'] != str(rel):
        # New path is allowed only if unoccupied; preserve old path as archive.
        if dest.exists():result.skipped+=1;return 'rename_collision'
    if dest.exists() and stored and hash_file(dest)!=stored['sha256']:
        result.skipped+=1;return 'locally_modified'
    headers={}
    if stored and dest.exists():
        if stored['etag']:headers['If-None-Match']=stored['etag']
        if stored['last_modified']:headers['If-Modified-Since']=stored['last_modified']
    # The remote path is produced ONLY by the allowlisted Moodle folder parser.
    if not re.fullmatch(r'/pluginfile\.php/\d+/mod_folder/content/\d+/.+',remote.url_path):
        raise ValueError('invalid remote file path')
    response=client.get(remote.url_path,headers=headers,stream=True)
    try:
        if response.status_code==304 and stored and dest.exists():
            result.unchanged+=1;return 'not_modified'
        if response.status_code!=200:raise MoodleError(f'file GET HTTP {response.status_code}')
        ctype=response.headers.get('Content-Type','').split(';')[0].lower()
        if ctype in ('text/html','application/xhtml+xml'):
            raise MoodleError('HTML/login response instead of file')
        announced=response.headers.get('Content-Length','')
        if announced.isascii() and announced.isdecimal() and int(announced)>max_bytes:
            raise MoodleError('file exceeds size limit')
        fd,tmp=tempfile.mkstemp(prefix='.fe-partial-',dir=dest.parent)
        h=hashlib.sha256();total=0;prefix=b''
        try:
            with os.fdopen(fd,'wb') as out:
                os.fchmod(out.fileno(),0o640)
                for block in response.iter_content(65536):
                    if not block:continue
                    total+=len(block)
                    if total>max_bytes:raise MoodleError('file exceeds size limit')
                    prefix=(prefix+block)[:32]
                    h.update(block);out.write(block)
                out.flush();os.fsync(out.fileno())
            if total==0 or prefix.lstrip().lower().startswith((b'<!doctype html',b'<html')):
                raise MoodleError('empty file or HTML response')
            if dest.suffix.lower()=='.pdf' and not prefix.startswith(b'%PDF-'):
                raise MoodleError('PDF signature mismatch')
            digest=h.hexdigest()
            if stored and dest.exists() and stored['sha256']==digest:
                result.unchanged+=1
            else:
                # Recheck destination immediately before replace; don't overwrite
                # a file that appeared or was edited while downloading.
                if dest.exists():
                    if not stored or hash_file(dest)!=stored['sha256']:
                        result.skipped+=1;return 'collision_or_local_edit'
                    os.replace(tmp,dest);result.updated+=1
                else:
                    # Exclusive hardlink prevents overwriting a concurrent new file.
                    try:os.link(tmp,dest)
                    except FileExistsError:
                        result.skipped+=1;return 'collision'
                    result.downloaded+=1
            state.save(key,rel,digest,response.headers.get('ETag'),response.headers.get('Last-Modified'),total)
            result.total_bytes+=total
            return 'ok'
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    finally:response.close()


def run_sync(root:Path,state_path:Path,cookie_file:Path,max_courses:int=100,
             max_files:int=2000,delay:float=0.3,client_factory=MoodleClient) -> dict:
    root=root.resolve(strict=True)
    state=State(state_path)
    result=Result();processed=0
    summary={'started_at':utcnow(),'status':'running','files':{},'subjects':[]}
    try:
        client=client_factory(cookie_file)
        courses=client.courses()
        if len(courses)>max_courses:raise MoodleError('course count exceeds configured limit')
        for course in courses:
            course_id=course['id']
            modules=client.modules(course_id)
            directory=course_dir(course)
            folders=[m for m in modules if m['type']=='folder']
            course_info={'id':course_id,'title':course['title'],'shortname':course['shortname'],
                'modules':modules,'synced_at':utcnow(),
                'limitations':'beta1.0 syncs folder files only; forums, quizzes, tasks and deadlines are not mirrored.'}
            atomic_json(root,directory/'predmet.json',course_info)
            summary['subjects'].append({'id':course_id,'title':course['title'],'folders':len(folders),
                                        'other_modules':len(modules)-len(folders)})
            for folder in folders:
                if delay:time.sleep(delay)
                files=client.folder_files(folder['id'])
                for remote in files:
                    if processed>=max_files:raise MoodleError('configured per-run file limit exceeded')
                    processed+=1
                    if delay:time.sleep(delay)
                    try:
                        store_file(client,state,root,course,folder,remote,result)
                    except (MoodleError,ValueError,OSError,sqlite3.Error,requests.RequestException) as e:
                        # Deliberately don't record request URLs or credentials.
                        result.errors+=1
                        print(f'File sync error: course={course_id} module={folder["id"]} type={type(e).__name__}',flush=True)
        summary['status']='ok' if result.errors==0 else 'partial'
    except Exception as e:
        summary['status']='failed'
        summary['error_type']=type(e).__name__
        print(f'Sync interrupted: {type(e).__name__}: {str(e)[:160]}',flush=True)
    finally:
        summary['finished_at']=utcnow()
        summary['files']=vars(result)
        state.close()
    return summary
