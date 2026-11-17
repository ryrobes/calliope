from test_zen_chat_layout import zen_page


def test_attach_documents_and_render_downloads_without_broken_images(zen_page):
    page=zen_page
    page.evaluate('qa.setupEvents()')
    assert page.locator('#image-input').get_attribute('accept') is None
    page.locator('#image-input').set_input_files([
        {'name':'report.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':b'PK demo'},
        {'name':'notes.md','mimeType':'text/markdown','buffer':b'notes'},
        {'name':'archive.zip','mimeType':'application/zip','buffer':b'PK archive'},
    ])
    page.wait_for_function('qa.state.attachments.length===3')
    assert page.locator('.attachment-file-label').count()==3
    assert page.locator('#attachment-tray img').count()==0
    page.evaluate('''() => {qa.state.turns=[{id:'t',ordinal:1,user_message:'Review these files',assistant_message:'',status:'completed',attachments:qa.state.attachments.map(a=>({...a,url:'/download'}))}];qa.renderChat();}''')
    assert page.locator('.message-attachments .attachment-file-link').count()==3
    assert page.locator('.message-attachments img').count()==0


def test_files_panel_uploads_and_points_to_shared_folder(zen_page):
    page=zen_page
    page.evaluate('qa.state.surfaces=[];qa.renderChat()')
    files=[]
    def route(r):
        if r.request.method=='POST':
            files.append({'name':r.request.headers['x-file-name'],'bytes':len(r.request.post_data_buffer),'status':'ready'})
            r.fulfill(status=201,json={'file':files[-1],'delivery':{'status':'ready'},'writable':True})
        else:r.fulfill(json={'files':files,'originals':[],'desktop_path':'/home/kasm-user/Shared/notebook','delivery':{'status':'ready'},'writable':True})
    page.route('**/api/calliope/sessions/notebook/files',route)
    page.locator('#shared-files-open').click()
    dialog=page.get_by_role('dialog',name='Conversation files')
    dialog.get_by_label('Upload files',exact=True).set_input_files({'name':'office.docx','mimeType':'application/octet-stream','buffer':b'PK document'})
    dialog.get_by_text('office.docx',exact=True).wait_for()
    assert dialog.locator('.wx-filemanager').count()==1
    assert dialog.get_by_text('office.docx',exact=True).count()>=1
    assert dialog.locator('input[webkitdirectory]').count()==1
    dialog.get_by_role('button',name='Use folder in chat').click()
    assert '/home/kasm-user/Shared/notebook' in page.evaluate('qa.composerValue()')


def test_files_navigation_rename_conflict_and_read_only_originals(zen_page):
    page=zen_page;page.evaluate('qa.state.surfaces=[];qa.renderChat()')
    files=[{'name':'Demo','type':'folder','bytes':0,'status':'ready'},
           {'name':'Demo/report.csv','type':'file','bytes':800,'status':'ready','original_id':'source'}]
    actions=[]
    def listing(r):r.fulfill(json={'files':files,'originals':[{'name':'Demo/report.csv','bytes':800,'shared_file_id':'source','url':'/original/source'}],
                                  'desktop_path':'/home/kasm-user/Shared/notebook','delivery':{'status':'ready'},'writable':True})
    def action(r):
        body=r.request.post_data_json;actions.append(body)
        if body['target'].endswith('occupied.csv'):
            r.fulfill(status=409,json={'error':{'message':'That name already exists.'}})
        else:
            files[1]['name']=body['target'];r.fulfill(json={'name':body['target']})
    page.route('**/api/calliope/sessions/notebook/files',listing)
    page.route('**/api/calliope/sessions/notebook/files/action',action)
    page.locator('#shared-files-open').click()
    dialog=page.get_by_role('dialog',name='Conversation files')
    # The table is the real SVAR view, not our previous hand-written list.
    dialog.locator('.wx-name-cell').get_by_text('Demo',exact=True).dblclick()
    row=dialog.locator('.wx-name-cell').get_by_text('report.csv',exact=True);row.click()
    row.click(button='right')
    dialog.get_by_text('Rename',exact=True).click()
    prompt=dialog.locator('.wx-modal input');prompt.fill('occupied.csv')
    prompt.press('Enter')
    dialog.get_by_text('That name already exists.',exact=True).wait_for()
    assert dialog.locator('.wx-name-cell').get_by_text('report.csv',exact=True).count()==1
    row=dialog.locator('.wx-name-cell').get_by_text('report.csv',exact=True);row.click(button='right')
    dialog.get_by_text('Rename',exact=True).click()
    prompt=dialog.locator('.wx-modal input');prompt.fill('renamed.csv');prompt.press('Enter')
    dialog.locator('.wx-name-cell').get_by_text('renamed.csv',exact=True).wait_for()
    assert actions[-1]=={'operation':'rename','path':'Demo/report.csv','target':'Demo/renamed.csv'}
    dialog.get_by_role('button',name='Uploaded originals',exact=True).click()
    assert dialog.get_by_role('button',name='Add files',exact=True).is_disabled()
    dialog.locator('.wx-name-cell').get_by_text('Demo',exact=True).dblclick()
    dialog.locator('.wx-name-cell').get_by_text('report.csv',exact=True).wait_for()
    assert dialog.locator('.wx-name-cell').get_by_text('renamed.csv',exact=True).count()==0
