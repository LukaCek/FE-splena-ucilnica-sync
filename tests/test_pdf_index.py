"""PDF full-text word search: real PDF fixtures, caching, privacy and offline HTML."""
from pathlib import Path
import hashlib
import sqlite3
import zlib

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from fe_sync.pdf_index import index_pdfs,normalize_words,cached_terms
from fe_sync.search import render_search
from fe_sync.sync import State,course_dir
from fe_sync.pages import write_generated,page

COURSE={'id':4149,'title':'Matematika I','shortname':'MAT'}


def real_pdf(path, text=None):
    """Minimal PDF with a proper Helvetica text stream; no reportlab dependency."""
    writer=PdfWriter()
    sheet=writer.add_blank_page(width=612,height=792)
    if text is not None:
        font=DictionaryObject({
            NameObject('/Type'):NameObject('/Font'),
            NameObject('/Subtype'):NameObject('/Type1'),
            NameObject('/BaseFont'):NameObject('/Helvetica')
        })
        sheet[NameObject('/Resources')]=DictionaryObject({
            NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject()
        escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream.set_data(('BT /F1 14 Tf 40 700 Td ('+escaped+') Tj ET').encode('ascii'))
        sheet[NameObject('/Contents')]=writer._add_object(stream)
    with path.open('wb') as f:writer.write(f)


def create_test_file(root,state,label='Predavanja.pdf',text='Coulombov zakon'):
    course=course_dir(COURSE)
    relative=course/'Gradiva/Predavanja__12'/label
    path=root/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    real_pdf(path,text)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    remote_key=f'4149/12/{label}'
    state.save(remote_key,relative,sha,None,None,path.stat().st_size)
    return path,remote_key


def test_normalized_words_match_coulombs_law_and_slovene_accents():
    assert normalize_words('Coulombov zakon, ELEKTRIČNO polje!')==[
        'coulombov','zakon','elektricno','polje']
    assert normalize_words('á č š ž')==[]  # one-letter tokens are intentionally omitted


def test_real_pdf_indexed_into_searchable_offline_html(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    pdf,key=create_test_file(root,state)
    records=state.list_files()
    words,report=index_pdfs(root,state,records,[COURSE])
    assert report['total']==1 and report['ready']==1,report
    assert report['searchable']==1 and report['cached']==0
    relative=pdf.relative_to(root).as_posix()
    assert 'coulombov' in words[relative].split()
    assert 'zakon' in words[relative].split()
    write_generated(root,course_dir(COURSE)/'index.html',
                    page('Matematika I','<main>predmet</main>','2026'))
    output=render_search(root,[COURSE],{4149:[]},records,'2026',pdf_terms=words)
    assert output['pdf_word_entries']==1
    html=(root/'Spletna-ucilnica/ISKANJE.html').read_text(encoding='utf-8')
    assert 'data-pdf-terms="coulombov zakon"' in html
    assert 'pdfWords.includes' in html
    assert 'Coulombov zakon' not in html  # terms are normalized, not verbatim body
    assert 'file://' not in html.split('search-results')[0].lower() or 'https' in html
    state.close()


def test_unchanged_pdf_uses_sqlite_cache_and_pdf_is_not_modified(tmp_path,monkeypatch):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    pdf,key=create_test_file(root,state)
    before=pdf.read_bytes()
    index_pdfs(root,state,state.list_files(),[COURSE])
    import fe_sync.pdf_index as pdfindex
    def must_not_reread(*a,**kw):
        raise AssertionError('cache hit should not re-read the PDF')
    monkeypatch.setattr(pdfindex,'_extract',must_not_reread)
    monkeypatch.setattr(pdfindex,'_sha_file',must_not_reread)
    vocab,result=index_pdfs(root,state,state.list_files(),[COURSE])
    assert result['cached']==1 and result['processed']==0
    assert 'coulombov' in ' '.join(vocab.values())
    assert before==pdf.read_bytes()
    state.close()


def test_locally_modified_pdf_is_never_indexed_or_overwritten(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    path,key=create_test_file(root,state)
    index_pdfs(root,state,state.list_files(),[COURSE])
    before=path.read_bytes()
    real_pdf(path,'Private local notes')   # changed content, SQLite SHA still old
    edited=path.read_bytes()
    vocab,result=index_pdfs(root,state,state.list_files(),[COURSE])
    assert result['searchable']==0
    assert not vocab
    assert path.read_bytes()==edited!=before
    state.close()


def test_untracked_files_symlinks_and_nonpdf_ignored(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    pdf,key=create_test_file(root,state)
    outside=tmp_path/'outside.pdf'
    real_pdf(outside,'PRIVATE')
    unsafe=root/course_dir(COURSE)/'Gradiva/Predavanja__12/unsafe.pdf'
    unsafe.symlink_to(outside)
    fake={'remote_key':'4149/12/unsafe.pdf',
          'rel_path':unsafe.relative_to(root).as_posix(),
          'sha256':hashlib.sha256(outside.read_bytes()).hexdigest(),
          'size_bytes':outside.stat().st_size}
    unknown={'remote_key':'4149/12/untracked.pdf',
             'rel_path':'../outside.pdf','sha256':fake['sha256'],
             'size_bytes':outside.stat().st_size}
    vocab,report=index_pdfs(root,state,state.list_files()+[fake,unknown],[COURSE])
    assert report['total']==1
    assert 'outside.pdf' not in str(vocab)
    state.close()


def test_image_only_pdf_is_reported_unsearchable_without_ocr(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    path,key=create_test_file(root,state,text=None)
    words,status=index_pdfs(root,state,state.list_files(),[COURSE])
    assert status['no_text']==1
    assert status['searchable']==0
    assert not words
    state.close()


def test_budget_marks_pending_without_error_or_overwrite(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    for name in ('a.pdf','b.pdf'):
        create_test_file(root,state,label=name)
    words,status=index_pdfs(root,state,state.list_files(),[COURSE],max_files=1)
    assert status['total']==2 and status['processed']==1
    assert status['pending']==1 and not status['complete']
    words,status=index_pdfs(root,state,state.list_files(),[COURSE],max_files=1)
    assert status['processed']==1 and status['cached']==1
    assert status['pending']==0 and status['searchable']==2
    state.close()


def test_cached_terms_validated_in_read_only_connection(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    pdf,key=create_test_file(root,state)
    index_pdfs(root,state,state.list_files(),[COURSE])
    state.close()
    connection=sqlite3.connect(f'file:{tmp_path}/state/files.sqlite?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    records=[dict(r) for r in connection.execute('select * from files')]
    loaded=cached_terms(root,connection,records,[COURSE])
    assert 'coulombov' in loaded[pdf.relative_to(root).as_posix()]
    connection.close()


def test_cache_decompression_bounded_and_corrupt_rows_fail_closed(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    path,key=create_test_file(root,state)
    index_pdfs(root,state,state.list_files(),[COURSE])
    state.c.execute('UPDATE pdf_search_index SET compressed_terms=? WHERE remote_key=?',
                    (sqlite3.Binary(b'not-zlib'),key))
    state.c.commit()
    terms,report=index_pdfs(root,state,state.list_files(),[COURSE])
    assert report['cached']==1
    assert path.relative_to(root).as_posix() not in terms or terms[path.relative_to(root).as_posix()]=='' 
    state.close()


class PdfResponse:
    status_code=200
    headers={'Content-Type':'application/pdf'}
    def __init__(self,payload):self.payload=payload
    def iter_content(self,size):yield self.payload
    def close(self):pass


class MoodleFixture:
    payload=None
    def __init__(self,cookies):pass
    def courses(self):return [COURSE]
    def modules(self,ident):
        return [{'id':12,'type':'folder','title':'Predavanja Mapa',
                 'section_title':'Predavanja'}]
    def folder_files(self,ident):
        from fe_sync.client import RemoteFile
        return [RemoteFile(('Coulomb.pdf',),
                '/pluginfile.php/50/mod_folder/content/0/Coulomb.pdf')]
    def get(self,*a,**kw):
        return PdfResponse(self.payload)


def test_end_to_end_moodle_pdf_search_and_cache(tmp_path):
    from fe_sync.sync import run_sync
    source=tmp_path/'original.pdf'
    real_pdf(source,'Coulombov zakon opisuje elektricno silo')
    MoodleFixture.payload=source.read_bytes()
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state/files.sqlite'
    first=run_sync(root,state,tmp_path/'secret',client_factory=MoodleFixture,delay=0)
    assert first['status']=='ok',first
    assert first['files']['downloaded']==1
    assert first['pdf_index']['ready']==1
    assert first['navigation']['search']['pdf_word_entries']==1
    html=(root/'Spletna-ucilnica/ISKANJE.html').read_text()
    assert 'coulombov' in html and 'elektricno' in html
    again=run_sync(root,state,tmp_path/'secret',client_factory=MoodleFixture,delay=0)
    assert again['status']=='ok'
    assert again['files']['unchanged']==1
    assert again['pdf_index']['cached']==1
    assert again['pdf_index']['processed']==0


def test_malformed_pdf_does_not_fail_core_sync(tmp_path):
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    course=course_dir(COURSE)
    rel=course/'Gradiva/Predavanja__12/broken.pdf'
    file=root/rel
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_bytes(b'%PDF-1.5\\nnot a real document')
    state.save('4149/12/broken.pdf',rel,hashlib.sha256(file.read_bytes()).hexdigest(),
               None,None,file.stat().st_size)
    terms,report=index_pdfs(root,state,state.list_files(),[COURSE])
    assert report['bad_pdf']==1 and report['pending']==0
    assert not terms
    state.close()


def test_extraction_timeout_does_not_block_other_files(tmp_path,monkeypatch):
    import time
    from fe_sync.pdf_index import _safe_extract
    import fe_sync.pdf_index as module
    root=tmp_path/'FE';root.mkdir()
    state=State(tmp_path/'state/files.sqlite')
    path,key=create_test_file(root,state)
    def hang(_path,_channel):
        time.sleep(5)
    monkeypatch.setattr(module,'_extract_worker',hang)
    t0=time.monotonic()
    status,terms,pages,total=_safe_extract(path,timeout=0.15)
    assert status in ('timeout','bad_pdf')
    assert time.monotonic()-t0<3
    state.close()



def test_status_page_explains_partial_first_run_pdf_index(tmp_path):
    from fe_sync.status import publish_status
    root=tmp_path/'FE';root.mkdir()
    state=tmp_path/'state';state.mkdir()
    summary={'status':'ok','finished_at':'2026-10-19T18:50:00+00:00',
             'files':{'errors':0},'pdf_index':{
                 'total':354,'searchable':120,'pending':200,'no_text':30,'partial':4}}
    publish_status(root,state,summary)
    txt=(root/'Spletna-ucilnica/STANJE.txt').read_text()
    html=(root/'Spletna-ucilnica/STANJE.html').read_text()
    assert '120/354' in txt and 'čaka 200' in txt
    assert 'brez besedila 30' in html
    assert 'ISKANJE.html' in html
