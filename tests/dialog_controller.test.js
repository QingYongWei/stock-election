'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
let document;
class Element {
  constructor(id,tag='DIV'){this.id=id;this.tagName=tag;this.listeners=new Map();this.attributes=new Map();this.childNodes=[];this.dataset={};this.style={};this.isConnected=true;this.disabled=false;this.hidden=false;this.open=false;this.textContent='';}
  addEventListener(type,fn){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(fn);}
  removeEventListener(type,fn){this.listeners.get(type)?.delete(fn);}
  fire(type,values={}){const e={target:this,prevented:false,preventDefault(){this.prevented=true;},...values};for(const fn of [...(this.listeners.get(type)||[])])fn(e);return e;}
  append(...nodes){this.childNodes.push(...nodes);}
  replaceChildren(...nodes){this.childNodes=nodes;}
  closest(){return null;}
  hasAttribute(k){return this.attributes.has(k);}
  setAttribute(k,v){this.attributes.set(k,v);}
  removeAttribute(k){this.attributes.delete(k);}
  focus(){document.activeElement=this;}
  showModal(){if(this.failOpen)throw Error('cannot open');this.open=true;}
  close(){this.open=false;queueMicrotask(()=>this.fire('close'));}
  getBoundingClientRect(){return {left:10,right:500,top:10,bottom:500};}
  querySelectorAll(){return ['close','cancel','confirm'].map(k=>nodes.get('action-dialog-'+k));}
  contains(node){return this===node||this.querySelectorAll().includes(node);}
  set innerHTML(_){throw Error('Dialog content must never be injected as HTML');}
}
const nodes=new Map();
for(const key of ['action-dialog','action-dialog-icon','action-dialog-badge','action-dialog-title','action-dialog-description','action-dialog-context','action-dialog-confirm','action-dialog-cancel','action-dialog-close'])nodes.set(key,new Element(key,key==='action-dialog'?'DIALOG':key.endsWith('confirm')||key.endsWith('cancel')||key.endsWith('close')?'BUTTON':'DIV'));
const caller=new Element('caller','BUTTON'),main=new Element('main','MAIN');
document={activeElement:caller,body:{style:{overflow:'auto'}},getElementById:id=>nodes.get(id),querySelector:()=>main,createElement:tag=>new Element('',tag.toUpperCase())};
const window={};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../stock_agent/static/action-dialog.js'),'utf8'),{document,window,queueMicrotask});
const api=window.AppDialog,dialog=nodes.get('action-dialog'),cancel=nodes.get('action-dialog-cancel'),confirm=nodes.get('action-dialog-confirm'),close=nodes.get('action-dialog-close');
(async()=>{
  let p=api.confirm({kind:'fee',title:'<img src=x onerror=bad()>',description:'Cost boundary',details:[{label:'Model',value:'<script>not HTML</script>'}]});
  assert.equal(api.isOpen,true);assert.equal(document.activeElement,cancel);assert.equal(document.body.style.overflow,'hidden');assert.equal(nodes.get('action-dialog-title').textContent,'<img src=x onerror=bad()>');assert.equal(nodes.get('action-dialog-context').childNodes[1].textContent,'<script>not HTML</script>');
  assert.equal(await api.confirm({title:'must not queue'}),false);assert.equal(api.isOpen,true);
  dialog.fire('cancel');assert.equal(await p,false);assert.equal(dialog.open,false);assert.equal(document.activeElement,caller);assert.equal(document.body.style.overflow,'auto');
  p=api.confirm({kind:'danger'});confirm.fire('click');assert.equal(await p,true);api.cancel();assert.equal(api.isOpen,false);
  p=api.confirm({kind:'discard'});close.focus();let e=dialog.fire('keydown',{key:'Tab',shiftKey:true});assert.equal(e.prevented,true);assert.equal(document.activeElement,confirm);e=dialog.fire('keydown',{key:'Tab',shiftKey:false});assert.equal(document.activeElement,close);assert.equal(e.prevented,true);
  document.activeElement=nodes.get('action-dialog-description');e=dialog.fire('keydown',{key:'Enter'});assert.equal(e.prevented,true);assert.equal(api.isOpen,true);dialog.fire('keydown',{key:'Escape'});assert.equal(await p,false);
  p=api.confirm();dialog.fire('click',{clientX:20,clientY:20});assert.equal(api.isOpen,true);dialog.fire('click',{clientX:1,clientY:1});assert.equal(await p,false);
  const first=api.confirm();api.cancel();const second=api.confirm();assert.equal(await first,false);await Promise.resolve();assert.equal(api.isOpen,true,'An old close event must not cancel a new decision');cancel.fire('click');assert.equal(await second,false);
  caller.isConnected=false;document.activeElement=caller;p=api.confirm();api.cancel();assert.equal(await p,false);assert.equal(document.activeElement,main);assert.equal(main.hasAttribute('tabindex'),false);
  dialog.failOpen=true;p=api.confirm();assert.equal(await p,false);assert.equal(api.isOpen,false);assert.equal(document.body.style.overflow,'auto');
  for(const node of nodes.values())for(const listeners of node.listeners.values())assert.equal(listeners.size,0,'No listeners may leak between decisions');
  console.log('Dialog controller state, focus, cancellation, concurrency and text-safety checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
