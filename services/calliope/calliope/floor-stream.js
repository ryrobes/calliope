/* Independent lane transport and variable-height rendering, shared by live/AS-OF. */
(function(root){
'use strict';
class FloorStream {
  constructor(fetchPage,onChange){this.fetchPage=fetchPage;this.onChange=onChange;this.revision=0;this.controllers=new Set();this.pending=new Set();this.resetting=false;this.data={cards:[],lanes:[],pages:{},facets:{},total:0};this.params={}}
  applyPresentations(titles,revision){
    if(revision!==this.revision)return false;
    const labels=new Map(titles.map(t=>[String(t.item_id),t]));let changed=false;
    for(const card of this.data.cards){const label=labels.get(String(card.item_id));if(!label)continue;
      if(card.semantic_title!==label.semantic_title||card.semantic_status!==label.semantic_status){
        card.semantic_title=label.semantic_title;card.semantic_status=label.semantic_status;changed=true;
      }
    }
    return changed;
  }
  cancel(){this.revision++;for(const c of this.controllers)c.abort();this.controllers.clear();this.pending.clear()}
  async reset(params,initial){this.cancel();this.params={...params};this.resetting=true;if(initial){this.resetting=false;this.data=initial;this.onChange(this.data,{reset:true});return}return this.request(null,true)}
  async request(lane,reset=false){
    if(this.pending.has(lane)||(lane&&this.resetting))return;
    const cursor=lane?this.data.pages?.[lane]?.next_cursor:null;if(lane&&!cursor)return;
    const revision=this.revision,controller=new AbortController();this.controllers.add(controller);this.pending.add(lane);
    let released=false;const release=()=>{if(released)return;released=true;if(revision===this.revision){if(reset)this.resetting=false;this.pending.delete(lane);this.controllers.delete(controller)}};
    this.onChange(this.data,{loading:true,lane});
    try{
      const params={...this.params};if(lane){params.lane=lane;params.cursor=cursor}
      const data=await this.fetchPage(params,controller.signal);if(revision!==this.revision)return;
      if(reset)this.data=data;
      else {const known=new Set(this.data.cards.map(c=>c.item_id));this.data={...this.data,pages:{...this.data.pages,...data.pages},cards:[...this.data.cards,...data.cards.filter(c=>!known.has(c.item_id))]}}
      release();this.onChange(this.data,{reset,lane});
    }catch(error){release();if(revision===this.revision&&error.name!=='AbortError')this.onChange(this.data,{error,lane,reset})}
    finally{release()}
  }
}
class FloorWindow {
  constructor(body,{items=[],total=0,markup,header='',onRender=()=>{},onMore=()=>{},heights=new Map()}){
    Object.assign(this,{body,items,total,markup,header,onRender,onMore,heights});this.estimate=170;this.frame=0;this.loading=false;this.error=null;this.nodes=new Map();
    body.innerHTML=`${header}<div data-window-top></div><div data-window-cards></div><div data-window-bottom></div><div class="floor-window-status" data-window-status role="status"></div><button type="button" class="more" data-window-more hidden>Could not load · Retry</button>`;
    this.top=body.querySelector('[data-window-top]');this.cards=body.querySelector('[data-window-cards]');this.bottom=body.querySelector('[data-window-bottom]');this.message=body.querySelector('[data-window-status]');this.retry=body.querySelector('[data-window-more]');
    this.scroll=()=>{cancelAnimationFrame(this.frame);this.frame=requestAnimationFrame(()=>this.draw())};body.addEventListener('scroll',this.scroll,{passive:true});body.addEventListener('dragend',this.scroll);
    this.click=e=>{if(e.target.closest('[data-window-more]'))this.onMore()};body.addEventListener('click',this.click);
    this.resize=new ResizeObserver(this.scroll);this.resize.observe(body);this.resize.observe(this.cards);
    this.draw();
  }
  destroy(){cancelAnimationFrame(this.frame);this.resize.disconnect();this.body.removeEventListener('scroll',this.scroll);this.body.removeEventListener('dragend',this.scroll);this.body.removeEventListener('click',this.click)}
  update(items,total){this.items=items;this.total=total;this.loading=false;this.error=null;this.draw()}
  status(loading,error){this.loading=loading;this.error=error;this.draw()}
  height(item){return this.heights.get(item.item_id)||this.estimate}
  draw(){
    const {body,items}=this;if(!body.isConnected||body.querySelector('.dragging'))return;
    const scroll=body.scrollTop,viewport=body.clientHeight||600;
    const header=body.querySelector('[data-window-header],.brief-tile'),headerHeight=header?header.getBoundingClientRect().height+parseFloat(getComputedStyle(header).marginBottom||0):0;
    const offsets=[0];for(const item of items)offsets.push(offsets.at(-1)+this.height(item));
    let start=0,end=items.length;
    while(start<items.length&&offsets[start+1]<scroll-headerHeight-viewport)start++;
    end=start;while(end<items.length&&offsets[end]<scroll-headerHeight+viewport*2)end++;
    // Keep the same DOM nodes throughout the overscan window: loaded images,
    // focus, hover, and native drag must not be torn down on each scroll event.
    const wanted=new Set(items.slice(start,end).map(item=>item.item_id));let changed=false;
    for(const [id,node] of this.nodes){if(!wanted.has(id)){node.el.remove();this.nodes.delete(id);changed=true}}
    items.slice(start,end).forEach((item,index)=>{
      const html=this.markup(item);let node=this.nodes.get(item.item_id);
      if(!node||node.html!==html){
        const template=document.createElement('template');template.innerHTML=html;const el=template.content.firstElementChild;
        if(node){const focused=node.el.contains(document.activeElement),action=['data-piece-action','data-piece-async','data-piece-aside'].find(a=>document.activeElement?.hasAttribute(a));node.el.replaceWith(el);if(focused){const target=action?el.querySelector(`[${action}]`):el;target?.focus({preventScroll:true})}}
        node={el,html};this.nodes.set(item.item_id,node);changed=true;
      }
      const at=this.cards.children[index];if(at!==node.el)this.cards.insertBefore(node.el,at||null);
    });
    this.top.style.height=offsets[start]+'px';this.bottom.style.height=(offsets.at(-1)-offsets[end])+'px';
    const more=items.length<this.total;
    this.retry.hidden=!this.error;this.message.textContent=this.loading?'Loading more…':!items.length&&!this.header&&!this.error?'Nothing here':'';
    this.message.hidden=!this.loading&&(items.length>0||!!this.header||!!this.error);
    // Anchor the visible item while late typography / image sizes settle.
    let anchor=start;while(anchor+1<items.length&&offsets[anchor+1]<=scroll-headerHeight)anchor++;
    let measuredChange=false;
    [...this.cards.children].forEach((el,i)=>{const h=el.getBoundingClientRect().height+parseFloat(getComputedStyle(el).marginBottom||0);if(h>0&&Math.abs(this.height(items[start+i])-h)>1){this.heights.set(items[start+i].item_id,h);measuredChange=true}});
    if(changed)this.onRender(body);
    if(measuredChange){let corrected=0;for(let i=0;i<anchor;i++)corrected+=this.height(items[i]);body.scrollTop=Math.max(0,scroll+corrected-offsets[anchor]);this.scroll();return}
    if(!this.loading&&!this.error&&more&&scroll+viewport*2>=headerHeight+offsets.at(-1))this.onMore();
  }
}
root.FloorStream=FloorStream;root.FloorWindow=FloorWindow;
})(globalThis);
