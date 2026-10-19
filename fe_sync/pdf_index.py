"""Incremental, bounded, LOCAL PDF word indexing for the offline FE search page.

The index contains normalized *distinct words* (not the actual PDF text).
Index entries live in /state/files.sqlite, never overwrite the PDF and never
enter a network request. A search for multiple words matches all words in any
order; it is not exact-phrase or OCR search.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import logging
import multiprocessing
import re
import sqlite3
import time
import unicodedata
import zlib

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_FILE_BYTES=50*1024*1024
MAX_PAGES=350
MAX_TERMS=18000
MAX_CHARS=1_500_000
MAX_COMPRESSED=350_000
MAX_FILES_PER_CYCLE=400
MAX_SECONDS_PER_CYCLE=210
MAX_SECONDS_PER_PDF=25
MAX_WORKER_VMEM=384*1024*1024
INDEX_VERSION=1
# Do not log PDF text, professor names, URL tokens, file paths or private notes.
PARTIAL_RESULTS=frozenset({'partial','no_text','unsupported','bad_pdf','timeout','ready'})


class _RecoveredXrefWarningFilter(logging.Filter):
    """Hide *only* pypdf's repetitive notice that it recovered a bad xref.

    Unrelated warnings and errors remain visible. Parsing failures remain
    exposed through pdf_index.bad_pdf, .timeout and .partial in status.json.
    Installed in each PDF worker only, never on the whole app logger.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.levelno == logging.WARNING and
                    record.getMessage().startswith('Ignoring wrong pointing object '))



def normalize_words(text:str)->list[str]:
    """Accent-insensitive UTF-8 words, compatible with the search JS."""
    folded=unicodedata.normalize('NFD', text).casefold()
    folded=''.join(c for c in folded if unicodedata.category(c)!='Mn')
    return [word for word in re.findall(r'[^\W_]+',folded,flags=re.UNICODE)
            if 2<=len(word)<=48]


def _table(db:sqlite3.Connection):
    db.execute('''CREATE TABLE IF NOT EXISTS pdf_search_index(
        remote_key TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        index_version INTEGER NOT NULL,
        status TEXT NOT NULL,
        scanned_pages INTEGER NOT NULL,
        total_pages INTEGER NOT NULL,
        compressed_terms BLOB NOT NULL
    )''')
    db.commit()


def _valid_pdf(root:Path,record:dict,prefixes:tuple[Path,...]):
    """Only Moodle-owned tracked PDFs. Refuse symlinks and local modifications."""
    rel_str=record.get('rel_path')
    sha=record.get('sha256')
    size=record.get('size_bytes')
    key=record.get('remote_key')
    if (not isinstance(rel_str,str) or not rel_str.lower().endswith('.pdf')
        or not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha)
        or type(size) is not int or size<=0
        or not isinstance(key,str) or not key or len(key)>3000):
        return None
    rel=Path(rel_str)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        return None
    if not any(rel.is_relative_to(prefix) for prefix in prefixes):
        return None
    for i in range(1,len(rel.parts)+1):
        if (root.joinpath(*rel.parts[:i])).is_symlink():
            return None
    target=root/rel
    try:
        if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
            return None
        st=target.stat()
        if st.st_size!=size:return None
    except OSError:
        return None
    return rel_str,target,st


def _extract(path:Path)->tuple[str,str,int,int]:
    """No OCR, no external executables, no network or password decryption."""
    try:
        reader=PdfReader(str(path),strict=False)
        if reader.is_encrypted:
            return 'unsupported','',0,0
        count=len(reader.pages)
        if count>5000:return 'unsupported','',0,count
        words=set()
        characters=0
        visited=0
        partial=count>MAX_PAGES
        for pageno in range(min(count,MAX_PAGES)):
            content=reader.pages[pageno].extract_text() or ''
            visited+=1
            characters+=len(content)
            for term in normalize_words(content):
                if len(words)>=MAX_TERMS and term not in words:
                    partial=True
                    break
                words.add(term)
            if characters>=MAX_CHARS or len(words)>=MAX_TERMS:
                partial=True
                break
        packed=' '.join(sorted(words))
        if len(packed)>MAX_COMPRESSED*3:
            # Prevent a single PDF from creating an unusably huge Android HTML
            # attribute. Keep a deterministic prefix and mark this as partial.
            packed=packed[:MAX_COMPRESSED*3].rsplit(' ',1)[0]
            partial=True
        status='partial' if partial else 'ready' if words else 'no_text'
        return status,packed,visited,count
    except Exception:
        # Untrusted PDF parsing must not abort the core Moodle sync.
        return 'bad_pdf','',0,0


def _extract_worker(path:str,channel):
    """Untrusted PDF parsing happens in an isolated, memory-limited child."""
    # pypdf._reader emits dozens of "Ignoring wrong pointing object" warnings
    # for some Moodle PDFs with recoverable xref tables. Suppress precisely
    # that notice, locally to this child, without masking other diagnostics.
    reader_logger=logging.getLogger('pypdf._reader')
    recovered_filter=_RecoveredXrefWarningFilter()
    reader_logger.addFilter(recovered_filter)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(MAX_WORKER_VMEM,MAX_WORKER_VMEM))
        channel.send(_extract(Path(path)))
    except Exception:
        try:channel.send(('bad_pdf','',0,0))
        except (BrokenPipeError,OSError):pass
    finally:
        reader_logger.removeFilter(recovered_filter)
        channel.close()


def _safe_extract(path:Path,timeout:int=MAX_SECONDS_PER_PDF):
    """Kill PDFs that hang or exhaust memory, without killing Docker itself."""
    ctx=multiprocessing.get_context('fork')  # TrueNAS SCALE is Linux.
    recv,send=ctx.Pipe(duplex=False)
    proc=ctx.Process(target=_extract_worker,args=(str(path),send),daemon=True)
    try:
        proc.start()
        send.close()
        if recv.poll(timeout):
            try:
                outcome=recv.recv()
                if (isinstance(outcome,tuple) and len(outcome)==4
                    and outcome[0] in ('ready','partial','no_text','unsupported','bad_pdf')):
                    return outcome
            except (EOFError,OSError):
                pass
            return 'bad_pdf','',0,0
        return 'timeout','',0,0
    except (OSError,RuntimeError):
        return 'bad_pdf','',0,0
    finally:
        if proc.pid is not None:
            if proc.is_alive():proc.terminate()
            proc.join(timeout=1)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1)
        recv.close()
        send.close()


def _decode(row):
    try:
        blob=row['compressed_terms']
        if not blob or len(blob)>MAX_COMPRESSED:return ''
        inflater=zlib.decompressobj()
        expanded=inflater.decompress(blob,MAX_COMPRESSED*3+1)
        if len(expanded)>MAX_COMPRESSED*3 or not inflater.eof:return ''
        return expanded.decode('utf-8')
    except (ValueError,TypeError,zlib.error,UnicodeError):
        return ''


def _get_row(state,key):
    return state.c.execute(
        'SELECT * FROM pdf_search_index WHERE remote_key=?',(key,)).fetchone()


def _matches(row,record,stat):
    return (row is not None and row['sha256']==record['sha256']
            and row['size_bytes']==stat.st_size and row['mtime_ns']==stat.st_mtime_ns
            and row['index_version']==INDEX_VERSION
            and row['status'] in PARTIAL_RESULTS)


def _sha_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for buf in iter(lambda:stream.read(128*1024),b''):
            h.update(buf)
    return h.hexdigest()


def index_pdfs(root:Path,state,records:list[dict],courses:list[dict],
               max_files:int=MAX_FILES_PER_CYCLE,
               time_budget:float=MAX_SECONDS_PER_CYCLE)->tuple[dict[str,str],dict]:
    """Index only successfully mirrored, unmodified PDFs; bound CPU per cycle."""
    from .sync import course_dir
    root=root.resolve(strict=True)
    prefixes=tuple(course_dir(c) for c in courses)
    _table(state.c)
    deadline=time.monotonic()+max(1.0,float(time_budget))
    terms={}
    counters={'total':0,'ready':0,'partial':0,'no_text':0,'unsupported':0,
              'bad_pdf':0,'timeout':0,'modified':0,'pending':0,'processed':0,'cached':0}
    for record in records:
        info=_valid_pdf(root,record,prefixes)
        if not info:continue
        rel,path,st=info
        counters['total']+=1
        row=_get_row(state,record['remote_key'])
        if _matches(row,record,st):
            counters['cached']+=1
            status=row['status']
            if status in ('ready','partial'):
                terms[rel]=_decode(row)
            counters[status]+=1
            continue
        if (counters['processed']>=max_files or time.monotonic()>=deadline):
            counters['pending']+=1
            continue
        if st.st_size>MAX_FILE_BYTES:
            status='unsupported';vocab='';scanned=0;total=0
        else:
            try:
                if _sha_file(path)!=record['sha256']:
                    counters['modified']+=1
                    continue
                status,vocab,scanned,total=_safe_extract(path)
            except OSError:
                counters['modified']+=1
                continue
        compressed=zlib.compress(vocab.encode('utf-8'),6)
        if len(compressed)>MAX_COMPRESSED:
            # Avoid unlimited SQLite cache growth on unexpected text.
            status='partial';vocab='';compressed=zlib.compress(b'',6)
        with state.c:
            state.c.execute('''INSERT INTO pdf_search_index(
                remote_key,sha256,size_bytes,mtime_ns,index_version,status,
                scanned_pages,total_pages,compressed_terms)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(remote_key) DO UPDATE SET
                sha256=excluded.sha256,size_bytes=excluded.size_bytes,
                mtime_ns=excluded.mtime_ns,index_version=excluded.index_version,
                status=excluded.status,scanned_pages=excluded.scanned_pages,
                total_pages=excluded.total_pages,compressed_terms=excluded.compressed_terms''',
                (record['remote_key'],record['sha256'],st.st_size,st.st_mtime_ns,
                 INDEX_VERSION,status,scanned,total,compressed))
        counters['processed']+=1
        counters[status]+=1
        if status in ('ready','partial'):
            terms[rel]=vocab
    counters['searchable']=counters['ready']+counters['partial']
    counters['complete']=counters['pending']==0
    return terms,counters


def cached_terms(root:Path,db:sqlite3.Connection,records:list[dict],courses:list[dict])->dict[str,str]:
    """Render mode: reuse verified cached terms without reindexing or DB writes."""
    from .sync import course_dir
    prefixes=tuple(course_dir(c) for c in courses)
    try:
        columns={r[1] for r in db.execute('PRAGMA table_info(pdf_search_index)')}
        if 'compressed_terms' not in columns:return {}
    except sqlite3.Error:
        return {}
    out={}
    for record in records:
        info=_valid_pdf(root,record,prefixes)
        if not info:continue
        rel,_,stat=info
        row=db.execute('SELECT * FROM pdf_search_index WHERE remote_key=?',
                       (record['remote_key'],)).fetchone()
        if _matches(row,record,stat) and row['status'] in ('ready','partial'):
            out[rel]=_decode(row)
    return out
