// No credentials, executable schemes or protocol-relative destinations.
export function safeLink(value) {
  if(typeof value!=='string'||!value||value.length>2048||/[\s\\\x00-\x1f\x7f]/.test(value))return undefined;
  if(value.startsWith('/')&&!value.startsWith('//'))return value;
  try {const url=new URL(value);if(value.startsWith('https://')&&url.hostname&&!url.username&&!url.password)return value;}catch{}
  return undefined;
}
