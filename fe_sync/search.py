"""Self-contained offline search over verified Moodle course metadata and files.

Results are embedded into one static HTML page: Android file:// viewers never
need fetch(), a server, internet access, cookies, or a client-side search index.
"""
from __future__ import annotations

from pathlib import Path

from .client import BASE
from .pages import MARKER, e, local_href, module_url, page, valid_stored_files, write_generated
from .sync import course_dir
from .content import page_dir, forum_dir, is_announcement

MAX_RESULTS = 5000
MAX_NOTE_TEXT = 2400

KINDS = (
    ('all','Vse vrste'),
    ('course','Predmeti'),
    ('pdf','PDF'),
    ('document','Dokumenti'),
    ('image','Slike'),
    ('code','Koda in zapiski'),
    ('archive','Arhivi'),
    ('notes','Moodle strani in obvestila'),
    ('other','Druge datoteke'),
    ('online','Samo e.FE'),
)


def file_category(path: Path) -> tuple[str,str]:
    extension=path.suffix.casefold()
    if extension=='.pdf':return 'pdf','PDF'
    if extension in {'.doc','.docx','.ppt','.pptx','.xls','.xlsx','.odt','.ods','.odp','.csv','.txt'}:
        return 'document','Dokument'
    if extension in {'.png','.jpg','.jpeg','.gif','.svg'}:return 'image','Slika'
    if extension in {'.py','.ipynb','.c','.cpp','.h','.java','.tex','.md','.json'}:
        return 'code','Koda'
    if extension in {'.zip','.rar','.7z'}:return 'archive','Arhiv'
    return 'other','Datoteka'


def _owned_html(path:Path)->bool:
    if path.is_symlink() or not path.is_file():return False
    try:
        # Never index user's custom pages as Moodle-generated notes.
        with path.open('r',encoding='utf-8',errors='replace') as stream:
            return MARKER in stream.read(18000)
    except OSError:
        return False


def _note_text(path:Path)->str:
    from .status import TEXT_MARKER
    if path.is_symlink() or not path.is_file():return ''
    try:
        # Read only our generated notes and never more than 12KB per note.
        with path.open('r',encoding='utf-8',errors='replace') as stream:
            data=stream.read(12000)
        if not data.startswith(TEXT_MARKER):return ''
        return data[len(TEXT_MARKER):len(TEXT_MARKER)+MAX_NOTE_TEXT]
    except OSError:
        return ''


def search_entries(root:Path,courses:list[dict],module_lists:dict[int,list[dict]],
                   records:list[dict],pdf_terms:dict[str,str]|None=None)->list[dict]:
    """Validate destinations, avoid symlink escapes and never index session data."""
    result=[]
    pdf_terms=pdf_terms or {}
    for course in courses:
        course_id=course['id']
        name=course['title']
        base=course_dir(course)
        course_root=root/base
        local_index=course_root/'index.html'
        if _owned_html(local_index):
            result.append(dict(course_id=course_id,course=name,kind='course',
                               badge='Predmet',label=name,target=local_index,
                               search=f'{name} {course.get("shortname","")}'))
        # SQLite records are the only authority for downloaded Moodle files.
        for file in valid_stored_files(root,records,base):
            kind,badge=file_category(file)
            record=dict(course_id=course_id,course=name,kind=kind,
                        badge=badge,label=file.name,target=file,
                        search=f'{name} {file.relative_to(course_root).as_posix()}')
            if kind=='pdf':
                # Pre-extracted, bounded *words*. Never do PDF extraction in
                # the HTML renderer or browser. Only indexer-owned words enter.
                record['pdf_terms']=pdf_terms.get(file.relative_to(root).as_posix(),'')
            result.append(record)
        for module in module_lists.get(course_id,[]):
            if not isinstance(module,dict):continue
            mtype=module.get('type')
            ident=module.get('id')
            if type(ident) is not int or not (0<ident<2**31):continue
            label=str(module.get('title',''))[:180]
            if mtype in ('page','forum') and (mtype=='page' or is_announcement(module)):
                try:
                    base_note=page_dir(module) if mtype=='page' else forum_dir(module)
                except ValueError:
                    continue
                note_root=course_root/base_note
                target=note_root/'index.html'
                if _owned_html(target) and target.resolve().is_relative_to(root):
                    md=note_root/'vsebina.md' if mtype=='page' else None
                    content=_note_text(md) if md else ''
                    result.append(dict(course_id=course_id,course=name,kind='notes',
                                       badge='Stran' if mtype=='page' else 'Obvestila',
                                       label=label,target=target,
                                       search=f'{name} {label} {content}'))
                    # Archived announcement posts also participate in search.
                    if mtype=='forum':
                        for note in sorted(note_root.glob('tema-*.md'))[:150]:
                            if note.is_symlink():continue
                            target_post=note.with_suffix('.html')
                            if not _owned_html(target_post):continue
                            if not target_post.resolve().is_relative_to(root):continue
                            post_text=_note_text(note)
                            result.append(dict(course_id=course_id,course=name,kind='notes',
                                               badge='Objava',label=note.stem,
                                               target=target_post,search=f'{name} {label} {post_text}'))
                    continue
            # Online modules never include arbitrary Moodle/external supplied URLs.
            if mtype not in {'folder','resource'}:
                url=module_url(module)
                if url.startswith(BASE+'/mod/'):
                    result.append(dict(course_id=course_id,course=name,kind='online',
                                       badge='e.FE',label=label,target=url,
                                       search=f'{name} {label} {module.get("section_title","")}'))
        if len(result)>MAX_RESULTS:
            break
    return result[:MAX_RESULTS]


def render_search(root:Path,courses:list[dict],module_lists:dict[int,list[dict]],
                  records:list[dict],updated:str,pdf_terms:dict[str,str]|None=None)->dict:
    """Render searchable rows directly into HTML for robust file:// behavior."""
    relative=Path('Spletna-ucilnica/ISKANJE.html')
    home=root/'Spletna-ucilnica'
    entries=search_entries(root,courses,module_lists,records,pdf_terms=pdf_terms)
    pdf_word_entries=sum(bool(item.get('pdf_terms')) for item in entries)
    items=[]
    for ent in entries:
        target=ent['target']
        external=isinstance(target,str)
        href=target if external else local_href(home,target)
        course_id=str(ent['course_id'])
        attrs=(' target="_blank" rel="noopener noreferrer"' if external else '')
        words=ent.get('pdf_terms','')
        pdf_attr=(f' data-pdf-terms="{e(words)}"' if words else '')
        items.append(
            f'<li class="search-item" data-result data-kind="{e(ent["kind"])}" '
            f'data-course="{e(course_id)}" '
            f'data-search="{e(ent["search"])}"{pdf_attr}>'
            f'<span class="result-kind">{e(ent["badge"])}</span>'
            '<span class="result-detail">'
            f'<a href="{e(href)}"{attrs}>{e(ent["label"])}</a>'
            f'<small>{e(ent["course"])}</small></span></li>')
    options=''.join(f'<option value="{e(k)}">{e(v)}</option>' for k,v in KINDS)
    subjects=''.join(f'<option value="{e(c["id"])}">{e(c["title"])}</option>' for c in courses)
    body=(
        '<div class="breadcrumb"><a href="index.html">Moji predmeti</a> / Iskanje</div>'
        '<div class="page-title"><h1>Iskanje po vseh predmetih</h1></div>'
        '<main id="content" class="course-shell">'
        '<p class="muted-info">Vsebina te strani je shranjena neposredno v HTML in deluje tudi brez interneta. '
        'Išče po imenih gradiv, po prepoznanih arhiviranih Moodle straneh in po '
        '<strong>besedah iz besedilnih PDF-jev</strong>. Več iskanih besed se mora '
        'pojaviti v istem dokumentu, ne nujno zaporedoma. '
        'Skenirani PDF-ji brez besedilne plasti zahtevajo OCR in niso vključeni; '
        'zelo veliki ali obsežni PDF-ji so lahko obdelani le delno. '
        'Ne preiskuje tvojih osebnih zapiskov.</p>'
        f'<p class="muted-info">Z besedami iz vsebine je indeksiranih {pdf_word_entries} PDF-jev. '
        'Na prvem zagonu lahko indeksiranje še poteka; napredek je v STANJE.html.</p>'
        '<div class="search-controls">'
        '<label for="globalSearch">Iskalni niz'
        '<input id="globalSearch" type="search" placeholder="Npr. kolokvij, PDF, elektrotehnika ..." '
        'oninput="filterGlobalResults()" autocomplete="off"></label>'
        '<label for="searchKind">Vrsta gradiva<select id="searchKind" '
        'onchange="filterGlobalResults()">'+options+'</select></label>'
        '<label for="searchCourse">Predmet<select id="searchCourse" '
        'onchange="filterGlobalResults()"><option value="all">Vsi predmeti</option>'
        +subjects+'</select></label></div>'
        f'<p class="search-counter" id="searchCount" role="status" aria-live="polite">'
        f'{len(items)} zadetkov</p>'
        '<ul class="search-results" id="searchResults">'
        +''.join(items)+'</ul>'
        '<p class="search-empty" id="searchEmpty" hidden>Ni zadetkov. Poskusi z drugim iskalnim nizom ali filtrom.</p>'
        '</main>')
    html=page('Iskanje',body,updated,home='index.html',status_href='STANJE.html',
              updates_href='NOVOSTI.html',calendar_href='KOLEDAR.html',
              search_href='ISKANJE.html')
    write_generated(root,relative,html)
    return {'items':len(entries),'home':relative.as_posix(),
            'embedded':True,'limited':len(entries)>=MAX_RESULTS,
            'pdf_word_entries':pdf_word_entries}
