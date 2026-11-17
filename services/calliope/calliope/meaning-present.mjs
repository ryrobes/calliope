// Pure presentation helpers. Stored evidence is never coerced into new measures.
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const label = value => String(value ?? '').replaceAll('_', ' ');
export function exactNumber(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat(undefined, {maximumFractionDigits:20}).format(value)
    : String(value ?? 'Not recorded');
}
export function dateParts(value) {
  if (!value) return null;
  const raw = String(value), parsed = new Date(raw.replace(/^(\d{4}-\d\d-\d\d) /, '$1T'));
  if (!Number.isFinite(parsed.getTime())) return {date:raw, time:'', iso:''};
  return {date:new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric'}).format(parsed),
    time:new Intl.DateTimeFormat(undefined,{hour:'2-digit',minute:'2-digit',timeZoneName:'short'}).format(parsed),iso:parsed.toISOString()};
}
export function timestamp(value, compact=false) {
  const d=dateParts(value);
  if (!d) return '<span class="not-recorded">Not recorded</span>';
  return `<time class="timestamp${compact?' compact':''}" datetime="${escapeHtml(d.iso)}" title="${escapeHtml(value)}"><span>${escapeHtml(d.date)}</span>${d.time?`<small>${escapeHtml(d.time)}</small>`:''}</time>`;
}
export function dataValue(value, depth=0) {
  if(value == null) return '<span class="not-recorded">Not recorded</span>';
  if(typeof value !== 'object') return `<span class="${typeof value==='number'?'numeric':'data-text'}">${escapeHtml(typeof value==='number'?exactNumber(value):value)}</span>`;
  if(depth>=4) return `<code class="data-json">${escapeHtml(JSON.stringify(value))}</code>`;
  if(Array.isArray(value)) return value.length?`<ul class="data-array">${value.map(v=>`<li>${dataValue(v,depth+1)}</li>`).join('')}</ul>`:'<span class="not-recorded">None recorded</span>';
  return Object.keys(value).length?`<dl class="data-properties">${Object.entries(value).map(([k,v])=>`<div><dt>${escapeHtml(label(k))}</dt><dd>${dataValue(v,depth+1)}</dd></div>`).join('')}</dl>`:'<span class="not-recorded">None recorded</span>';
}
export function sqlMarkup(value) {
  const source=String(value||'');
  const token=/--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|\{[\w.]+\}|\b(?:select|from|where|group|by|order|limit|as|and|or|not|null|is|case|when|then|else|end|join|left|right|inner|outer|on|with|distinct|filter|over|partition|desc|asc|interval|sum|avg|count|min|max|coalesce|now|true|false)\b|\b\d+(?:\.\d+)?\b/gi;
  let output='',offset=0;
  for(const m of source.matchAll(token)) {
    output+=escapeHtml(source.slice(offset,m.index));
    const t=m[0],kind=t.startsWith('--')||t.startsWith('/*')?'comment':t[0]==="'"||t[0]==='"'?'string':t[0]==='{'?'parameter':/^\d/.test(t)?'number':'keyword';
    output+=`<span class="sql-${kind}">${escapeHtml(t)}</span>`;offset=m.index+t.length;
  }
  return output+escapeHtml(source.slice(offset));
}
export function numericHistory(observations) {
  return (Array.isArray(observations)?observations:[])
    .filter(o=>o.time!=null&&o.time!==''&&Number.isFinite(Number(o.time))&&typeof o.value==='number'&&Number.isFinite(o.value))
    .map(o=>({...o,time:Number(o.time)})).sort((a,b)=>a.time-b.time);
}
export function historyChart(observations, width=640) {
  width=Math.max(280,Number(width)||640);
  const left=width<420?62:76,right=width-28;
  const points=numericHistory(observations);
  if(points.length<2)return '<div class="panel-empty"><span class="empty-symbol">∿</span><p>A history needs two saved numeric observations.</p><small>Existing receipts remain available in Observations.</small></div>';
  const low=Math.min(...points.map(p=>p.value)),high=Math.max(...points.map(p=>p.value)),t0=points[0].time,t1=points.at(-1).time;
  const pad=high===low?Math.max(Math.abs(high)*.03,1):(high-low)*.12,lo=low-pad,hi=high+pad;
  const coords=points.map(p=>[t1===t0?(left+right)/2:left+(p.time-t0)/(t1-t0)*(right-left),154-(p.value-lo)/(hi-lo)*136]);
  const path=coords.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' '),last=points.at(-1);
  const short=v=>new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:2}).format(v);
  return `<figure class="history-figure"><figcaption><span>Recorded history <b>${points.length} observations</b></span><span class="history-range">${escapeHtml(short(low))} – ${escapeHtml(short(high))}</span></figcaption>
    <svg class="history-chart" viewBox="0 0 ${width} 194" role="img" aria-label="${points.length} saved numeric observations, range ${escapeHtml(exactNumber(low))} to ${escapeHtml(exactNumber(high))}. Values are available in the Observations table.">
    ${[hi,(hi+lo)/2,lo].map((v,i)=>`<g class="chart-tick"><path d="M${left} ${18+i*68}H${right}"/><text x="${left-12}" y="${22+i*68}">${escapeHtml(short(v))}</text></g>`).join('')}
    <path class="chart-area" d="${path} L${coords.at(-1)[0]},164 L${coords[0][0]},164 Z"/><path class="chart-line" d="${path}"/>
    ${coords.map((p,i)=>`<circle class="chart-point" cx="${p[0]}" cy="${p[1]}" r="4" tabindex="0" data-observation="${i}" aria-label="${escapeHtml(exactNumber(points[i].value))} at ${escapeHtml(points[i].observed_at)}"><title>${escapeHtml(exactNumber(points[i].value))} · ${escapeHtml(points[i].observed_at)}</title></circle>`).join('')}
    <text class="chart-date" x="${left}" y="186">${escapeHtml(dateParts(points[0].observed_at)?.date||'')}</text><text class="chart-date end" x="${right}" y="186">${escapeHtml(dateParts(last.observed_at)?.date||'')}</text></svg>
    <div class="chart-readout" aria-live="polite"><span>Latest in history</span><strong>${escapeHtml(exactNumber(last.value))}</strong>${timestamp(last.observed_at,true)}</div>
    <p class="panel-footnote">Daily saved snapshots · when the value was observed · one definition and default scope · vertical scale fits this history</p></figure>`;
}
export function matchingFields(fields, query='', role='') {
  const q=query.trim().toLowerCase();
  return (Array.isArray(fields)?fields:[]).filter(f=>(!role||(f.role||'unclassified')===role)&&[f.col_name,f.data_type,f.role,f.what,f.basis].join(' ').toLowerCase().includes(q));
}
