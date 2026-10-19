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
from .resources import discover_resource_file, resource_local_path, RESOURCE_FILE_RE

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


def legacy_folder_dir(module:dict) -> Path:
    """Original pre-beta1.2.2 folder naming, needed for safe migration."""
    title=re.sub(r'\s+Mapa$', '', module['title']).strip() or 'Mapa'
    return Path('Gradiva')/f'{safe_part(title,90)}__{module["id"]}'


def folder_dir(module:dict) -> Path:
    """Portable Moodle folder naming for Syncthing on Android/Windows.

    Android shared storage rejects characters such as '?' although Linux allows
    them. The Moodle module ID remains in the path to prevent title collisions.
    """
    title=re.sub(r'\s+Mapa$', '', module['title']).strip() or 'Mapa'
    title=re.sub(r'[<>:"/\\|?*]', '', title).strip() or 'Mapa'
    title=safe_part(title,90)
    return Path('Gradiva')/f'{title}__{module["id"]}'


def resource_dir(module:dict) -> Path:
    """Android-compatible destination for a single Moodle resource."""
    title=re.sub(r'\s+Datoteka$', '', module['title']).strip() or 'Datoteka'
    title=re.sub(r'[<>:"/\\|?*]', '', title).strip() or 'Datoteka'
    title=safe_part(title,90)
    return Path('Gradiva')/'Samostojne-datoteke'/f'{title}__{module["id"]}'


def forum_dir(module:dict) -> Path:
    """Managed forum attachments, separate from user notes."""
    from .content import forum_dir as content_forum_dir
    return content_forum_dir(module)


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
    def list_files(self):
        return [dict(row) for row in self.c.execute('SELECT remote_key,rel_path,sha256,size_bytes FROM files')]
    def close(self):self.c.close()


def hash_file(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for buf in iter(lambda:f.read(131072),b''):h.update(buf)
    return h.hexdigest()


def migrate_legacy_folder(root:Path,state:State,course:dict,module:dict) -> int:
    """Atomically rename an owned pre-portable folder; never discard local files.

    Only migrate a directory containing exactly the previously indexed files,
    all unchanged according to SQLite hashes. If an interruption happens between
    filesystem rename and database update, a subsequent run can reconcile it.
    """
    base=course_dir(course)
    old_rel=base/legacy_folder_dir(module)
    new_rel=base/folder_dir(module)
    if old_rel==new_rel:return 0
    # Enforce both source and target underneath the FE root with no symlinks.
    old=ensure_safe_parent(root,old_rel)
    new=ensure_safe_parent(root,new_rel)
    prefix=old_rel.as_posix()+'/'
    tracked=[r for r in state.list_files() if r['rel_path'].startswith(prefix)]
    if not tracked:
        if old.exists():
            raise ValueError(f'untracked legacy folder requires manual review: {old_rel}')
        return 0
    if old.exists() and new.exists():
        raise ValueError(f'portable folder already exists: {new_rel}')
    current=old if old.exists() else new
    if not current.is_dir() or current.is_symlink():
        raise ValueError(f'managed folder missing or invalid: {old_rel}')
    expected={Path(r['rel_path']).relative_to(old_rel).as_posix():r for r in tracked}
    actual={}
    for path in current.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'legacy folder contains symlink: {old_rel}')
        if path.is_file():
            actual[path.relative_to(current).as_posix()]=path
    if set(actual)!=set(expected):
        raise ValueError(f'legacy folder has untracked or missing files: {old_rel}')
    for name,path in actual.items():
        if hash_file(path)!=expected[name]['sha256']:
            raise ValueError(f'legacy folder has locally modified files: {old_rel}')
    if old.exists():
        # Same filesystem; move all bytes with a single directory rename.
        old.rename(new)
    with state.c:
        for name,row in expected.items():
            dest=(new_rel/Path(name)).as_posix()
            state.c.execute('UPDATE files SET rel_path=? WHERE remote_key=? AND rel_path=?',
                            (dest,row['remote_key'],row['rel_path']))
    return len(tracked)


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
    kind=module['type']
    is_resource=kind=='resource'
    is_forum=kind=='forum'
    base_path=course_dir(course)/(resource_dir(module) if is_resource else
                                 forum_dir(module) if is_forum else folder_dir(module))
    # Portable filenames for Android, without modifying Moodle's remote URLs.
    local_parts=resource_local_path(remote.path) if (is_resource or is_forum) else remote.path
    rel=base_path.joinpath(*local_parts)
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
    if is_resource:
        if not RESOURCE_FILE_RE.fullmatch(remote.url_path.split('?',1)[0]):
            raise ValueError('invalid resource file path')
    elif is_forum:
        from .content import FORUM_FILE_RE
        if not FORUM_FILE_RE.fullmatch(remote.url_path.split('?',1)[0]):
            raise ValueError('invalid forum attachment path')
    elif not re.fullmatch(r'/pluginfile\.php/\d+/mod_folder/content/\d+/.+',remote.url_path):
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
    module_lists={}
    summary={'started_at':utcnow(),'status':'running','files':{},'subjects':[]}
    try:
        client=client_factory(cookie_file)
        courses=client.courses()
        if len(courses)>max_courses:raise MoodleError('course count exceeds configured limit')
        from .updates import capture
        baseline=capture(root,state,courses)
        for course in courses:
            course_id=course['id']
            directory=course_dir(course)
            try:
                modules=client.modules(course_id)
            except (requests.ConnectionError, requests.Timeout) as exc:
                result.errors+=1
                summary['courses_unavailable']=summary.get('courses_unavailable',0)+1
                summary.setdefault('error_type',type(exc).__name__)
                print(f'Course temporarily unavailable: id={course_id} type={type(exc).__name__}',flush=True)
                # Retain this subject in local navigation if a prior known-good
                # manifest exists. Never claim its data was checked in this run.
                manifest=root/directory/'predmet.json'
                if manifest.is_file() and not manifest.is_symlink():
                    try:
                        previous=json.loads(manifest.read_text(encoding='utf-8'))
                        if (previous.get('id')==course_id and
                            previous.get('title')==course['title'] and
                            isinstance(previous.get('modules'),list)):
                            cached=previous['modules']
                            module_lists[course_id]=cached
                            old_folders=sum(m.get('type')=='folder' for m in cached)
                            old_resources=sum(m.get('type')=='resource' for m in cached)
                            summary['subjects'].append({'id':course_id,'title':course['title'],
                                'folders':old_folders,'resources':old_resources,
                                'other_modules':len(cached)-old_folders-old_resources,
                                'cached':True})
                            summary['courses_from_cache']=summary.get('courses_from_cache',0)+1
                    except (ValueError,OSError,TypeError,AttributeError):
                        pass
                continue
            module_lists[course_id]=modules
            folders=[m for m in modules if m['type']=='folder']
            resources=[m for m in modules if m['type']=='resource']
            pages=[m for m in modules if m['type']=='page']
            from .content import is_announcement
            announcements=[m for m in modules if is_announcement(m)]
            course_info={'id':course_id,'title':course['title'],'shortname':course['shortname'],
                'modules':modules,'synced_at':utcnow(),
                'limitations':'beta1.6 reads optional official Moodle calendar action events with verified dates and tracks recent changes; interactive submissions are never mirrored.'}
            atomic_json(root,directory/'predmet.json',course_info)
            summary['subjects'].append({'id':course_id,'title':course['title'],'folders':len(folders),
                                        'resources':len(resources),
                                        'other_modules':len(modules)-len(folders)-len(resources)})
            for folder in folders:
                try:
                    renamed=migrate_legacy_folder(root,state,course,folder)
                    if renamed:
                        summary['portable_paths_migrated']=summary.get('portable_paths_migrated',0)+renamed
                        print(f'Portable folder migration: course={course_id} module={folder["id"]} files={renamed}',flush=True)
                except (ValueError,OSError,sqlite3.Error) as exc:
                    # Never create a second copy when migration is ambiguous.
                    result.errors+=1
                    print(f'Portable folder migration blocked: course={course_id} module={folder["id"]}: {str(exc)[:160]}',flush=True)
                    continue
                if delay:time.sleep(delay)
                try:
                    files=client.folder_files(folder['id'])
                except (requests.ConnectionError, requests.Timeout) as exc:
                    result.errors+=1
                    summary['folders_unavailable']=summary.get('folders_unavailable',0)+1
                    summary.setdefault('error_type',type(exc).__name__)
                    print(f'Folder temporarily unavailable: course={course_id} module={folder["id"]} type={type(exc).__name__}',flush=True)
                    continue
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
            # One-file Moodle resource activities (download only from approved paths).
            summary['resources_found']=summary.get('resources_found',0)+len(resources)
            for resource in resources:
                if processed>=max_files:raise MoodleError('configured per-run file limit exceeded')
                processed+=1
                if delay:time.sleep(delay)
                try:
                    remote=discover_resource_file(client,resource['id'])
                    if remote is None:
                        summary['resources_unsupported']=summary.get('resources_unsupported',0)+1
                        continue
                    outcome=store_file(client,state,root,course,resource,remote,result)
                    if outcome in ('ok','not_modified'):
                        summary['resources_processed']=summary.get('resources_processed',0)+1
                    else:
                        summary['resources_skipped']=summary.get('resources_skipped',0)+1
                except (MoodleError,ValueError,OSError,sqlite3.Error,requests.RequestException) as exc:
                    result.errors+=1
                    summary['resources_errors']=summary.get('resources_errors',0)+1
                    print(f'Resource sync error: course={course_id} module={resource["id"]} type={type(exc).__name__}',flush=True)
            # Plain text snapshots only. Unsupported Moodle layouts stay as e.FE links.
            from .content import archive_page,archive_forum,ForumReturnedToCourse
            summary['pages_found']=summary.get('pages_found',0)+len(pages)
            summary['announcements_found']=summary.get('announcements_found',0)+len(announcements)
            for item in pages:
                if not callable(getattr(client,'html',None)):
                    summary['pages_unsupported']=summary.get('pages_unsupported',0)+1
                    continue
                if delay:time.sleep(delay)
                try:
                    if archive_page(client,root,course,item):
                        summary['pages_archived']=summary.get('pages_archived',0)+1
                    else:
                        summary['pages_unsupported']=summary.get('pages_unsupported',0)+1
                except (MoodleError,ValueError,OSError,requests.RequestException) as exc:
                    result.errors+=1
                    summary['pages_errors']=summary.get('pages_errors',0)+1
                    print(f'Page archive error: course={course_id} module={item["id"]} type={type(exc).__name__}',flush=True)
            for item in announcements:
                if not callable(getattr(client,'html',None)):
                    summary['announcements_unsupported']=summary.get('announcements_unsupported',0)+1
                    continue
                if delay:time.sleep(delay)
                try:
                    posts,attachments=archive_forum(client,state,root,course,item,
                                                     store_file,result,delay=delay)
                    if posts:
                        summary['announcement_posts_archived']=summary.get('announcement_posts_archived',0)+posts
                        summary['announcement_attachments_processed']=summary.get('announcement_attachments_processed',0)+attachments
                    else:
                        summary['announcements_unsupported']=summary.get('announcements_unsupported',0)+1
                except ForumReturnedToCourse:
                    # Moodle explicitly redirected the forum to this same
                    # course. Nothing was archived; report that transparently,
                    # but this is not a failed download or an expired session.
                    summary['announcements_unsupported']=summary.get('announcements_unsupported',0)+1
                    summary['announcements_returned_to_course']=summary.get('announcements_returned_to_course',0)+1
                    print(f'Forum not archived (returned to course): course={course_id} module={item["id"]}',flush=True)
                except (MoodleError,ValueError,OSError,requests.RequestException) as exc:
                    result.errors+=1
                    summary['announcements_errors']=summary.get('announcements_errors',0)+1
                    print(f'Forum archive error: course={course_id} module={item["id"]} type={type(exc).__name__}',flush=True)
        # Calendar is optional: not all Moodle instances expose the read-only
        # AJAX API. Never invent deadlines or fail core file sync because of it.
        from .calendar import fetch_calendar,render_calendar
        try:
            events,truncated=fetch_calendar(client,courses)
            summary['calendar']=render_calendar(root,events,truncated)
        except (MoodleError,ValueError,OSError,requests.RequestException) as exc:
            summary['calendar']={'available':False,'error_type':type(exc).__name__}
            print(f'Calendar unavailable: {type(exc).__name__}',flush=True)
            try:
                render_calendar(root,None)
            except (ValueError,OSError) as render_exc:
                result.errors+=1
                print(f'Calendar HTML error: {type(render_exc).__name__}',flush=True)
        # Preserve changes for 30 days, instead of showing only the last cycle.
        from .updates import capture as capture_updates,record_changes,history,render_history
        try:
            current=capture_updates(root,state,courses,module_lists)
            summary['changes']=record_changes(state,baseline,current,utcnow())
            summary['changes']['shown']=render_history(root,history(state))['shown']
        except (OSError,ValueError,sqlite3.Error) as exc:
            result.errors+=1
            summary['changes_error_type']=type(exc).__name__
            print(f'Changes history error: {type(exc).__name__}',flush=True)
        # Index mirrored PDFs locally, never re-read unchanged PDFs. The work
        # budget is bounded so malformed/large files cannot stall routine sync.
        # PDF-index failures do not erase previous search or course navigation.
        pdf_terms={}
        try:
            from .pdf_index import index_pdfs
            known_courses=[c for c in courses if c['id'] in module_lists]
            pdf_terms,summary['pdf_index']=index_pdfs(
                root,state,state.list_files(),known_courses)
        except (OSError,ValueError,sqlite3.Error,ImportError) as exc:
            summary['pdf_index']={'available':False,'error_type':type(exc).__name__}
            print(f'PDF index unavailable: {type(exc).__name__}',flush=True)
        # Generate a self-contained, offline HTML navigation after mirroring files.
        # Module information is only copied from explicitly read e.FE pages.
        try:
            from .pages import build_site
            # Never pass courses without manifests: on a first-ever transient
            # failure a subject may have no cached data yet.
            display_courses=[c for c in courses if c['id'] in module_lists]
            summary['navigation']=build_site(root,display_courses,module_lists,
                                              state.list_files(),utcnow(),pdf_terms=pdf_terms)
        except (OSError,ValueError,KeyError) as e:
            result.errors+=1
            summary['navigation']={'error_type':type(e).__name__,'reason':str(e)[:200]}
            print(f'Local navigation error: {type(e).__name__}: {str(e)[:200]}',flush=True)
        summary['status']='ok' if result.errors==0 else 'partial'
    except Exception as e:
        summary['status']='failed'
        summary['error_type']=type(e).__name__
        print(f'Sync interrupted: {type(e).__name__}: {str(e)[:160]}',flush=True)
    finally:
        summary['finished_at']=utcnow()
        summary['files']=vars(result)
        state.close()
        # Status must exist whenever the generated index links to it, even
        # when run_sync is called directly rather than through the CLI.
        try:
            from .status import publish_status
            publish_status(root,state_path.parent,summary)
        except (ValueError,OSError) as exc:
            summary['status_publish_error_type']=type(exc).__name__
            print(f'Sync status publication error: {type(exc).__name__}',flush=True)
            if summary['status']=='ok':summary['status']='partial'
    return summary


def render_cached(root: Path, state_dir: Path) -> dict:
    """Regenerate only offline HTML using cached Moodle subject metadata.

    No network, authentication, file downloads or edits to professor materials.
    Managed HTML is atomically replaced only if it has our marker.
    """
    from .pages import build_site
    status_path=state_dir/'status.json'
    status=json.loads(status_path.read_text(encoding='utf-8'))
    entries=status.get('subjects',[])
    if not isinstance(entries,list) or not entries:
        raise ValueError('no cached subjects found in status.json')
    courses=[]
    module_lists={}
    seen=set()
    for entry in entries:
        ident=entry.get('id')
        name=entry.get('title')
        if type(ident) is not int or ident<=0 or ident in seen or not isinstance(name,str):
            raise ValueError('invalid cached course reference')
        seen.add(ident)
        course={'id':ident,'title':name}
        manifest=root/course_dir(course)/'predmet.json'
        info=json.loads(manifest.read_text(encoding='utf-8'))
        if info.get('id')!=ident or info.get('title')!=name or not isinstance(info.get('modules'),list):
            raise ValueError(f'invalid course manifest: {ident}')
        course['shortname']=info.get('shortname','')
        courses.append(course)
        module_lists[ident]=info['modules']
    # Render mode does not access Moodle and never extracts PDFs. Only cached
    # search vocabulary with matching source stat/SHA metadata is reused.
    db_path=state_dir/'files.sqlite'
    con=sqlite3.connect(f'file:{db_path}?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    try:
        columns={row[1] for row in con.execute('PRAGMA table_info(files)')}
        required={'remote_key','rel_path','sha256','size_bytes'}
        if required.issubset(columns):
            records=[dict(r) for r in con.execute(
                'SELECT remote_key,rel_path,sha256,size_bytes FROM files')]
            from .pdf_index import cached_terms
            pdf_terms=cached_terms(root,con,records,courses)
        else:
            # Older cached fixtures/databases can still regenerate HTML
            # navigation by filename. No unsafe legacy PDF text adoption.
            records=[dict(r) for r in con.execute('SELECT rel_path FROM files')]
            pdf_terms={}
    finally:
        con.close()
    return build_site(root,courses,module_lists,records,utcnow(),pdf_terms=pdf_terms)
