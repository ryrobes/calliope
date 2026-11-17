"""The same authenticated shell links and account actions on every workspace page."""
import asyncio
import re
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
import calliope
import calliope_dreams
import warehouse_theme


class Shell(HTMLParser):
    def __init__(self,html):
        super().__init__();self.nav=[];self.menu=[];self.active=[];self.system_ids=0
        header=re.search(r'<nav\b[^>]*data-warehouse-header[^>]*>(.*?)</nav>',html,re.S)
        self.feed(header.group(1) if header else html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag!='a':return
        if a.get('id')=='system-workspace-open':self.system_ids+=1
        if a.get('href') in ['/floor','/gallery','/meaning','/team','/calliope?system=1'] and a.get('role')!='menuitem':
            # Brand anchors have their own accessible label, not a workspace link.
            if not a.get('aria-label'):
                self.nav.append(a['href'])
                if a.get('aria-current')=='page':self.active.append(a['href'])
        if a.get('role')=='menuitem':self.menu.append(a['href'])


@pytest.mark.parametrize('admin',[False,True])
def test_shell_navigation_and_menu_agree_across_routes(monkeypatch,tmp_path,admin):
    import auth,business_logic,correspondence,meaning_web,server,sms
    routes={}
    class MCP:
        @staticmethod
        def custom_route(path,methods):
            def register(fn):routes[(path,tuple(methods))]=fn;return fn
            return register
    session={'identity':'viewer@example.test','mapped':True,'via':'password'}
    actor_checks=[]
    def is_admin(factory,actor):actor_checks.append(actor);return admin
    monkeypatch.setattr(auth,'read_session_full',lambda request:session)
    monkeypatch.setattr(auth,'background_layer',lambda *a,**kw:'')
    monkeypatch.setattr(calliope,'ensure_tables',lambda _:None)
    monkeypatch.setattr(calliope.provider_settings,'ensure',lambda _:None)
    monkeypatch.setattr(calliope,'_start_session_synopsis_worker',lambda _:None)
    monkeypatch.setattr(calliope.hermes_docs,'ensure',lambda _:None)
    monkeypatch.setattr(calliope.hermes_config,'ensure',lambda _:None)
    monkeypatch.setattr(calliope_dreams,'is_company_admin',is_admin)
    monkeypatch.setattr(business_logic,'ensure',lambda _:None)
    monkeypatch.setattr(correspondence,'ensure',lambda _:None)
    monkeypatch.setattr(sms,'ensure',lambda _:None)
    monkeypatch.setenv('WAREHOUSE_HERMES_URL','http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY','test-only')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path))
    calliope.register_calliope_routes(MCP(),lambda:None,'',lambda html:html)
    meaning_web.register(MCP(),SimpleNamespace(_conn=lambda:None),lambda req:('viewer@example.test',session,None),None)
    expected_nav=['/floor','/gallery','/meaning','/team']+(['/calliope?system=1'] if admin else [])
    assert ('/ops' in Shell(warehouse_theme.account_control(session,workspace_menu=True,can_administer=admin)).menu)==admin
    expected_menu=Shell(warehouse_theme.account_control(session,workspace_menu=True,can_administer=admin)).menu
    for path in ['/calliope','/floor','/team','/data','/meaning','/meaning/logic','/gallery']:
        html=server._landing_html([],session['identity'],session,can_administer=admin) if path=='/gallery' else asyncio.run(routes[(path,('GET',))](SimpleNamespace())).body.decode()
        shell=Shell(html)
        assert shell.nav==expected_nav,path
        assert shell.system_ids==int(admin),path
        assert shell.menu==expected_menu,path
        assert '__CALLIOPE_WORKSPACE_NAV__' not in html
        assert 'Models &amp; agents' not in html
        assert not any('panel=models' in href for href in shell.menu)
        assert shell.active==([path] if path in ['/floor','/team','/meaning','/gallery'] else ['/meaning'] if path=='/meaning/logic' else []),path
    assert actor_checks and set(actor_checks)=={session['identity']}


@pytest.mark.parametrize('admin',[False,True])
def test_app_review_shell_uses_authenticated_admin_visibility(monkeypatch,admin):
    import app_review, calliope_dreams, auth
    monkeypatch.setattr(calliope_dreams,'is_company_admin',lambda factory,actor:admin)
    monkeypatch.setattr(auth,'background_layer',lambda *a,**kw:'')
    session={'identity':'viewer@example.test','via':'password'}
    html=app_review.page({'slug':'visits','selected':'</script><script>alert(1)</script>'},session['identity'],session=session,factory=lambda:None).body.decode()
    shell=Shell(html)
    assert shell.system_ids==int(admin)
    assert shell.active==['/gallery']
    assert shell.menu==Shell(warehouse_theme.account_control(session,workspace_menu=True,can_administer=admin)).menu
    assert '<script>alert(1)' not in html
    assert '/theme/workspace-page.css' in html
    assert 'class="card"' not in html
