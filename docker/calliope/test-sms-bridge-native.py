"""Image-build smoke test against the actual pinned Hermes SMS adapter; no network."""
import sys
sys.path.insert(0, '/opt/hermes')
sys.path.insert(0, '/usr/local/bin')
import sms_bridge as b

import hashlib,hmac,base64,tempfile
from pathlib import Path
from unittest.mock import patch
from plugins.platforms.sms import adapter as native
account={'account_sid':'AC'+'a'*32,'auth_token':'fake-test-token','phone_number':'+13215550000','webhook_url':'https://demo.example.com/webhooks/twilio'}
params={'AccountSid':account['account_sid'],'To':account['phone_number'],'From':'+13215550123','Body':'A photo','MessageSid':'SM'+'c'*32}
data=account['webhook_url']+''.join(k+params[k] for k in sorted(params))
sig=base64.b64encode(hmac.new(account['auth_token'].encode(),data.encode(),hashlib.sha1).digest()).decode()
assert b.validate({'account':account,'params':params,'signature':sig})['valid'], 'signature'
assert not b.validate({'account':account,'params':{**params,'Body':'tampered'},'signature':sig})['valid'], 'tamper'
assert not b.validate({'account':account,'params':params,'signature':'wrong'})['valid'], 'invalid'
assert b.format_reply({'account':account,'text':'**Ready**'})['chunks']==['Ready'], 'format'
chunks=b.format_reply({'account':account,'text':'hello '*600})['chunks']
assert len(chunks)>=3 and all(len(c)<=1600 for c in chunks), 'chunks'
calls=[]
class Response:
 status=201
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 async def json(self):return {'sid':'SM'+'e'*32}
class Client:
 def post(self,url,**kwargs):
  assert url=='https://api.twilio.com/2010-04-01/Accounts/'+account['account_sid']+'/Messages.json'
  calls.append(url);return Response()
 async def close(self):pass
with tempfile.TemporaryDirectory() as root, patch.object(native,'_new_session',lambda **kw:Client()):
 request={'account':account,'to':params['From'],'text':'Hello','attempt_id':'native-test'}
 result=b.send(request,spool=Path(root)/'sms.sqlite')
 assert result['state']=='sent', result
 assert b.send(request,spool=Path(root)/'sms.sqlite')==result
 assert len(calls)==1
print('Native Hermes SMS adapter: signatures, tampering, formatting, 1600-character chunking, submission and retry deduplication passed. Network disabled.')
