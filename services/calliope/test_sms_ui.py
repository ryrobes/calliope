"""The existing secure Stage forms work for SMS, including non-secret login grants."""
import pytest
import sms
import calliope
import connection_forms
from test_zen_chat_layout import zen_page


@pytest.mark.parametrize('width',[1500,390])
@pytest.mark.parametrize('action_id',['sms.connect','sms.user'])
def test_sms_forms_submit_native_contract(zen_page,width,action_id):
    page=zen_page
    page.set_viewport_size({'width':width,'height':1100})
    action=next(a for a in sms.actions() if a['id']==action_id)
    values=({'account_sid':'AC'+'a'*32,'phone_number':'+13215550000','webhook_url':'https://demo.example.com/webhooks/twilio'} if action_id=='sms.connect' else
            {'phone_number':'+13215550123','owner_email':'joe@example.com','enabled':True})
    handoff=calliope._secure_action_handoff(action,values['phone_number'],['TWILIO_AUTH_TOKEN'] if action_id=='sms.connect' else [],values)
    payload=calliope._project_tool_result('administer_calliope_action',handoff,{},'sms')[0]['payload']
    posted=[]
    def execute(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=400,json={'error':{'message':'Check the settings.'},'surface_payload':{**payload,'revision':1,'error':'Check the settings.'}})
    page.route('**/api/calliope/actions/'+action_id+'/execute',execute)
    page.evaluate('''payload=>{qa.state.current.channel='sms';qa.state.current.access_role='owner';
        qa.state.surfaces=[{id:'sms-connect',turn_id:'one',kind:'action',title:'Text messaging',payload}];
        qa.setupEvents();qa.renderChat(true);qa.renderStage();document.body.classList.remove('mobile-chat-open');}''',payload)
    form=page.locator('[data-stage-secure-action-form]')
    assert form.is_visible()
    assert form.locator('[name=phone_number]').input_value()==values['phone_number']
    if action_id=='sms.connect':form.locator('[name="secret:TWILIO_AUTH_TOKEN"]').fill('secret-ui-canary')
    else:form.locator('[name=enabled]').uncheck()
    assert form.evaluate('n=>n.scrollWidth<=n.clientWidth+1')
    form.get_by_role('button',name='Test and connect' if action_id=='sms.connect' else 'Save phone access',exact=True).click()
    page.wait_for_function('qa.state.surfaces[0].payload.revision===1')
    assert len(posted)==1
    page.wait_for_function("!document.querySelector('[data-stage-secure-action-form]')?.dataset.busy")
    assert form.get_by_role('button',name='Test and connect' if action_id=='sms.connect' else 'Save phone access',exact=True).is_enabled()
    if action_id=='sms.user':assert posted[0]['inputs']['enabled'] is False
    else:
        assert posted[0]['inputs']['secret:TWILIO_AUTH_TOKEN']=='secret-ui-canary'
        assert form.locator('[name="secret:TWILIO_AUTH_TOKEN"]').input_value()==''
        assert 'secret-ui-canary' not in page.evaluate('JSON.stringify(qa.state.surfaces)')
    form.screenshot(path=f'/tmp/{action_id}-{width}.png')


def test_sms_owner_can_answer_native_question_on_floor(zen_page):
    page=zen_page
    requests=[]
    def answer(route):
        body=route.request.post_data_json
        requests.append(body)
        route.fulfill(json={'id':'question','status':'answered','revision':2,'answers':body['answers'],'delivery_status':'queued'})
    page.route('**/api/calliope/input-requests/question/answer',answer)
    page.evaluate("qa.state.current.channel='sms';qa.state.current.access_role='owner';qa.setupEvents();qa.renderChat();qa.renderSessionAccess()")
    assert page.locator('#chat-decisions').is_visible()
    page.locator('#chat-decisions textarea').fill('For executives')
    page.locator('#chat-decisions label:has(input[value=leaders])').click()
    page.locator('#chat-decisions button[type=submit]').last.click()
    page.wait_for_function("qa.state.surfaces[0].payload.request_state.status==='answered'")
    assert requests==[{'answers':{'audience':'leaders','notes':'For executives'},'revision':1}]
