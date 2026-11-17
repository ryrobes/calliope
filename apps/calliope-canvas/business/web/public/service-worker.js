const CACHE='calliope-field-shell-v2';
self.addEventListener('install',event=>event.waitUntil((async()=>{
 const cache=await caches.open(CACHE),response=await fetch('/');if(!response.ok)throw Error('App shell unavailable');
 const html=await response.clone().text();await cache.put('/',response);
 // The first page's JS/CSS may load before this worker controls it. Precache
 // those dependencies explicitly so the first offline reload is complete.
 const assets=[...html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)"/g)].map(m=>m[1]);
 await cache.addAll([...new Set(assets)]);await self.skipWaiting();
})()));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('calliope-field-shell-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{const r=event.request,u=new URL(r.url);if(r.method!=='GET'||u.origin!==location.origin||u.pathname.startsWith('/api/'))return;
 event.respondWith(fetch(r).then(response=>{if(response.ok){const copy=response.clone();event.waitUntil(caches.open(CACHE).then(c=>c.put(r,copy)));}return response;}).catch(async()=>await caches.match(r)||((r.mode==='navigate')?await caches.match('/'):Response.error())));
});
