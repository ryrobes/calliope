import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = readFileSync(new URL('./calliope/dictation.js', import.meta.url), 'utf8');
const sleep = ms => new Promise(r => setTimeout(r, ms));
function harness({failLive=false,deferBatch=false,maxSeconds}={}) {
  let completeBatch;
  const tracks=[], inserts=[], messages=[], requests=[], channels=[], timers=[];
  class Recorder extends EventTarget {
    static isTypeSupported(){return true}
    constructor(){super();this.state='inactive';this.mimeType='audio/webm'}
    start(){this.state='recording'}
    stop(){this.state='inactive';queueMicrotask(()=>{
      this.dispatchEvent(Object.assign(new Event('dataavailable'),{data:new Blob(['test audio'])}));
      this.dispatchEvent(new Event('stop'));
    })}
  }
  class Channel extends EventTarget {
    readyState='open';
    send(raw){assert.equal(JSON.parse(raw).type,'input_audio_buffer.commit');queueMicrotask(()=>this.emit('conversation.item.input_audio_transcription.completed',{transcript:'Three submitted leads.',item_id:'one'}))}
    emit(type,data){this.dispatchEvent(Object.assign(new Event('message'),{data:JSON.stringify({type,...data})}))}
    close(){this.readyState='closed';this.dispatchEvent(new Event('close'))}
  }
  class Peer extends EventTarget {
    addTrack(){}
    createDataChannel(){const channel=new Channel();channels.push(channel);return channel}
    async createOffer(){return {type:'offer',sdp:'v=0\r\nm=audio'}}
    async setLocalDescription(){}
    async setRemoteDescription(){}
    close(){}
  }
  const context={Blob,FormData,AbortController,MediaRecorder:Recorder,RTCPeerConnection:Peer,Map,Date,JSON,
    setInterval,clearInterval,clearTimeout,
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return setTimeout(fn,ms===12000?100:ms===140?1:ms)},
    navigator:{mediaDevices:{getUserMedia:async()=>{const track={enabled:true,stopped:false,stop(){this.stopped=true}};tracks.push(track);return {getTracks:()=>[track],getAudioTracks:()=>[track]}}}}};
  context.window=context;
  vm.runInNewContext(source,context);
  const config={enabled:true,realtime:{enabled:true},max_audio_seconds:maxSeconds};
  const controller=context.CalliopeDictation.create({config:()=>config,
    fetch:async(url)=>{requests.push(url);if(url.endsWith('realtime-transcription'))return {ok:!failLive,status:503,text:async()=>failLive?'{}':'v=0\r\nm=audio'};
      if (deferBatch) await new Promise(resolve=>{completeBatch=resolve});
      return {ok:true,json:async()=>({text:'Fallback transcript.'})}},
    onTranscript:(target,text)=>inserts.push({target,text}),onMessage:(...args)=>messages.push(args)});
  return {controller,tracks,inserts,messages,requests,channels,context,timers,completeBatch:()=>completeBatch()};
}
for (const kind of ['chat','floor','daily_note']) {
  const h=harness();await h.controller.start({kind});
  h.channels[0].emit('conversation.item.input_audio_transcription.delta',{delta:'Three ',item_id:'one'});
  h.channels[0].emit('conversation.item.input_audio_transcription.delta',{delta:'submitted',item_id:'one'});
  assert.equal(h.controller.state.liveTranscript,'Three submitted');assert.equal(h.inserts.length,0);
  h.controller.stop();await sleep(25);
  assert.equal(h.inserts[0].text,'Three submitted leads.');assert.equal(h.inserts[0].target.kind,kind);
  assert.equal(h.requests.length,1);assert.equal(h.controller.state.phase,'idle');assert.ok(h.tracks.every(t=>t.stopped));
}
{
 const h=harness({failLive:true});await h.controller.start({kind:'floor'});h.controller.stop();await sleep(25);
 assert.equal(h.inserts[0].text,'Fallback transcript.');assert.equal(h.requests.length,2);
}
{
 const h=harness();await h.controller.start({kind:'floor'});h.controller.cancel();await sleep(25);
 h.channels[0].emit('conversation.item.input_audio_transcription.completed',{transcript:'stale'});
 assert.equal(h.inserts.length,0);assert.equal(h.requests.length,1);assert.ok(h.tracks.every(t=>t.stopped));
}
{
 const h=harness({failLive:true,deferBatch:true});await h.controller.start({kind:'chat'});h.controller.stop();await sleep(10);
 assert.equal(h.controller.state.phase,'transcribing');h.controller.cancel();h.completeBatch();await sleep(10);
 assert.equal(h.inserts.length,0);assert.equal(h.controller.state.phase,'idle');
}
{
 const h=harness();let grant;const track={stopped:false,stop(){this.stopped=true}};
 h.context.navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>{grant=resolve});
 const pending=h.controller.start({kind:'floor'});h.controller.cancel();
 grant({getTracks:()=>[track],getAudioTracks:()=>[track]});await pending;
 assert.ok(track.stopped);assert.equal(h.requests.length,0);assert.equal(h.controller.state.phase,'idle');
}
console.log('Dictation: live partials, manual finalization, three destinations, fallback, cancellation and stale events passed.');

{
 const h=harness();await h.controller.start({kind:'chat'});
 assert.ok(h.timers.some(t=>t.ms===600000));
 assert.ok(!h.timers.some(t=>t.ms===120000));
 h.controller.cancel();await sleep(10);
}
{
 const h=harness({maxSeconds:45});await h.controller.start({kind:'chat'});
 const deadline=h.timers.find(t=>t.ms===45000);assert.ok(deadline);deadline.fn();await sleep(25);
 assert.equal(h.controller.state.phase,'idle');assert.equal(h.inserts.length,1);
 assert.ok(h.messages.some(([text])=>text.includes('Recording limit reached')));
 assert.ok(h.tracks.every(t=>t.stopped));
}
console.log('Dictation: ten-minute default, explicit shorter limit, automatic finalization passed.');
