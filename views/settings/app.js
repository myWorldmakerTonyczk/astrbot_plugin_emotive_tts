const bridge = window.AstrBotPluginView;
const form = document.querySelector('#settings');
const status = document.querySelector('#status');
const save = document.querySelector('#save');
const secrets = new Set(['api_key','analysis_api_key','tts_headers','analysis_headers','tts_body_template']);
const groups = {
 voice: ['连接与音色（目前仅保证阿里云 CosyVoice 模型，其他模型不保证）','选择匹配的模型和音色，设置连接凭据。',['tts_mode','tts_provider_id','api_key','aliyun_region','aliyun_workspace','tts_endpoint','tts_headers','tts_auto_auth','tts_body_template','tts_response_mode','tts_audio_path','tts_model','voice','speech_rate','tts_seed']],
 emotion: ['语气与上下文','让语音结合对话内容，使用自然的情绪和停顿。',['auto_emotion','context_messages','voice_persona','fallback_instruction','analysis_mode','analysis_provider_id','analysis_endpoint','analysis_model','analysis_api_key','analysis_headers']],
 sending: ['发送与会话','控制自动语音、工具调用和可用的聊天范围。',['enabled','auto_reply_voice','tool_enabled','private_enabled','group_enabled','private_umo_whitelist','group_umo_whitelist','trigger_probability','send_text_with_voice','max_text_chars']],
 advanced: ['高级设置','调整任务容量、超时和临时文件保留时间。',['analysis_debug','context_max_chars','include_persona','analysis_timeout','tts_timeout','job_timeout','concurrency','queue_capacity','audio_ttl_seconds']]
};
let state, active='voice', busy=false;
const dirty=new Map(), cleared=new Set();
window.emotiveSeedSettings={hasUnsaved:()=>busy||!state||dirty.size>0||cleared.size>0,revision:()=>state?.revision,reload:()=>load()};
function notify(text, kind=''){status.textContent=text;status.className=kind;}
function update(){document.querySelector('#dirty').textContent=dirty.size||cleared.size?'有未保存的修改':'没有未保存的修改';save.disabled=busy||!state||(!dirty.size&&!cleared.size);document.querySelector('#defaults').disabled=busy||!state;}
function conditions(){document.querySelector('#mode').textContent=dirty.has('tts_mode')?dirty.get('tts_mode'):state.values.tts_mode;for(const node of form.querySelectorAll('[data-key]')){const condition=state.schema[node.dataset.key].condition||{};node.hidden=!Object.entries(condition).every(([k,v])=>(Array.isArray(v)?v.includes(dirty.has(k)?dirty.get(k):state.values[k]):(dirty.has(k)?dirty.get(k):state.values[k])===v));
 const mark=node.querySelector('.required');
 if(mark){const meta=state.schema[node.dataset.key];mark.hidden=!Object.entries(meta.required_condition||{}).every(([k,v])=>(dirty.has(k)?dirty.get(k):state.values[k])===v);const input=document.querySelector('#'+node.dataset.key);const value=input.value.trim();const filled=value!==''&&(!['int','float'].includes(meta.type)||Number.isFinite(Number(value)));mark.textContent=filled?'（已填）':'（必填）';mark.className=filled?'required filled':'required';}
}}
function render(){form.replaceChildren();document.querySelector('#mode').textContent=state.values.tts_mode;
 for(const [id,[title,desc,keys]] of Object.entries(groups)){
 const section=document.createElement('section');section.dataset.section=id;section.hidden=id!==active;
 const heading=document.createElement('h2');heading.textContent=title;section.append(heading);
 const intro=document.createElement('p');intro.textContent=desc;section.append(intro);
 const fields=document.createElement('div');fields.className='fields';section.append(fields);
 for(const key of keys){const meta=state.schema[key];if(!meta)continue;
 const row=document.createElement('div');row.dataset.key=key;row.className='field';
 const label=document.createElement('label');label.htmlFor=key;label.textContent=meta.description;if(meta.required){const mark=document.createElement('span');mark.className='required';mark.textContent='（必填）';label.append(mark);}
 const input=document.createElement(meta.options?'select':meta.type==='text'||meta.type==='list'?'textarea':'input');input.id=key;input.name=key;if(meta.required)input.setAttribute('aria-required','true');
 if(meta.options)for(const value of meta.options){const opt=document.createElement('option');opt.value=value;opt.textContent=meta.labels?.[meta.options.indexOf(value)]??value;input.append(opt);}
 else if(input.tagName==='INPUT')input.type=meta.type==='bool'?'checkbox':secrets.has(key)?'password':['int','float'].includes(meta.type)?'number':'text';
 if(meta.slider){input.type='range';input.min=meta.slider.min;input.max=meta.slider.max;input.step=meta.slider.step;}
 if(input.type==='number')input.step=meta.type==='int'?'1':'any';
 const value=dirty.has(key)?dirty.get(key):state.values[key];if(meta.type==='bool')input.checked=value;else input.value=meta.type==='list'?value.join('\n'):value??'';
 if(meta.slider){const valueLabel=document.createElement('output');valueLabel.htmlFor=key;valueLabel.textContent=Number(input.value).toFixed(2)+'（'+Math.round(Number(input.value)*100)+'%）';label.append(' ',valueLabel);input.addEventListener('input',()=>{valueLabel.textContent=Number(input.value).toFixed(2)+'（'+Math.round(Number(input.value)*100)+'%）';});}
 if(secrets.has(key)){input.autocomplete='new-password';input.placeholder=state.configured[key]?'已保存，留空保留原值':'尚未设置';}
 if(meta.type==='bool'){row.classList.add('switch','wide');row.append(label,input);}else row.append(label,input);
 const hint=document.createElement('span');hint.id=key+'-hint';hint.className='hint';hint.textContent=meta.hint||'';input.setAttribute('aria-describedby',hint.id);if(meta.type==='bool')label.append(hint);else row.append(hint);
 if(meta.type==='text'||meta.type==='list'||key.endsWith('endpoint'))row.classList.add('wide');
 input.addEventListener('input',()=>{let val=meta.type==='bool'?input.checked:meta.type==='list'?input.value.split('\n').map(x=>x.trim()).filter(Boolean):['int','float'].includes(meta.type)?(input.value===''?null:Number(input.value)):input.value;
 if(secrets.has(key)){cleared.delete(key);const clear=row.querySelector('.clear');if(clear)clear.textContent='清除已存凭据';}
 if(JSON.stringify(val)===JSON.stringify(state.values[key]))dirty.delete(key);else dirty.set(key,val);conditions();update();});
 if(secrets.has(key)&&state.configured[key]){const button=document.createElement('button');button.type='button';button.className='clear';button.textContent=cleared.has(key)?'撤销清除':'清除已存凭据';button.onclick=()=>{if(cleared.has(key)){cleared.delete(key);button.textContent='清除已存凭据';}else{cleared.add(key);dirty.delete(key);input.value='';button.textContent='撤销清除';}update();};row.append(button);}
 fields.append(row);if(['tts_headers','tts_body_template'].includes(key)&&window.EmotiveTemplateEditor)window.EmotiveTemplateEditor(input,key);
 if(key==='tts_audio_path'){
 const testRow=document.createElement('div');testRow.className='field wide';testRow.dataset.key='tts_response_mode';
 const test=document.createElement('button');test.type='button';test.textContent='测试当前已保存的接口（生成1句语音）';
 const result=document.createElement('p');result.className='hint';result.textContent='使用当前语气分析和语音配置，会调用API；不发送聊天，不保存会话记忆。';
 const audio=document.createElement('audio');audio.controls=true;audio.hidden=true;let audioUrl='';
 test.onclick=async()=>{if(dirty.size||cleared.size){notify('请先保存或撤销修改，再测试。','error');return;}test.disabled=true;result.textContent='正在生成测试语音…';
 try{const response=await bridge.apiPost('settings/test-request',{revision:state.revision});if(audioUrl)URL.revokeObjectURL(audioUrl);const bytes=Uint8Array.from(atob(response.audio_base64),c=>c.charCodeAt(0));audioUrl=URL.createObjectURL(new Blob([bytes],{type:response.mime_type}));audio.src=audioUrl;audio.hidden=false;result.textContent='成功取得 '+response.format.toUpperCase()+' 音频。正文：'+response.text+'；instruction：'+response.instruction+(response.source==='fallback'?'（分析失败，使用默认语气）':'');}
 catch(error){result.textContent=error.message||'测试失败，请检查模板和音频获取设置。';}finally{test.disabled=false;}};
 testRow.append(test,result,audio);fields.append(testRow);}
 }
 form.append(section);}
 conditions();update();}
for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>{active=button.dataset.tab;for(const b of document.querySelectorAll('[data-tab]')){if(b===button)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}for(const section of form.children)section.hidden=section.dataset.section!==active;form.hidden=active==='seeds';const preview=document.querySelector('#seed-preview');if(preview)preview.hidden=active!=='seeds';const footer=document.querySelector('footer');if(footer)footer.hidden=active==='seeds';};
async function load(){busy=true;update();try{state=await bridge.apiGet('settings');dirty.clear();cleared.clear();render();notify('设置已加载，所有选项均可在此调整。');}catch(e){notify(e.message||'读取失败，请重新打开视图。','error');}finally{busy=false;update();}}
document.querySelector('#defaults').onclick=()=>{
 if(!state||busy)return;
 for(const [key,meta] of Object.entries(state.schema)){
  if(secrets.has(key))continue;
  const value=structuredClone(meta.default);
  if(JSON.stringify(value)===JSON.stringify(state.values[key]))dirty.delete(key);else dirty.set(key,value);
 }
 render();notify('已恢复默认选项（包括语音人设、连接选择及会话范围），API Key和请求头保持当前值。点击“保存设置”后生效；“重新加载”可撤销。');
};
let reloadArmed=false;
document.querySelector('#reload').onclick=()=>{if((dirty.size||cleared.size)&&!reloadArmed){reloadArmed=true;document.querySelector('#reload').textContent='确认放弃修改';notify('再次点击将放弃未保存的修改。');return;}reloadArmed=false;document.querySelector('#reload').textContent='重新加载';load();};
save.onclick=async()=>{if(form.checkValidity&&!form.checkValidity()){notify('请先修正请求模板中的无效字段。','error');return;}busy=true;update();try{const changes=Object.fromEntries(dirty);state=await bridge.apiPost('settings/save',{revision:state.revision,changes,clear_secrets:[...cleared]});dirty.clear();cleared.clear();render();notify('设置已保存。','success');}catch(e){notify(e.message||'保存失败，修改仍保留在页面中。','error');}finally{busy=false;update();}};
form.onsubmit=e=>e.preventDefault();
if(!bridge){notify('请在 AstrBot 插件详情的“语音设置”视图中打开此页面。','error');}else{try{const ctx=await bridge.ready();document.body.classList.toggle('dark',!!ctx.isDark);bridge.onContext(ctx=>document.body.classList.toggle('dark',!!ctx.isDark));await load();}catch(e){notify('无法连接 AstrBot，请重新打开视图。','error');}}
