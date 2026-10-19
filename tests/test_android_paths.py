"""Portable Syncthing paths must not get stuck on Android's shared storage."""
import hashlib
import pytest

from fe_sync.sync import State, course_dir, folder_dir, legacy_folder_dir, migrate_legacy_folder

COURSE={'id':4154,'title':'Atomika in optika'}
MODULE={'type':'folder','id':144739,'title':'Kaj rešujemo na vajah? (skupini 2 in 6) Mapa'}


def setup_old_folder(tmp_path):
    root=tmp_path/'FE'; root.mkdir()
    state=State(tmp_path/'state.sqlite')
    old_rel=course_dir(COURSE)/legacy_folder_dir(MODULE)
    path=root/old_rel
    path.mkdir(parents=True)
    for i in range(1,9):
        file=path/f'{i}_vaje.pdf'
        data=f'%PDF-1.7\nExample {i}'.encode()
        file.write_bytes(data)
        key=f'{COURSE["id"]}/{MODULE["id"]}/{file.name}'
        state.save(key,old_rel/file.name,hashlib.sha256(data).hexdigest(),None,None,len(data))
    return root,state,old_rel


def test_android_folder_has_no_forbidden_characters():
    folder=str(folder_dir(MODULE))
    assert folder=='Gradiva/Kaj rešujemo na vajah (skupini 2 in 6)__144739'
    assert not set(folder).intersection(set('?*<>:"\\|'))
    assert '?' in str(legacy_folder_dir(MODULE))


def test_existing_eight_pdfs_migrate_without_download_or_deletion(tmp_path):
    root,state,old_rel=setup_old_folder(tmp_path)
    assert migrate_legacy_folder(root,state,COURSE,MODULE)==8
    new_rel=course_dir(COURSE)/folder_dir(MODULE)
    assert not (root/old_rel).exists()
    assert len(list((root/new_rel).glob('*.pdf')))==8
    assert {r['rel_path'] for r in state.list_files()}=={str(new_rel/f'{i}_vaje.pdf') for i in range(1,9)}
    assert migrate_legacy_folder(root,state,COURSE,MODULE)==0
    state.close()


def test_never_move_modified_or_unmanaged_files(tmp_path):
    root,state,old_rel=setup_old_folder(tmp_path)
    (root/old_rel/'1_vaje.pdf').write_bytes(b'my notes')
    with pytest.raises(ValueError,match='locally modified'):
        migrate_legacy_folder(root,state,COURSE,MODULE)
    assert (root/old_rel).exists()
    state.close()


def test_recover_rename_interrupted_before_sqlite_update(tmp_path):
    root,state,old_rel=setup_old_folder(tmp_path)
    new_rel=course_dir(COURSE)/folder_dir(MODULE)
    (root/old_rel).rename(root/new_rel)
    assert migrate_legacy_folder(root,state,COURSE,MODULE)==8
    assert migrate_legacy_folder(root,state,COURSE,MODULE)==0
    state.close()


def test_unmanaged_extra_file_blocks_migration(tmp_path):
    root,state,old_rel=setup_old_folder(tmp_path)
    (root/old_rel/'my-notes.txt').write_text('keep me')
    with pytest.raises(ValueError,match='untracked or missing'):
        migrate_legacy_folder(root,state,COURSE,MODULE)
    assert (root/old_rel/'my-notes.txt').exists()
    state.close()
