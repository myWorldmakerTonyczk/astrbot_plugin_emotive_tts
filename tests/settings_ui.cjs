const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = process.argv[2];
class Element {
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.classList={add(){},toggle(){}};this._value='';}
  set value(v){this._value=String(v);} get value(){return this._value;}
  append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attributes[k]=v;} removeAttribute(k){delete this.attributes[k];}
  addEventListener(k,f){this['on'+k]=f;}
  all(){return this.children.filter(x=>x instanceof Element).flatMap(x=>[x,...x.all()]);}
  querySelectorAll(q){return this.all().filter(x=>q==='[data-key]'?'key' in x.dataset:q==='[data-tab]'?'tab' in x.dataset:false);}
  querySelector(q){return this.all().find(x=>q.startsWith('#')?x.id===q.slice(1):q.startsWith('.')?x.className?.split(' ').includes(q.slice(1)):false);}
}
(async()=>{
 const schema=JSON.parse(fs.readFileSync(path.join(root,'views/settings/schema.json'),'utf8'));
 assert(!('voice_style' in schema));assert(schema.voice.required);assert(!schema.voice_persona.required);
 const fields=new Element();fields.id='settings';
 const page=new Element();page.append(fields);
 for(const id of ['status','save','dirty','mode','defaults','reload']){const el=new Element('button');el.id=id;page.append(el);}
 const document={body:new Element(),querySelector:q=>page.querySelector(q),querySelectorAll:()=>[],createElement:t=>new Element(t)};
 const values=Object.fromEntries(Object.entries(schema).filter(([k])=>!['config_revision','whitelist_revision','umo_whitelist'].includes(k)).map(([k,s])=>[k,structuredClone(s.default)]));
 values.speech_rate=1.5;values.voice_persona='自定义人设';values.tts_mode='平台已配置的语音模型';
 const state={schema:Object.fromEntries(Object.entries(schema).filter(([k])=>k in values)),values,configured:{api_key:true,analysis_api_key:true,tts_headers:true,analysis_headers:true},revision:'test'};
 const posts=[];
 const bridge={ready:async()=>({isDark:false}),onContext(){},apiGet:async()=>structuredClone(state),apiPost:async(_,body)=>{posts.push(body);return structuredClone(state);}};
 const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
 await new AsyncFunction('window','document',fs.readFileSync(path.join(root,'views/settings/app.js'),'utf8'))({AstrBotPluginView:bridge},document);
 assert.equal(document.querySelector('#voice').attributes['aria-required'],'true');
 const requiredMark=key=>fields.querySelectorAll('[data-key]').find(x=>x.dataset.key===key).querySelector('.required');
 assert.equal(requiredMark('voice').textContent,'（必填）');
 assert.equal(requiredMark('speech_rate').textContent,'（已填）');
 const voice=document.querySelector('#voice');voice.value='voice-id';voice.oninput();
 assert.equal(requiredMark('voice').textContent,'（已填）');
 assert.equal(requiredMark('voice').className,'required filled');
 voice.value='   ';voice.oninput();assert.equal(requiredMark('voice').textContent,'（必填）');
 voice.value='';voice.oninput();
 const count=document.querySelector('#context_messages');count.value='0';count.oninput();
 assert.equal(requiredMark('context_messages').textContent,'（已填）','zero is a filled value');
 count.value='';count.oninput();assert.equal(requiredMark('context_messages').textContent,'（必填）');

 assert.equal(fields.querySelectorAll('[data-key]').find(x=>x.dataset.key==='voice').hidden,true);
 const key=document.querySelector('#api_key');key.value='new-secret';key.oninput();
 document.querySelector('#defaults').onclick();
 assert.equal(posts.length,0,'reset must not save automatically');
 assert.equal(document.querySelector('#speech_rate').value,'1.12');
 assert.equal(document.querySelector('#voice_persona').value,schema.voice_persona.default);
 assert.equal(document.querySelector('#api_key').value,'new-secret','pending credential edit preserved');
 assert.equal(fields.querySelectorAll('[data-key]').find(x=>x.dataset.key==='voice').hidden,false);
 await document.querySelector('#save').onclick();
 assert.equal(posts.length,1);assert.equal(posts[0].changes.api_key,'new-secret');
 assert.equal(posts[0].changes.voice_persona,schema.voice_persona.default);
 assert(!('tts_headers' in posts[0].changes));assert.deepEqual(posts[0].clear_secrets,[]);
 document.querySelector('#defaults').onclick();
 document.querySelector('#reload').onclick(); // first click arms discard
 await document.querySelector('#reload').onclick();
 assert.equal(document.querySelector('#voice_persona').value,'自定义人设');
 assert.equal(document.querySelector('#speech_rate').value,'1.5');
 console.log('UI behavior passed: default preview/save/undo, credential preservation, conditional required fields.');
})().catch(e=>{console.error(e);process.exitCode=1;});
