const fs=require('node:fs');
const assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this._value='';this.disabled=false;this.hidden=false;}
 set value(v){this._value=String(v);}get value(){return this._value;}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
 setAttribute(k,v){this.attributes[k]=v;}
 play(){return Promise.resolve();}
}
(async()=>{
 const source=fs.readFileSync(process.argv[2]+'/views/settings/seed-preview.js','utf8').replace('export class SeedPreview','class SeedPreview');
 const storage=new Map(),posts=[];
 let dirty=false,reloads=0;
 const window={addEventListener(){},emotiveSeedSettings:{hasUnsaved:()=>dirty,revision:()=>'rev',reload:async()=>{reloads++;}}};
 const document={querySelector:()=>null,createElement:t=>new Element(t)};
 const localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)};
 const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
 const SeedPreview=await new AsyncFunction('window','document','localStorage','setTimeout','clearTimeout',source+'\nreturn SeedPreview;')(window,document,localStorage,()=>1,()=>{});
 const batch={id:'a'.repeat(32),created_at:1,status:'complete',config_revision:'rev',seeds:[0,37],settings:{voice_persona:'女孩声'},scenes:[{id:'happy',title:'开心',reply:'真开心',contexts:[{role:'user',content:'我成功啦'}]}],instructions:{happy:{instruction:'轻快自然。',source:'llm'}},samples:[{id:'happy_0',scene:'happy',seed:0,status:'ok'},{id:'happy_37',scene:'happy',seed:37,status:'ok'}]};
 const data={batches:[batch],revision:'rev',selected_seed:-1};
 const api={apiGet:async()=>structuredClone(data),apiPost:async(path,body)=>{posts.push({path,body});return path.endsWith('/audio')?{audio_base64:Buffer.from('wav').toString('base64')}:structuredClone(data);}};
 const preview=new SeedPreview(new Element(),api);
 preview.manual.value='0，37 65535';assert.deepEqual(preview.parseSeeds(),[0,37,65535]);
 for(const value of ['1,1','-1','65536','a','1 2 3 4 5']){preview.manual.value=value;assert.throws(()=>preview.parseSeeds());}
 preview.manual.value='';assert.equal(preview.parseSeeds(),null);

 await preview.load();assert.equal(preview.selected,null,'seed zero is not silently preselected');
 assert.equal(preview.apply.disabled,true);
 preview.select(37);assert.equal(preview.apply.disabled,false);assert.equal(posts.length,0);assert.equal(preview.rows.get('happy_37').card.attributes['data-selected'],'true');assert.equal(preview.rows.get('happy_0').card.attributes['data-selected'],'false');preview.showScene('happy');assert.equal(preview.sceneBlocks.get('happy').hidden,false);assert.equal(preview.sceneTabs.get('happy').attributes['aria-pressed'],'true');
 const row=preview.rows.get('happy_37');await row.button.onclick();
 const player=row.audio,src=player.src;
 preview.receive(structuredClone(data));assert.equal(preview.rows.get('happy_37').audio,player);assert.equal(player.src,src,'progress refresh retains player and URL');
 dirty=true;await preview.confirm();assert.equal(posts.filter(p=>p.path.endsWith('/apply')).length,0);
 dirty=false;await preview.confirm();assert.equal(reloads,1);assert.equal(posts.at(-1).body.seed,37);
 assert.equal(posts.at(-1).path,'settings/seed/apply');
 const reloaded=new SeedPreview(new Element(),api);await reloaded.load();assert.equal(reloaded.selected,37);
 batch.samples[1].status='pending';batch.status='running';preview.receive(structuredClone(data));assert.equal(preview.apply.disabled,true);
 preview.release();reloaded.release();
 console.log('Seed UI passed: explicit choice, preserved playback, dirty-settings guard, confirmed apply, restored choice and incomplete-seed guard.');
})().catch(e=>{console.error(e);process.exitCode=1;});
