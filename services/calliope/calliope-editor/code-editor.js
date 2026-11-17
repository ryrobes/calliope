import {EditorState, Prec} from '@codemirror/state';
import {EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, indentWithTab} from '@codemirror/commands';
import {HighlightStyle, syntaxHighlighting, bracketMatching, foldGutter} from '@codemirror/language';
import {sql, PostgreSQL} from '@codemirror/lang-sql';
import {json} from '@codemirror/lang-json';
import {tags} from '@lezer/highlight';

const theme=EditorView.theme({
 '&':{height:'100%',font:'12px/1.6 var(--mono)',color:'var(--ink)',backgroundColor:'transparent'},
 '&.cm-focused':{outline:'none'},
 '.cm-scroller':{overflow:'auto',fontFamily:'var(--mono)',lineHeight:'1.6'},
 '.cm-content':{padding:'10px 0',caretColor:'var(--accent)'},
 '.cm-gutters':{backgroundColor:'transparent',color:'var(--muted)',borderRight:'1px solid var(--line)'},
 '.cm-lineNumbers .cm-gutterElement':{minWidth:'3ch',padding:'0 8px'},
 '.cm-activeLine':{backgroundColor:'color-mix(in oklch,var(--accent) 5%,transparent)'},
 '.cm-cursor':{borderLeftColor:'var(--accent)'},
 '&.cm-focused .cm-selectionBackground, .cm-selectionBackground':{backgroundColor:'color-mix(in oklch,var(--accent) 24%,transparent)'},
 '.cm-matchingBracket':{backgroundColor:'color-mix(in oklch,var(--accent) 18%,transparent)'},
});
const highlighting=HighlightStyle.define([
 {tag:[tags.keyword,tags.operatorKeyword,tags.null],color:'var(--syntax-keyword,var(--accent))'},
 {tag:[tags.string,tags.special(tags.string)],color:'var(--syntax-string,#66bca8)'},
 {tag:[tags.number,tags.bool],color:'var(--syntax-number,#b394e4)'},
 {tag:[tags.propertyName,tags.attributeName],color:'var(--syntax-identifier,#78b5d3)'},
 {tag:[tags.typeName,tags.function(tags.variableName)],color:'var(--syntax-function,#78b5d3)'},
 {tag:tags.comment,color:'var(--muted)',fontStyle:'italic'},
 {tag:tags.operator,color:'var(--syntax-operator,var(--accent))'},
 {tag:tags.invalid,color:'var(--danger,#e17878)'},
]);

export function mountCodeEditor(host,{value='',language='sql',label,readOnly=false,onChange=()=>{},onRun}={}){
 let setting=false;
 const extensions=[
  language==='json'?json():sql({dialect:PostgreSQL}),theme,syntaxHighlighting(highlighting),
  lineNumbers(),foldGutter(),drawSelection(),bracketMatching(),EditorView.lineWrapping,
  EditorState.readOnly.of(readOnly),EditorView.editable.of(!readOnly),
  EditorView.contentAttributes.of({'aria-label':label,role:'textbox','aria-multiline':'true','aria-readonly':String(readOnly),tabindex:'0'}),
  ...(readOnly?[]:[history(),highlightActiveLine()]),
  Prec.highest(keymap.of(onRun?[{key:'Mod-Enter',run:()=>{onRun();return true;}}]:[])),
  keymap.of([...defaultKeymap,...historyKeymap,indentWithTab]),
  EditorView.updateListener.of(update=>{if(update.docChanged&&!setting)onChange(update.state.doc.toString());}),
 ];
 const view=new EditorView({parent:host,state:EditorState.create({doc:value,extensions})});
 return {
  getValue:()=>view.state.doc.toString(),
  setValue(text){if(text===view.state.doc.toString())return;setting=true;try{view.setState(EditorState.create({doc:text,extensions}));}finally{setting=false;}},
  focus:()=>view.focus(),measure:()=>view.requestMeasure(),destroy:()=>view.destroy(),
 };
}
