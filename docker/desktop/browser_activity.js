/* Visual feedback for real browser events; never changes focus, input, or values. */
(() => {
  if (window.__calliopeActivityInstalled) return;
  window.__calliopeActivityInstalled = true;
  let host, pointer, focus, timer;
  const ensure = () => {
    if (host?.isConnected) return;
    host = document.createElement('div');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'position:fixed;inset:0;pointer-events:none!important;z-index:2147483647;';
    const root = host.attachShadow({mode: 'closed'});
    const style = document.createElement('style');
    style.textContent = `:host{pointer-events:none!important}div{pointer-events:none!important;position:fixed;box-sizing:border-box}
      .pointer{width:16px;height:16px;margin:-8px;border:2px solid #fff;border-radius:50%;background:#248c88aa;box-shadow:0 0 0 3px #248c8855;transition:transform 100ms linear,opacity 180ms;opacity:0;left:0;top:0}
      .focus{border:2px solid #55c9c2;border-radius:4px;opacity:0;transition:opacity 180ms}
      @media(prefers-reduced-motion:reduce){.pointer,.focus{transition:none}}`;
    pointer = document.createElement('div'); pointer.className = 'pointer';
    focus = document.createElement('div'); focus.className = 'focus';
    root.append(style, pointer, focus); (document.documentElement || document.body).append(host);
  };
  const fade = () => { clearTimeout(timer); timer = setTimeout(() => { pointer.style.opacity = '0'; focus.style.opacity = '0'; }, 1100); };
  for (const type of ['pointermove', 'pointerdown']) document.addEventListener(type, event => {
    if (!event.isTrusted) return;
    ensure(); pointer.style.transform = `translate(${event.clientX}px,${event.clientY}px)`;
    pointer.style.opacity = '1'; focus.style.opacity = '0';
    if (type === 'pointerdown') pointer.animate([{outline:'0 solid #55c9c288'}, {outline:'12px solid #55c9c200'}], {duration:450});
    fade();
  }, {capture:true,passive:true});
  document.addEventListener('focusin', event => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    const box = event.target.getBoundingClientRect();
    if (!box.width || !box.height) return;
    ensure(); Object.assign(focus.style, {left:`${box.left-2}px`,top:`${box.top-2}px`,width:`${box.width+4}px`,height:`${box.height+4}px`,opacity:'1'}); fade();
  }, {capture:true,passive:true});
})();
