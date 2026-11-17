/* The answerable index matcher, shared by the Floor composer's ghost rails and
   the chat composer's suggestion rail. Both pages ask the same question — which
   governed metric does this draft seem to be about — so they must answer it the
   same way; a scorer tuned on one page and not the other would give the same
   sentence two different sets of suggestions. Pure functions over a supplied
   index: no page state, no DOM, no fetch. */
(() => {
  'use strict';
  const STOPWORDS=new Set(['the','a','an','and','or','of','for','to','in','on','by','with','vs','versus','how','many','much','what','which','who','when','where','why','is','are','was','were','do','does','did','me','my','our','we','this','that','these','those','last','next','per','from','at','as','it','its','be','can','you','show','give','tell','get','about','over','into','all','any','total','number','count','sum','rate','average','avg','month','week','year','day','quarter','months','weeks','years','days','quarters','compare','compared','between','than','more','less','top','bottom','best','worst','up','down','so','if','not','no','yes','ok','please','also','just','now','today','yesterday','tomorrow','then','there','here','out','off','one','two','three','did','has','have','had','been','will','would','could','should','like','want','need','see','look','know','think','going','trend','trending','change','changed','growth','grew','fell','rose','across','each','every','some','most','few','vs.','versus']);
  const normalize=text=>String(text||'').toLowerCase().replace(/[^\p{L}\p{N}\s]+/gu,' ').replace(/\s+/g,' ').trim();
  const stem=token=>token.length>4&&/(ies)$/.test(token)?token.slice(0,-3)+'y':token.length>3?token.replace(/(sses|ses|xes|zes|ches|shes)$/,m=>m.slice(0,-2)).replace(/s$/,''):token;
  const tokens=text=>normalize(text).split(' ').filter(token=>token.length>=3&&!STOPWORDS.has(token)).map(stem);
  // Precomputed once per metric when the index lands, so a keystroke only walks
  // the prepared sets instead of re-normalizing every title and alias.
  function prepare(metric){
    const phrases=[
      ...(metric.bindings||[]).map(binding=>({kind:'binding',text:normalize(binding.term),gloss:binding.gloss||''})),
      ...(metric.aliases||[]).map(alias=>({kind:'alias',text:normalize(alias)})),
      {kind:'title',text:normalize(metric.title||'')},
    ].filter(phrase=>phrase.text.length>=3);
    const own=new Set(tokens(`${String(metric.name||'').replace(/_/g,' ')} ${metric.title||''} ${metric.category||''}`));
    const desc=new Set(tokens(metric.description||''));
    return {...metric,_phrases:phrases,_tokens:own,_desc:desc};
  }
  // `entries` lets a caller rank a mixed set (metrics and artifacts together);
  // `metrics` is the Floor's original single-kind index. Each result carries the
  // matched row as both `item` and `metric`, so the Floor's existing readers
  // keep working while mixed callers can speak generically.
  function matches(index,text,limit=6){
    const prepared=index&&Array.isArray(index.entries)?index.entries
      :index&&Array.isArray(index.metrics)?index.metrics:null;
    if(!prepared)return [];
    const hay=` ${normalize(text)} `,queryTokens=[...new Set(tokens(text))];
    if(hay.trim().length<3)return [];
    const scored=[];
    for(const metric of prepared){
      let score=0,via=null;
      for(const phrase of metric._phrases){
        if(hay.includes(` ${phrase.text} `)){
          const phraseScore=phrase.kind==='binding'?100:phrase.kind==='alias'?90:85;
          if(phraseScore>score){score=phraseScore;via=phrase.kind==='title'?null:phrase}
        }
      }
      let hits=0;
      for(const token of queryTokens){
        if(metric._tokens.has(token))hits+=1;
        else if(token.length>=4&&[...metric._tokens].some(candidate=>candidate.length>=4&&(candidate.startsWith(token)||token.startsWith(candidate))))hits+=0.7;
        else if(metric._desc.has(token))hits+=0.35;
      }
      // Coverage of the metric's own words, not raw hit count: a seven-word
      // metric must not outrank "sales amount" just by collecting more overlaps.
      if(hits>0)score=Math.max(score,Math.round(100*hits/Math.max(1,metric._tokens.size)+5*hits));
      if(score>=30)scored.push({metric,item:metric,score,via});
    }
    scored.sort((a,b)=>b.score-a.score||String(a.item.title||"").localeCompare(String(b.item.title||"")));
    return scored.slice(0,limit);
  }
  function valueText(metric){
    const display=metric?.display||{};let value=metric?.value;
    if(value===null||value===undefined||value==='')return '—';
    let number=typeof value==='number'?value:(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(value).trim())?Number(value):null);
    if(number===null||!Number.isFinite(number))return String(value).slice(0,24);
    const decimals=Number(display.decimals);
    // Ghost tiles ask for compact notation ($77.7M) so a big value and its
    // delta chip share one row; the wider discovery cards keep every digit.
    const compact=Boolean(display.compact)&&Math.abs(number)>=1e5;
    const options=compact?{notation:'compact',maximumFractionDigits:1}:Number.isInteger(decimals)?{maximumFractionDigits:Math.max(0,Math.min(decimals,12)),minimumFractionDigits:Math.max(0,Math.min(decimals,12))}:{maximumFractionDigits:2};
    const format=String(display.format||'').toLowerCase();
    try{
      if(display.currency||format==='currency')return `${new Intl.NumberFormat(undefined,{...options,style:'currency',currency:display.currency||'USD'}).format(number)}${display.suffix?` ${display.suffix}`:''}`;
      if(format==='percent'||format==='percentage'||format==='pct')return new Intl.NumberFormat(undefined,{...options,style:'percent'}).format(Math.abs(number)<=1.25?number:number/100);
      const unit=String(display.suffix||display.unit||'').trim();
      const shownUnit=unit&&!/^counts?$/i.test(unit)?` ${unit}`:'';
      return `${display.prefix||''}${new Intl.NumberFormat(undefined,options).format(number)}${shownUnit}`.trim();
    }catch(_e){return String(number)}
  }
  // The Gallery tile's ghost timeline, miniaturized: an area + glowing line
  // normalized into a fixed viewBox, drawn behind the card content.
  function sparkline(series){
    const points=(Array.isArray(series)?series:[]).filter(p=>Number.isFinite(Number(p?.value)));
    if(points.length<2)return '';
    const values=points.map(p=>Number(p.value)),min=Math.min(...values),max=Math.max(...values),span=(max-min)||1;
    const coords=points.map((p,i)=>[(i/(points.length-1))*100,36-((Number(p.value)-min)/span)*30]);
    const line=coords.map(([x,y],i)=>`${i?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`).join('');
    const area=`${line}L100,40L0,40Z`;
    return `<span class="floor-metric-ghost" aria-hidden="true"><svg viewBox="0 0 100 40" preserveAspectRatio="none"><path class="ghost-area" d="${area}"/><path class="ghost-line" d="${line}"/></svg></span>`;
  }
  // The delta chip's words, shared so the Floor's cards and the rail's tiles
  // can never disagree about which way a metric moved or by how much.
  function trendDelta(trend){
    const t=trend||{};
    const sign=t.direction==='up'?'▲':t.direction==='down'?'▼':'–';
    const pct=Number(t.percent);
    const text=t.percent!=null&&Number.isFinite(pct)?`${sign} ${Math.abs(pct).toFixed(1)}%`:'';
    return {sign,text,meaning:t.meaning||'neutral',basis:t.basis||'vs previous observation'};
  }
  window.CalliopeAnswerable={STOPWORDS,normalize,stem,tokens,prepare,matches,valueText,sparkline,trendDelta};
})();
