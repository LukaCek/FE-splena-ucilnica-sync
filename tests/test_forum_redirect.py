"""Only same-course Moodle redirects are unsupported; all others stay errors."""
from pathlib import Path

import pytest

from fe_sync.client import MoodleClient, MoodleError, MoodleRedirect
from fe_sync.content import ForumReturnedToCourse, read_forum
from fe_sync.sync import run_sync

BASE='https://ucilnica-fe.uni-lj.si'


class Redirect:
    status_code=303
    def __init__(self,target):
        self.headers={'Location':target}
        self.closed=False
    def close(self):
        self.closed=True


class Session:
    def __init__(self,target):
        self.target=target
        self.calls=0
        self.responses=[]
    def get(self,*args,**kwargs):
        self.calls+=1
        assert kwargs['allow_redirects'] is False
        reply=Redirect(self.target)
        self.responses.append(reply)
        return reply


def client_with_redirect(target):
    client=MoodleClient.__new__(MoodleClient)
    client.base=BASE
    client.host='ucilnica-fe.uni-lj.si'
    client.timeout=30
    client.session=Session(target)
    return client


def test_only_exact_authenticated_course_redirect_is_classified():
    client=client_with_redirect('/course/view.php?id=3625')
    with pytest.raises(MoodleRedirect) as exc:
        client.get('/mod/forum/view.php',params={'id':123693})
    assert exc.value.course_id==3625
    assert client.session.calls==1
    assert client.session.responses[0].closed


@pytest.mark.parametrize('target',[
    '/login/index.php',
    'https://evil.example/course/view.php?id=3625',
    'http://ucilnica-fe.uni-lj.si/course/view.php?id=3625',
    '//evil.example/course/view.php?id=3625',
    '/course/view.php?id=3625&sesskey=secret',
    '/course/view.php?id=3625#fragment',
    '/course/view.php?id=0',
    '/course/view.php?id=999999999999999999999',
    '/course/view.php?id=3625&id=3791',
    '/course/view.php?id=%2E%2E',
    '/mod/forum/index.php?id=3625',
])
    client=client_with_redirect(target)
    with pytest.raises(MoodleRedirect) as exc:
        client.get('/mod/forum/view.php',params={'id':123693})
    assert exc.value.course_id is None


class ForumClient:
    base=BASE
    def __init__(self,redirect_course):
        self.redirect_course=redirect_course
    def html(self,path,params):
        assert path=='/mod/forum/view.php'
        assert params=={'id':123693}
        raise MoodleRedirect(course_id=self.redirect_course)


def test_same_course_can_be_skipped_without_following_redirect():
    with pytest.raises(ForumReturnedToCourse):
        read_forum(ForumClient(3625),123693,course_id=3625)


def test_other_course_or_unclassified_redirect_is_still_error():
    for target in (None,3791):
        with pytest.raises(MoodleRedirect) as exc:
            read_forum(ForumClient(target),123693,course_id=3625)
        assert not isinstance(exc.value,ForumReturnedToCourse)


class MakerLabClient:
    def __init__(self,*args):
        pass
    def courses(self):
        return [{'id':3625,'title':'MakerLab','shortname':'MAKERLAB'}]
    def modules(self,course_id):
        return [{'id':123693,'type':'forum','title':'Obvestila Forum',
                 'section_title':'Splošno'}]
    def html(self,path,params):
        assert path=='/mod/forum/view.php' and params['id']==123693
        raise MoodleRedirect(course_id=3625)


class LoginRedirectClient(MakerLabClient):
    def html(self,path,params):
        raise MoodleRedirect(course_id=None)


def test_makerlab_unavailable_forum_not_counted_as_error(tmp_path):
    root=tmp_path/'FE'
    root.mkdir()
    state=tmp_path/'state'
    state.mkdir()
    result=run_sync(root,state/'files.sqlite',state/'session.json',
                    client_factory=MakerLabClient,delay=0)
    assert result['status']=='ok',result
    assert result['announcements_found']==1
    assert result['announcements_unsupported']==1
    assert result['announcements_returned_to_course']==1
    assert result['files']['errors']==0
    assert result['navigation']['pages']==2
    index=root/'Spletna-ucilnica/Ostali-predmeti/3625_MakerLab/index.html'
    assert 'e.FE' in index.read_text(encoding='utf-8')
    txt=(root/'Spletna-ucilnica/STANJE.txt').read_text(encoding='utf-8')
    assert 'Neprebrani forumi' in txt
    html=(root/'Spletna-ucilnica/STANJE.html').read_text(encoding='utf-8')
    assert 'Opozorilo: 1 forum(ov)' in html


def test_unexpected_login_redirect_keeps_status_partial(tmp_path):
    root=tmp_path/'FE'
    root.mkdir()
    result=run_sync(root,tmp_path/'state/files.sqlite',tmp_path/'session.json',
                    client_factory=LoginRedirectClient,delay=0)
    assert result['status']=='partial'
    assert result['announcements_errors']==1
    assert result.get('announcements_returned_to_course',0)==0
    assert result['files']['errors']==1
