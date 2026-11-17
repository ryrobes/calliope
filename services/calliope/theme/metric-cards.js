/* Shared governed metric cards for Gallery and Floor. No page state or navigation. */
(() => {
"use strict";
let sparkGradientSequence=0;
 function esc(value){return String(value==null?'':value).replace(/[&<>"']/g,function(char){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char];});}
 function text(value){return String(value==null?'':value).replace(/\s+/g,' ').trim();}
 function relative(value){
   if(!value)return 'not observed';var time=Date.parse(value);if(!Number.isFinite(time))return String(value);
   var seconds=Math.max(0,(Date.now()-time)/1000),steps=[[3600,60,'m'],[86400,3600,'h'],[604800,86400,'d'],[2629800,604800,'w'],[31557600,2629800,'mo']];
   if(seconds<90)return 'just now';for(var i=0;i<steps.length;i++){if(seconds<steps[i][0])return Math.floor(seconds/steps[i][1])+steps[i][2]+' ago';}
   return Math.floor(seconds/31557600)+'y ago';
 }
 function numeric(value){
   if(typeof value==='number'&&Number.isFinite(value))return value;
   if(typeof value==='string'&&value.trim()!==''&&Number.isFinite(Number(value)))return Number(value);
   if(value&&typeof value==='object'){
     if(Number.isFinite(Number(value.value)))return Number(value.value);
     if(value.row&&typeof value.row==='object'){
       var found=Object.values(value.row).find(function(item){return typeof item==='number'&&Number.isFinite(item);});
       if(found!==undefined)return Number(found);
     }
   }return null;
 }
 function formatValue(value,display,name){
   var number=numeric(value);if(number===null)return value==null?'—':typeof value==='object'?JSON.stringify(value):String(value);
   display=display||{};
   // A metric's name is identity, never a display-format instruction.
   var format=text(display.format).toLowerCase(),unit=text(display.unit).toLowerCase();
   var integer=['int','integer','count'].includes(format);
   var decimals=integer?0:Number.isInteger(Number(display.decimals))?Math.max(0,Math.min(Number(display.decimals),8)):2,rendered='';
   if(format==='currency'||(!format&&(display.currency||unit==='usd'))){
     try{rendered=new Intl.NumberFormat(undefined,{style:'currency',currency:display.currency||'USD',notation:Math.abs(number)>=10000?'compact':'standard',maximumFractionDigits:decimals}).format(number);}catch(ignore){}
   }else if(['percent','percentage','pct','rate','%'].includes(format)||(!format&&['percent','percentage','pct','%'].includes(unit))){
     var pct=Math.abs(number)<=1.25?number:number/100;
     rendered=new Intl.NumberFormat(undefined,{style:'percent',maximumFractionDigits:decimals}).format(pct);
   }
   if(!rendered)rendered=new Intl.NumberFormat(undefined,{notation:Math.abs(number)>=100000?'compact':'standard',maximumFractionDigits:decimals}).format(number);
   if(display.prefix&&!rendered.startsWith(String(display.prefix)))rendered=String(display.prefix)+rendered;
   if(display.suffix&&!rendered.endsWith(String(display.suffix)))rendered+=String(display.suffix);
   if(display.unit&&!display.prefix&&!display.suffix&&!/^(?:%|percent|percentage|pct|usd|currency)$/i.test(text(display.unit)))rendered+=' '+display.unit;
   return rendered;
 }
	 function points(metric){return (metric.series||[]).map(function(point){return {value:numeric(point.numeric_value!=null?point.numeric_value:point.value),point:point};}).filter(function(item){return item.value!==null;});}
 function smoothPath(points){
   // Monotone cubic interpolation: soften corners without inventing peaks
   // or dipping below the observations, including flat runs and reversals.
   var slopes=[],tangents=[],n=points.length;
   for(var i=0;i<n-1;i++){
     var dx=points[i+1][0]-points[i][0];
     if(dx<=0)return points.map(function(p,j){return (j?'L':'M')+' '+p[0].toFixed(2)+' '+p[1].toFixed(2);}).join(' ');
     slopes.push((points[i+1][1]-points[i][1])/dx);
   }
   tangents[0]=slopes[0];tangents[n-1]=slopes[n-2];
   for(var j=1;j<n-1;j++)tangents[j]=slopes[j-1]*slopes[j]<=0?0:2*slopes[j-1]*slopes[j]/(slopes[j-1]+slopes[j]);
   for(var k=0;k<n-1;k++){
     if(slopes[k]===0){tangents[k]=tangents[k+1]=0;continue;}
     var a=tangents[k]/slopes[k],b=tangents[k+1]/slopes[k],length=Math.hypot(a,b);
     if(length>3){tangents[k]=3*a*slopes[k]/length;tangents[k+1]=3*b*slopes[k]/length;}
   }
   var path='M '+points[0][0].toFixed(2)+' '+points[0][1].toFixed(2);
   for(var p=1;p<n;p++){
     var left=points[p-1],right=points[p],step=(right[0]-left[0])/3;
     path+=' C '+(left[0]+step).toFixed(2)+' '+(left[1]+step*tangents[p-1]).toFixed(2)+' '+(right[0]-step).toFixed(2)+' '+(right[1]-step*tangents[p]).toFixed(2)+' '+right[0].toFixed(2)+' '+right[1].toFixed(2);
   }
   return path;
 }
	 function sparkline(metric,rangeDays=7,smooth=false,floor=false){
	   var values=points(metric);if(!values.length||(!floor&&values.length<2))return '';
	   var w=600,h=160,pad=0,min=Math.min.apply(null,values.map(function(item){return item.value;})),max=Math.max.apply(null,values.map(function(item){return item.value;}));if(max===min){var cardFlatPad=Math.abs(max)*.08||1;min-=cardFlatPad;max+=cardFlatPad;}var range=max-min;
	   var windowEnd=Date.now(),windowStart=windowEnd-rangeDays*86400000;
	   var historyScope=metric.series_scope==='history',tickScope=metric.series_scope==='ticks',observedScope=metric.series_scope==='observed_window',periodScope=metric.series_scope==='period_snapshots';
       if(periodScope){windowStart=Date.parse(values[0].point.period_start||values[0].point.observed_at);windowEnd=Date.parse(values[values.length-1].point.period_start||values[values.length-1].point.observed_at);}
	   var x=function(item,index){if(values.length===1)return w-8;if(historyScope||tickScope)return pad+index/Math.max(1,values.length-1)*(w-pad*2);var stamp=Date.parse((periodScope?(item.point.period_start||item.point.observed_at):observedScope?item.point.observed_at:(item.point.data_as_of||item.point.observed_at))||'');if(!Number.isFinite(stamp))return pad+index/Math.max(1,values.length-1)*(w-pad*2);return pad+Math.max(0,Math.min(1,(stamp-windowStart)/Math.max(1,windowEnd-windowStart)))*(w-pad*2);},y=function(value){return 12+(max-value)/range*(h-24);};
	   var path=values.map(function(item,index){return (index?'L':'M')+' '+x(item,index).toFixed(2)+' '+y(item.value).toFixed(2);}).join(' '),gradientId='metric-card-area-gradient-'+(++sparkGradientSequence),firstX=x(values[0],0),lastX=x(values[values.length-1],values.length-1);
	   if(smooth&&values.length>1)path=smoothPath(values.map(function(item,index){return [x(item,index),y(item.value)];}));
       var endpoint=floor?'<circle class="metric-card-endpoint" cx="'+lastX+'" cy="'+y(values[values.length-1].value)+'" r="3"></circle>':'';
	   return '<div class="metric-card-chart" aria-hidden="true"'+(tickScope?' title="Latest '+values.length+' readings · evenly spaced"':historyScope?' title="Sparse in this window - showing full observed history"':'')+'><svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none"><defs><linearGradient id="'+gradientId+'" x1="0" y1="0" x2="0" y2="1"><stop class="metric-card-area-top" offset="0%"></stop><stop class="metric-card-area-mid" offset="54%"></stop><stop class="metric-card-area-clear" offset="82%"></stop><stop class="metric-card-area-floor" offset="100%"></stop></linearGradient></defs><path class="metric-card-area" fill="url(#'+gradientId+')" d="'+path+' L '+lastX+' '+h+' L '+firstX+' '+h+' Z"></path><path class="metric-card-line" d="'+path+'"></path>'+endpoint+'</svg></div>';
	 }
	 function statusInfo(metric){
	   var snapshot=metric.snapshot||{},observed=Boolean(snapshot.observation_id||snapshot.data_as_of||snapshot.observed_at),raw=String(snapshot.status||'').toLowerCase(),bad=observed&&(snapshot.ok===false||/(fail|breach|error)/.test(raw)),good=observed&&(snapshot.ok===true||/(pass|healthy|ok)/.test(raw));
	   return {label:snapshot.status||(observed?'observed':'not observed'),className:bad?'bad':good?'good':'neutral',bad:bad,observed:observed};
	 }
	 function statusMarkup(status){if(!status.observed)return '<span class="metric-card-status missing" title="No materialized observation" aria-label="Not observed">×</span>';if(status.bad)return '<span class="metric-card-status bad">'+esc(status.label)+'</span>';return '';}
 function dateLabel(value,zone,withTime=false){
   if(!value||!Number.isFinite(Date.parse(value)))return '';
   var options={month:'short',day:'numeric',timeZone:zone||'UTC'};
   if(withTime){options.hour='numeric';options.minute='2-digit';}
   try{return new Intl.DateTimeFormat(undefined,options).format(new Date(value));}catch(_){options.timeZone='UTC';return new Intl.DateTimeFormat(undefined,options).format(new Date(value));}
 }
 function measurementMarkup(metric){
   var m=metric.measurement||{},label=m.label||'Latest reading',zone=m.time_zone||'',stamp=(metric.snapshot||{}).observed_at;
   if(m.kind==='observation_day'){
     label='Recorded day';
     if(stamp&&zone){try{var f=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'});label=f.format(new Date(stamp))===f.format(new Date())?'Today':dateLabel(stamp,zone);}catch(_){label=dateLabel(stamp,'UTC')||label;}}
   }
   if(zone)label+=' · '+zone.split('/').pop().replace(/_/g,' ');
   return '<span class="metric-card-measurement" title="'+esc((m.detail||'')+(zone?' Timezone: '+zone+'.':'')+' Headline is the latest recorded value; chart periods do not recalculate it.')+'">'+esc(label)+'</span>';
 }
 function axisMarkup(metric){
   var values=points(metric);if(!values.length)return '<div class="metric-card-axis"><span>No readings in this period</span></div>';
   var first=values[0].point,last=values[values.length-1].point,zone=(metric.measurement||{}).time_zone||'UTC';
   var left=dateLabel(first.period_start||first.observed_at,zone),right=dateLabel(last.period_start||last.observed_at,zone);
   if(values.length===1)return '<div class="metric-card-axis"><span>One reading · '+esc(right)+'</span></div>';
   if(left===right){left=dateLabel(first.observed_at,zone,true);right=dateLabel(last.observed_at,zone,true);}
   return '<div class="metric-card-axis"><span>'+esc(left)+'</span><span>'+esc(right)+'</span></div>';
 }
 function deltaMarkup(metric,floor=false){
   var trend=metric.trend;if(!trend)return '';
   var meaning=trend.meaning==='good'?'good':trend.meaning==='bad'?'bad':'neutral',sign=trend.direction==='up'?'▲':trend.direction==='down'?'▼':'–',value=trend.percent==null?'':Math.abs(Number(trend.percent)).toFixed(1)+'%';
   var direction=trend.direction==='up'?'Up':trend.direction==='down'?'Down':'No change',meaningLabel=meaning==='good'?'favorable':meaning==='bad'?'unfavorable':'with no preferred direction defined',label=direction+(value?' '+value:'')+' · '+meaningLabel+(trend.basis?' · '+trend.basis:'');
   if(floor){
     var values=points(metric);if(values.length<2)return '';
     if(!value){if(trend.absolute==null)return '';value=formatValue(Math.abs(Number(trend.absolute)),metric.display,metric.name);}
     var first=values[0].point,zone=(metric.measurement||{}).time_zone||'UTC';
     value+=' since '+dateLabel(first.observed_at,zone);
     label+=' · Baseline reading: '+dateLabel(first.observed_at,zone,true)+'. This is not a period-over-period comparison.';
   }
   return '<small class="metric-card-delta '+meaning+'" title="'+esc(label)+'" aria-label="'+esc(label)+'"><b aria-hidden="true">'+sign+'</b>'+(value?'<span>'+esc(value)+'</span>':'')+'</small>';
 }
	 function cardMarkup(metric,options={}){
	   var snapshot=metric.snapshot||{},status=statusInfo(metric),stamp=['ticks','observed_window','period_snapshots'].includes(metric.series_scope)?snapshot.observed_at:(snapshot.data_as_of||snapshot.observed_at),meta=(metric.subcategory?metric.subcategory+' · ':'')+'v'+metric.version+(metric.grain?' · '+metric.grain:'')+(metric.parameter_count?' · '+metric.parameter_count+' params':''),age=stamp?relative(stamp):'no history';
	   return '<article class="metric-gallery-card '+(status.bad?'bad':'')+'" data-metric-card="'+esc(metric.name)+'">'
	     +'<div class="metric-card-grid"></div>'+sparkline(metric,options.rangeDays||7,Boolean(options.smooth),Boolean(options.floor))
	     +'<button type="button" class="metric-card-open" data-metric-open="'+esc(metric.name)+'" aria-label="'+esc(options.openLabel||('Open Metric Lens for '+metric.title))+'"></button>'
	     +(options.actions||'')+'<div class="metric-card-content">'
	     +statusMarkup(status)
	     +'<div class="metric-card-value"><strong>'+esc(formatValue(snapshot.value,metric.display,metric.name))+'</strong>'+(options.floor?measurementMarkup(metric):'')+deltaMarkup(metric,Boolean(options.floor))+'</div>'
         +(options.floor?axisMarkup(metric):'')
	     +'<h2 class="metric-card-title" title="'+esc(metric.title)+'">'+esc(metric.title)+'</h2><p class="metric-card-description" title="'+esc(metric.description||'')+'">'+esc(metric.description||'')+'</p>'
	     +'<footer class="metric-card-foot"><span title="'+esc(meta)+'">'+esc(options.floor?(metric.subcategory||'Core metric'):meta)+'</span><span title="'+esc(stamp?'Observed '+age:'No materialized history')+'">'+esc(options.floor&&stamp?'Updated '+age:age)+'</span></footer></div></article>';
	 }

window.CalliopeMetricCards=Object.freeze({esc,text,relative,numeric,formatValue,points,sparkline,statusInfo,statusMarkup,deltaMarkup,cardMarkup});
})();
