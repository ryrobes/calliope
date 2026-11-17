import React from 'react';
import {renderMarkdown} from '@a2ui/markdown-it';
const h=React.createElement;
// Sanitized (DOMPurify) HTML from the same renderer published apps use for Text.
export function Markdown({props:p}){
  const [html,setHtml]=React.useState('');
  React.useEffect(()=>{let live=true;renderMarkdown(String(p.text||'')).then(value=>{if(live)setHtml(value);});return()=>{live=false;};},[p.text]);
  return h('div',{className:`c-markdown ink-${p.tone||'normal'}`,dangerouslySetInnerHTML:{__html:html}});
}
