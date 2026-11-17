"""Bounded local delivery evidence; never execute uploaded content."""
import base64
import re
import uuid
from pathlib import PurePath
MAX_BYTES=5*1024*1024
TYPES={'image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff','application/pdf':b'%PDF-','text/plain':None}
def validate(store,body):
    if not isinstance(body,dict):raise store.rejection('Invalid evidence object.')
    mime=body.get('mime');name=body.get('name');encoded=body.get('data')
    if mime not in TYPES or not isinstance(name,str) or not 1<=len(name)<=180 or PurePath(name).name!=name or any(ord(c)<32 for c in name):raise store.rejection('Choose a PNG, JPEG, PDF or plain-text file with a valid filename.')
    if not isinstance(encoded,str) or len(encoded)>((MAX_BYTES+2)//3)*4:raise store.rejection('Delivery files must be at most 5 MB.')
    try:data=base64.b64decode(encoded,validate=True)
    except ValueError:raise store.rejection('Invalid attachment encoding.') from None
    if not 0<len(data)<=MAX_BYTES or TYPES[mime] and not data.startswith(TYPES[mime]):raise store.rejection('File content does not match its type or size limit.')
    if mime=='text/plain':
        try:data.decode('utf-8')
        except UnicodeDecodeError:raise store.rejection('Text attachments must use UTF-8.') from None
    return data

def upload(store,body):
    data=validate(store,body);name=body['name'];mime=body['mime']
    id='ATT-'+uuid.uuid4().hex
    with store.lock,store.connect() as db:db.execute('INSERT INTO business_attachments(id,name,mime,size,content) VALUES (?,?,?,?,?)',(id,name,mime,len(data),data))
    return {'id':id,'name':name,'mime':mime,'size':len(data)}

def get(store,id):
    if not re.fullmatch(r'ATT-[a-f0-9]{32}',id):raise store.rejection('Attachment not found.',404)
    with store.connect() as db:r=db.execute('SELECT * FROM business_attachments WHERE id=?',(id,)).fetchone()
    if not r:raise store.rejection('Attachment not found.',404)
    return dict(r)
