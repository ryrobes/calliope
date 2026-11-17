"""Real Stage review controls: responsive layout, editing, sends and context."""
import copy
import json

import pytest
from test_zen_chat_layout import zen_page


def fixture_batch():
    return {'id':'batch-one','surface_id':'mail-one','owner_email':'viewer@example.com','title':'Weekly branch review',
            'mailbox':{'enabled':True,'address':'callie@example.com'},'messages':[],
            'drafts':[{'id':f'draft-{i}','contact_id':f'person-{i}','ordinal':i,'revision':1,'status':'draft',
                'recipient':f'{name.lower()}@example.com','subject':f'This week at {branch}',
                'body':f'Hi {name},\n\nTwo things need attention this week.\n\nThe branch one-sheet is attached. Please review the missing plate photos and let me know which transfers you want to prioritize.\n\nThanks,\nCallie',
                'attachments':[],'evidence_refs':[],
                'context':{'person':{'name':name,'role':'Branch manager','organization':branch,'summary':'Handles branch operations and warranty returns. Prefers a short checklist.',
                    'channels':[{'channel':'email','address':f'{name.lower()}@example.com'},{'channel':'sms','address':'+13215550123'}]},'notes':[]}}
             for i,(name,branch) in enumerate([('Joe','Riverside'),('Ann','Lakeside'),('Sam','Harbor Point')])]}


def install(page,batch):
    def route(r):
        if r.request.method=='POST':
            data=r.request.post_data_json
            if r.request.url.endswith('/send'):
                for selection in data['drafts']:
                    next(d for d in batch['drafts'] if d['id']==selection['id'])['status']='queued'
            elif '/drafts/' in r.request.url:
                draft=next(d for d in batch['drafts'] if d['id']==r.request.url.rsplit('/',1)[-1])
                if 'held' in data:draft['status']='held' if data['held'] else 'draft'
                else:draft.update(subject=data['subject'],body=data['body'],revision=draft['revision']+1)
        r.fulfill(json=batch)
    page.route('**/api/calliope/mail/batches/**',route)
    page.evaluate('''batch=>{
      qa.state.zen=false;document.body.classList.remove('zen');
      qa.state.surfaces=[{id:batch.surface_id,turn_id:'one',kind:'email',title:batch.title,payload:batch,
        source:{origin:'reviewed_email'},presentation:{phase:'published',size:'wide'}}];
      qa.setupEvents();qa.renderChat();qa.renderStage();
    }''',copy.deepcopy(batch))


@pytest.mark.parametrize('width',[1600,1050,700])
@pytest.mark.parametrize('zen',[False,True])
def test_email_card_layout_and_paging(zen_page,width,zen):
    page=zen_page;page.set_viewport_size({'width':width,'height':1100});install(page,fixture_batch())
    if zen:page.evaluate("qa.state.zen=true;document.body.classList.add('zen');qa.renderStage()")
    if width==700:page.evaluate("document.body.classList.remove('mobile-chat-open')")
    card=page.locator('[data-mail-batch]').first
    assert card.is_visible()
    assert card.evaluate('n=>n.scrollWidth<=n.clientWidth+1')
    assert card.locator('.mail-envelope').inner_text().find('Joe')>=0
    card.locator('[data-mail-action=next]').click()
    assert 'Ann' in card.locator('.mail-envelope').inner_text()
    card.locator('[data-mail-action=about]').click()
    assert 'short checklist' in card.locator('.mail-about').inner_text()
    assert '+13215550123' in card.locator('.mail-about').inner_text()
    card.locator('[data-mail-action=about]').click()
    card.screenshot(path=f'/tmp/calliope-email-review-{width}-{zen}.png')


def test_edit_survives_repack_and_batch_send_excludes_held(zen_page):
    page=zen_page;batch=fixture_batch();install(page,batch)
    card=page.locator('[data-mail-batch]').first
    card.locator('[data-mail-action=edit]').click()
    card.locator('[data-mail-body]').fill('Joe, please send the replacement photo by Friday.')
    page.evaluate('qa.renderStage()')
    assert card.locator('[data-mail-body]').input_value()=='Joe, please send the replacement photo by Friday.'
    card.locator('[data-mail-action=save]').click()
    page.wait_for_function("qa.state.surfaces[0].payload.drafts[0].revision===2")
    card.locator('[data-mail-action=hold]').click()
    page.wait_for_function("qa.state.surfaces[0].payload.drafts[0].status==='held'")
    assert card.locator('[data-mail-action=all]').inner_text()=='Send all 2 drafts'
    with page.expect_request('**/api/calliope/mail/batches/batch-one/send') as sent:
        card.locator('[data-mail-action=all]').click()
    assert [x['id'] for x in sent.value.post_data_json['drafts']]==['draft-1','draft-2']
    assert batch['drafts'][0]['status']=='held'


def test_revision_handoff_and_secure_setup(zen_page):
    page=zen_page;batch=fixture_batch();batch['mailbox']={'enabled':False};install(page,batch)
    card=page.locator('[data-mail-batch]').first
    card.locator('[data-mail-action=revise]').click()
    assert 'draft-0' in page.evaluate('qa.composerValue()')
    card.locator('[data-mail-action=connect]').click()
    dialog=page.locator('#mail-connection-dialog')
    assert dialog.is_visible()
    assert dialog.locator('[name=password]').get_attribute('type')=='password'
    dialog.locator('[name=password]').fill('not-a-real-password')
    dialog.locator('[data-close]').first.click()
    assert dialog.locator('[name=password]').input_value()==''


def test_contact_note_survives_repack(zen_page):
    page=zen_page;install(page,fixture_batch());card=page.locator('[data-mail-batch]').first
    card.locator('[data-mail-action=about]').click()
    card.locator('[data-mail-context]').fill('Joe now handles returns for Lakeside.')
    page.evaluate('qa.renderStage()')
    assert card.locator('[data-mail-context]').input_value()=='Joe now handles returns for Lakeside.'


def test_selected_recipient_stays_visible_in_narrow_card(zen_page):
    page=zen_page;page.set_viewport_size({'width':700,'height':1100})
    batch=fixture_batch()
    for draft in batch['drafts']:
        draft['context']['person']['name'] += ' — branch operations and warranty returns'
    install(page,batch)
    page.evaluate("document.body.classList.remove('mobile-chat-open')")
    card=page.locator('[data-mail-batch]').first
    assert card.locator('.mail-recipients').evaluate('n=>n.scrollWidth>n.clientWidth')
    card.locator('[data-mail-action=next]').click()
    card.locator('[data-mail-action=next]').click()
    for _ in range(2):
        page.wait_for_function('''() => {
          const rail=document.querySelector('.mail-recipients');
          const selected=rail.querySelector('[aria-current="true"]');
          const r=rail.getBoundingClientRect(),s=selected.getBoundingClientRect();
          return selected.dataset.index==='2' && s.left>=r.left-1 && s.right<=r.right+1;
        }''')
        page.evaluate('qa.renderStage()')
    assert card.evaluate('n=>n.scrollWidth<=n.clientWidth+1')
