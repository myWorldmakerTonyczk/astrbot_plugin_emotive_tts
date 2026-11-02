const bridge = window.AstrBotPluginView;
const form = document.querySelector('#settings');
const status = document.querySelector('#status');
const save = document.querySelector('#save');
const secrets = new Set(['api_key','analysis_api_key','tts_headers','analysis_headers']);
const groups = {
 voice: ['连接与音色','选择匹配的模型和音色，设置连接凭据。',['api_key','aliyun_region','aliyun_workspace','tts_endpoint','tts_headers','tts_model','voice','speech_rate']],
 emotion: ['语气与上下文','让语音结合对话内容，使用自然的情绪和停顿。',['auto_emotion','context_messages','voice_style','fallback_instruction','analysis_mode','analysis_provider_id','analysis_endpoint','analysis_model','analysis_api_key','analysis_headers']],
 sending: ['发送与会话','控制自动语音、工具调用和可用的聊天范围。',['enabled','auto_reply_voice','tool_enabled','private_enabled','group_enabled','umo_whitelist','trigger_probability','send_text_with_voice','max_text_chars']],
 advanced: ['高级设置','调整任务容量、超时和临时文件保留时间。',['context_max_chars','include_persona','analysis_timeout','tts_timeout','job_timeout','concurrency','queue_capacity','audio_ttl_seconds']]
};
let state, active='voice', busy=false;
const dirty=new Map(), cleared=new Set();
function notify(text, kind=''){status.textContent=text;status.className=kind;}
function update(){document.querySelector('#dirty').textContent=dirty.size||cleared.size?'有未保存的修改':'没有未保存的修改';save.disabled=busy||!state||(!dirty.size&&!cleared.size);}
function conditions(){for(const node of form.querySelectorAll('[data-key]')){const condition=state.schema[node.dataset.key].condition||{};node.hidden=!Object.entries(condition).every(([k,v])=>(dirty.has(k)?dirty.get(k):state.values[k])===v);}}
function render(){form.replaceChildren();document.querySelector('#mode').textContent=state.values.tts_mode;
 for(const [id,[title,desc,keys]] of Object.entries(groups)){
 const section=document.createElement('section');section.dataset.section=id;section.hidden=id!==active;
 const heading=document.createElement('h2');heading.textContent=title;section.append(heading);
 const intro=document.createElement('p');intro.textContent=desc;section.append(intro);
 const fields=document.createElement('div');fields.className='fields';section.append(fields);
 for(const key of keys){const meta=state.schema[key];if(!meta)continue;
 const row=document.createElement('div');row.dataset.key=key;row.className='field';
 const label=document.createElement('label');label.htmlFor=key;label.textContent=meta.description;
 const input=document.createElement(meta.options?'select':meta.type==='text'||meta.type==='list'?'textarea':'input');input.id=key;input.name=key;
 if(meta.options)for(const value of meta.options){const opt=document.createElement('option');opt.value=value;opt.textContent=value;input.append(opt);}
 else if(input.tagName==='INPUT')input.type=meta.type==='bool'?'checkbox':secrets.has(key)?'password':['int','float'].includes(meta.type)?'number':'text';
 if(input.type==='number')input.step=meta.type==='int'?'1':'any';
 const value=state.values[key];if(meta.type==='bool')input.checked=value;else input.value=meta.type==='list'?value.join('\n'):value??'';
 if(secrets.has(key)){input.autocomplete='new-password';input.placeholder=state.configured[key]?'已保存，留空保留原值':'尚未设置';}
 if(meta.type==='bool'){row.classList.add('switch','wide');row.append(label,input);}else row.append(label,input);
 const hint=document.createElement('span');hint.id=key+'-hint';hint.className='hint';hint.textContent=meta.hint||'';input.setAttribute('aria-describedby',hint.id);if(meta.type==='bool')label.append(hint);else row.append(hint);
 if(meta.type==='text'||meta.type==='list'||key.endsWith('endpoint'))row.classList.add('wide');
 input.addEventListener('input',()=>{let val=meta.type==='bool'?input.checked:meta.type==='list'?input.value.split('\n').map(x=>x.trim()).filter(Boolean):['int','float'].includes(meta.type)?(input.value===''?null:Number(input.value)):input.value;
 if(secrets.has(key)){cleared.delete(key);const clear=row.querySelector('.clear');if(clear)clear.textContent='清除已存凭据';}
 if(JSON.stringify(val)===JSON.stringify(state.values[key]))dirty.delete(key);else dirty.set(key,val);conditions();update();});
 if(secrets.has(key)&&state.configured[key]){const button=document.createElement('button');button.type='button';button.className='clear';button.textContent='清除已存凭据';button.onclick=()=>{if(cleared.has(key)){cleared.delete(key);button.textContent='清除已存凭据';}else{cleared.add(key);dirty.delete(key);input.value='';button.textContent='撤销清除';}update();};row.append(button);}
 fields.append(row);}
 form.append(section);}
 conditions();update();}
for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>{active=button.dataset.tab;for(const b of document.querySelectorAll('[data-tab]')){if(b===button)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}for(const section of form.children)section.hidden=section.dataset.section!==active;};
async function load(){busy=true;update();try{state=await bridge.apiGet('settings');dirty.clear();cleared.clear();render();notify('设置已加载。API 类型在插件配置中选择。');}catch(e){notify(e.message||'读取失败，请重新打开视图。','error');}finally{busy=false;update();}}
let reloadArmed=false;
document.querySelector('#reload').onclick=()=>{if((dirty.size||cleared.size)&&!reloadArmed){reloadArmed=true;document.querySelector('#reload').textContent='确认放弃修改';notify('再次点击将放弃未保存的修改。');return;}reloadArmed=false;document.querySelector('#reload').textContent='重新加载';load();};
save.onclick=async()=>{busy=true;update();try{const changes=Object.fromEntries(dirty);state=await bridge.apiPost('settings/save',{revision:state.revision,changes,clear_secrets:[...cleared]});dirty.clear();cleared.clear();render();notify('设置已保存。','success');}catch(e){notify(e.message||'保存失败，修改仍保留在页面中。','error');}finally{busy=false;update();}};
form.onsubmit=e=>e.preventDefault();
if(!bridge){notify('请在 AstrBot 插件详情的“语音设置”视图中打开此页面。','error');}else{try{const ctx=await bridge.ready();document.body.classList.toggle('dark',!!ctx.isDark);bridge.onContext(ctx=>document.body.classList.toggle('dark',!!ctx.isDark));await load();}catch(e){notify('无法连接 AstrBot，请重新打开视图。','error');}}
