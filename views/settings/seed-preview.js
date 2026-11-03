// A separate controller keeps comparison playback alive while progress updates.
const bridge = window.AstrBotPluginView;
const panel = document.querySelector('#seed-preview');
const statusNames = {running:'生成中',complete:'全部完成',partial:'部分失败',failed:'生成失败',cancelled:'已停止',interrupted:'重载中断'};
const sampleNames = {pending:'等待生成',running:'正在生成',ok:'可试听',failed:'生成失败',cancelled:'已停止',interrupted:'未完成'};

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

export class SeedPreview {
  constructor(container, api) {
    this.panel=container; this.api=api; this.data=null; this.current=''; this.rendered='';
    this.rows=new Map(); this.urls=new Map(); this.seedButtons=new Map(); this.selected=null;
    this.pending=false; this.timer=null; this.deleteArmed='';this.sceneBlocks=new Map();this.sceneTabs=new Map();this.activeScene='';
    this.note=el('p','正在读取历史试听…','seed-note');
    const title=el('h2','挑一个更自然的声音');title.className='seed-title';
    const intro=el('p','先生成一组声音，再切换不同语境试听，最后选出你最喜欢的候选。');
    const detail=el('p','使用已保存的设置，试听不会发到聊天或写入会话记忆。默认3个种子、最多5次语气分析和15次语音合成，会使用你配置的API额度。已有固定种子会作为其中一个候选。结果保存在服务器，确认种子后也继续保留。','hint');
    const tools=el('div',undefined,'seed-tools');
    this.count=el('select'); this.count.setAttribute('aria-label','本批种子数量');
    for(const n of [2,3,4]) {const opt=el('option',`${n}个种子 · ${n*5}份语音`);opt.value=String(n);this.count.append(opt);}
    this.count.value='3';this.count.setAttribute('title','留空指定种子时，按此数量随机生成');
    this.start=el('button','生成新的声音对比');this.start.className='seed-primary'; this.start.type='button';this.start.onclick=()=>this.generate();
    this.refresh=el('button','刷新记录');this.refresh.type='button';this.refresh.onclick=()=>this.load();
    this.manual=el('input');this.manual.type='text';this.manual.id='seed-manual';this.manual.placeholder='例如：37, 1286, 52803';this.manual.maxLength=100;const manualLabel=el('label','指定种子（选填）');manualLabel.htmlFor='seed-manual';const manualBox=el('div',undefined,'seed-manual');manualBox.append(manualLabel,this.manual,el('p','输入1～4个种子，用逗号或空格分隔；填写后只生成指定种子，留空则随机生成。','hint'));tools.append(this.count,this.start,this.refresh);this.manualBox=manualBox;this.manual.oninput=()=>{this.count.disabled=!!this.manual.value.trim();this.start.textContent=this.manual.value.trim()?'试听指定种子':'生成随机声音对比';};
    this.history=el('select');this.history.setAttribute('aria-label','已保存的对比批次');this.history.onchange=()=>{this.current=this.history.value;this.renderBatch();};
    this.body=el('div');
    const help=el('details',undefined,'seed-help');help.append(el('summary','生成规则与API用量'),detail);const steps=el('p','1 生成对比　 →　 2 切换语境试听　 →　 3 确认候选','seed-steps');const historyLabel=el('label','已保存的声音对比','seed-history-label');historyLabel.htmlFor='seed-history';this.history.id='seed-history';container.append(title,intro,steps,this.manualBox,tools,help,this.note,historyLabel,this.history,this.body);
    this.note.setAttribute('role','status');this.note.setAttribute('aria-live','polite');
    window.addEventListener('pagehide',()=>{clearTimeout(this.timer);this.release();});
  }

  notify(message,error=false){this.note.textContent=message;this.note.className=error?'seed-note error':'seed-note';}
  hooks(){return window.emotiveSeedSettings;}
  cleanSettings(){
    if(!this.hooks() || this.hooks().hasUnsaved()) {
      this.notify('请先保存或撤销设置页中的修改，再生成试听或确认种子。',true);return false;
    }
    return true;
  }
  revision(){return this.hooks()?.revision() || this.data?.revision;}
  async load(){
    try{this.receive(await this.api.apiGet('settings/seed'));}
    catch(error){this.notify(error.message || '读取试听记录失败，请重试。',true);this.schedule();}
  }
  receive(data){
    this.data=data;
    const previous=this.current;
    this.history.replaceChildren();
    for(const batch of data.batches){
      const opt=el('option',`${new Date(batch.created_at*1000).toLocaleString()} · ${batch.seeds.join(' / ')} · ${statusNames[batch.status]||batch.status}`);
      opt.value=batch.id;this.history.append(opt);
    }
    this.current=data.batches.some(b=>b.id===previous)?previous:(data.batches[0]?.id||'');
    this.history.value=this.current; this.history.hidden=!data.batches.length;
    this.start.disabled=this.pending || data.batches.some(b=>b.status==='running');
    this.renderBatch();this.schedule();
  }
  schedule(){
    clearTimeout(this.timer);
    if(this.data?.batches.some(b=>b.status==='running')) this.timer=setTimeout(()=>this.load(),2500);
  }
  release(){for(const url of this.urls.values())URL.revokeObjectURL(url);this.urls.clear();}
  parseSeeds(){
    const raw=this.manual.value.trim();if(!raw)return null;
    const parts=raw.split(/[,，\s]+/).filter(Boolean);
    if(parts.length<1||parts.length>4||parts.some(p=>!/^\d+$/.test(p)))throw new Error('请输入1～4个整数种子，用逗号或空格分隔。');
    const seeds=parts.map(Number);
    if(seeds.some(n=>!Number.isInteger(n)||n<0||n>65535)||new Set(seeds).size!==seeds.length)throw new Error('种子范围为0～65535，且不能重复。');
    return seeds;
  }
  async generate(){
    if(this.pending || !this.cleanSettings())return;
    let seeds;try{seeds=this.parseSeeds();}catch(error){this.notify(error.message,true);return;}
    this.pending=true;this.start.disabled=true;this.notify('正在创建试听批次…');
    try{
      const data=await this.api.apiPost('settings/seed/start',{count:Number(this.count.value),revision:this.revision(),...(seeds?{seeds}: {})});
      this.current=data.batches[0]?.id||'';this.receive(data);
    }catch(error){this.notify(error.message||'创建失败。',true);}
    finally{this.pending=false;this.start.disabled=!!this.data?.batches.some(b=>b.status==='running');}
  }
  storedSelection(){
    try{const value=localStorage.getItem('emotive-seed-choice:'+this.current);return value===null?null:Number(value);}catch{return null;}
  }
  select(seed){
    this.selected=seed;
    try{localStorage.setItem('emotive-seed-choice:'+this.current,String(seed));}catch{/* Selection still works without storage. */}
    this.updateSelection();
  }
  updateSelection(){
    const batch=this.data.batches.find(b=>b.id===this.current);
    for(const [seed,node] of this.seedButtons){node.setAttribute('aria-pressed',String(seed===this.selected));}for(const sample of batch?.samples||[]){const row=this.rows.get(sample.id);if(row){row.card.setAttribute('data-selected',String(sample.seed===this.selected));row.pick.textContent=sample.seed===this.selected?'✓ 已选中':'选择候选 '+this.candidate(sample.seed);row.pick.setAttribute('aria-pressed',String(sample.seed===this.selected));}}
    const eligible=batch?.seeds.includes(this.selected) && batch.samples.filter(s=>s.seed===this.selected).every(s=>s.status==='ok');
    this.apply.disabled=!eligible || this.pending;
    this.apply.textContent=this.selected===null?'请先选择一个候选':`确认使用候选 ${this.candidate(this.selected)}`;
  }
  candidate(seed){const batch=this.data?.batches.find(b=>b.id===this.current);return String.fromCharCode(65+(batch?.seeds.indexOf(seed)??0));}
  showScene(id){this.activeScene=id;for(const [key,block] of this.sceneBlocks)block.hidden=key!==id;for(const [key,tab] of this.sceneTabs)tab.setAttribute('aria-pressed',String(key===id));}
  renderBatch(){
    const batch=this.data?.batches.find(b=>b.id===this.current);
    if(!batch){this.body.replaceChildren();this.notify('还没有试听记录。生成一批后，可以跨语境比较每个种子。');return;}
    if(this.rendered!==batch.id){
      this.rendered=batch.id;this.body.replaceChildren();this.rows.clear();this.seedButtons.clear();this.sceneBlocks.clear();this.sceneTabs.clear();this.release();this.deleteArmed='';
      const stored=this.storedSelection();this.selected=batch.seeds.includes(stored)?stored:null;
      this.progress=el('p',undefined,'seed-progress');this.body.append(this.progress);
      this.summary=el('p',undefined,'hint');this.body.append(this.summary);
      const details=el('details');details.append(el('summary','查看本批配置快照'));
      details.append(el('pre',JSON.stringify(batch.settings,null,2),'seed-json'));this.body.append(details);
      const choices=el('div',undefined,'seed-selection');const choiceHeading=el('p','选定后再确认，其他候选会继续保留。');choices.append(choiceHeading);
      for(const seed of batch.seeds){const button=el('button',`候选 ${this.candidate(seed)} · ${seed}`);button.type='button';button.onclick=()=>this.select(seed);this.seedButtons.set(seed,button);choices.append(button);}
      this.apply=el('button','请先选择一个候选');this.apply.className='seed-primary';this.apply.type='button';this.apply.onclick=()=>this.confirm();choices.append(this.apply);
      const tabs=el('div',undefined,'seed-scene-tabs');tabs.setAttribute('aria-label','切换试听语境');for(const scene of batch.scenes){const tab=el('button',scene.title.split(' · ')[0]);tab.type='button';tab.onclick=()=>this.showScene(scene.id);this.sceneTabs.set(scene.id,tab);tabs.append(tab);}this.body.append(el('h3','切换语境，比较同一句话','seed-compare-heading'),tabs);
      for(const scene of batch.scenes){
        const block=el('div',undefined,'seed-scene');block.append(el('h3',scene.title),el('p',scene.reply,'seed-reply'));
        const context=el('details');context.append(el('summary','查看固定上文与实际指令'));
        for(const msg of scene.contexts)context.append(el('p',`${msg.role==='user'?'对方':'机器人'}：${msg.content}`));
        const instruction=el('p','尚未生成指令','seed-instruction');context.append(instruction);block.append(context);
        const grid=el('div',undefined,'seed-grid');
        for(const sample of batch.samples.filter(s=>s.scene===scene.id)){
          const card=el('article',undefined,'seed-card');card.append(el('h4',`候选 ${this.candidate(sample.seed)}`),el('p',`种子 ${sample.seed}`,'seed-number'));
          const state=el('p','等待生成','hint');
          const button=el('button',`▶ 播放候选 ${this.candidate(sample.seed)}`);button.className='seed-play';button.type='button';button.disabled=true;
          const audio=el('audio');audio.controls=true;audio.preload='none';audio.hidden=true;
          button.onclick=()=>this.play(batch.id,sample.id,button,audio);
          const pick=el('button',`选择候选 ${this.candidate(sample.seed)}`,'seed-pick');pick.type='button';pick.onclick=()=>this.select(sample.seed);card.append(state,button,audio,pick);grid.append(card);this.rows.set(sample.id,{state,button,audio,instruction,card,pick});
        }
        block.append(grid);this.body.append(block);this.sceneBlocks.set(scene.id,block);
      }
      this.showScene(this.sceneBlocks.has(this.activeScene)?this.activeScene:batch.scenes[0].id);this.body.append(choices);const actions=el('div',undefined,'seed-tools seed-record-actions');
      this.stop=el('button','停止生成，保留已完成音频');this.stop.type='button';this.stop.onclick=()=>this.action('cancel');
      this.remove=el('button','删除这批对比');this.remove.type='button';this.remove.onclick=()=>{
        if(this.deleteArmed!==this.current){this.deleteArmed=this.current;this.remove.textContent='再次点击，删除这批音频与记录';return;}
        this.action('delete');
      };
      actions.append(this.stop,this.remove);this.body.append(actions);
    }
    const done=batch.samples.filter(s=>s.status==='ok').length;
    this.progress.textContent=`${statusNames[batch.status]||batch.status} · 已生成 ${done}/${batch.samples.length} 份 · 正式种子：${this.data.selected_seed<0?'服务默认':this.data.selected_seed}`;
    this.summary.textContent=batch.config_revision===this.data.revision?'使用生成时已保存的设置。':'当前配置与本批生成时不同；音频仍按旧配置保留，可查看快照或重新生成。';
    if(batch.error)this.progress.textContent+=' · '+batch.error;
    for(const sample of batch.samples){
      const row=this.rows.get(sample.id);row.state.textContent=sampleNames[sample.status]+(sample.error?'：'+sample.error:'');
      row.button.disabled=sample.status!=='ok';row.pick.disabled=sample.status!=='ok';
      const info=batch.instructions[sample.scene];
      row.instruction.textContent=info?`实际 instruction：${info.instruction}${info.source==='fallback'?'（语气分析失败，使用默认指令）':info.source==='default'?'（语气分析已关闭）':''}`:'尚未生成指令';
    }
    this.stop.hidden=batch.status!=='running';this.remove.disabled=batch.status==='running';this.updateSelection();
    this.notify(batch.status==='running'?'正在生成，可以先听已完成的音频；刷新不会丢失对比。':'对比已保留。选择一个种子后点击确认，才会用于正式语音。');
  }
  async play(batchId,sampleId,button,audio){
    if(this.urls.has(sampleId)){audio.hidden=false;audio.src=this.urls.get(sampleId);await audio.play().catch(()=>{});return;}
    button.disabled=true;button.textContent='正在加载…';
    try{
      const data=await this.api.apiPost('settings/seed/audio',{batch_id:batchId,sample_id:sampleId});
      if(this.current!==batchId)return;
      const bytes=Uint8Array.from(atob(data.audio_base64),c=>c.charCodeAt(0));
      const url=URL.createObjectURL(new Blob([bytes],{type:'audio/wav'}));this.urls.set(sampleId,url);
      audio.src=url;audio.hidden=false;button.textContent='▶ 再听一次';await audio.play().catch(()=>{});
    }catch(error){button.textContent='重试加载';this.notify(error.message||'加载失败。',true);}
    finally{button.disabled=false;}
  }
  async confirm(){
    if(this.pending || this.apply.disabled || !this.cleanSettings())return;
    this.pending=true;this.updateSelection();
    try{
      const data=await this.api.apiPost('settings/seed/apply',{batch_id:this.current,seed:this.selected,revision:this.revision()});
      this.receive(data);await this.hooks()?.reload();this.notify(`种子 ${this.selected} 已用于正式语音，对比音频继续保留。`);
    }catch(error){this.notify(error.message||'保存种子失败。',true);}
    finally{this.pending=false;this.updateSelection();}
  }
  async action(action){
    try{this.receive(await this.api.apiPost('settings/seed/'+action,{batch_id:this.current}));}
    catch(error){this.notify(error.message||'操作失败。',true);}
  }
}

if(panel && bridge){
  const preview=new SeedPreview(panel,bridge);
  try{await bridge.ready();await preview.load();}catch{preview.notify('无法连接AstrBot，请重新打开视图。',true);}
}
