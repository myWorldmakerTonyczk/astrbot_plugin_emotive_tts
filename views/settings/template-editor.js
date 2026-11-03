// Both editor modes write the same JSON. Nested keys use input.text / items.0.text.
window.EmotiveTemplateEditor = function(input, key) {
  const headers=key==='tts_headers';
  const variables={text:'正文',instruction:'语气 instruction',model:'模型',voice:'音色',speech_rate:'语速',seed:'种子',api_key:'API Key'};
  const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const wrapper=make('div');wrapper.className='template-editor';
  const toolbar=make('div');toolbar.className='template-toolbar';
  const mode=make('select');mode.setAttribute('aria-label','请求编辑模式');
  for(const [value,text] of [['json','JSON粘贴模式'],['form','逐项填写模式']]){const o=make('option',text);o.value=value;mode.append(o);}
  const panel=make('div');const note=make('p');note.className='hint';
  note.textContent='变量：<text>、<instruction>、<model>、<voice>、<speech_rate>、<seed>、<api_key>。JSON模式的变量放在双引号中；语速和种子整值替换为数字。';
  toolbar.append(mode);wrapper.append(toolbar,panel,note);let rows=[];let containers=new Map();
  const emit=()=>input.dispatchEvent(new Event('input',{bubbles:true}));
  function flatten(obj,prefix=''){
    const out=[];
    for(const [k,v] of Object.entries(obj)){
      if(k.includes('.')||['__proto__','constructor','prototype'].includes(k))throw Error('字段名包含点或保留字，请使用JSON模式。');
      const path=prefix?prefix+'.'+k:k;
      if(v&&typeof v==='object'&&Object.keys(v).length)out.push(...flatten(v,path));else out.push([path,v]);
    }return out;
  }
  function commit(){
    const body={};const seen=new Set();
    try{
      for(const row of rows){
        const name=row.name.value.trim();if(!name)continue;
        if(seen.has(name))throw Error('字段名不能重复。');seen.add(name);
        const path=headers?[name]:name.split('.');
        if(path.some(p=>!p||['__proto__','constructor','prototype'].includes(p)))throw Error('字段路径无效。');
        let value=row.value.value;
        if(row.kind.value.startsWith('var:'))value='<'+row.kind.value.slice(4)+'>';
        else if(row.kind.value==='json')value=JSON.parse(value);
        if(headers&&typeof value!=='string')throw Error('请求头的值必须是文本。');
        let node=body;
        for(let i=0;i<path.length-1;i++){
          const p=path[i];if(node[p]!==undefined&&(typeof node[p]!=='object'||node[p]===null))throw Error('字段路径发生冲突。');
          if(node[p]===undefined){const stored=containers.get(path.slice(0,i+1).join('.'));node[p]=stored==='array'||(!stored&&/^\d+$/.test(path[i+1]))?[]:{};}node=node[p];
        }
        const last=path[path.length-1];if(node[last]!==undefined)throw Error('字段路径发生冲突。');node[last]=value;
      }
      input.value=JSON.stringify(body,null,2);input.setCustomValidity('');note.textContent='已同步为JSON；嵌套字段填写 input.text，数组填写 items.0.text。切换模式不会丢失内容。';emit();
    }catch(error){input.setCustomValidity(error.message);note.textContent=error.message;emit();}
  }
  function add(name='',value=''){
    const row=make('div');row.className='template-row';
    const nameInput=make('input');nameInput.placeholder=headers?'Header名称':'字段名，例如 input.text';nameInput.value=name;
    const kind=make('select');kind.setAttribute('aria-label','值的来源');
    for(const [v,t] of [['text','自定义文本'],...(!headers?[['json','数字 / 布尔 / 对象 / 数组']]:[]),...Object.entries(variables).map(([v,t])=>['var:'+v,t])]){const o=make('option',t);o.value=v;kind.append(o);}
    const valueInput=make('input');valueInput.placeholder='自定义值';
    const match=typeof value==='string'&&value.match(/^<([a-z_]+)>$/);
    kind.value=match&&variables[match[1]]?'var:'+match[1]:typeof value==='string'?'text':'json';
    valueInput.value=typeof value==='string'?value:JSON.stringify(value);
    const sync=()=>{valueInput.hidden=kind.value.startsWith('var:');};sync();
    const entry={name:nameInput,kind,value:valueInput};rows.push(entry);
    const remove=make('button','删除');remove.type='button';remove.onclick=()=>{rows=rows.filter(r=>r!==entry);row.remove();commit();};
    for(const n of [nameInput,valueInput])n.addEventListener('input',commit);
    kind.onchange=()=>{sync();commit();};row.append(nameInput,kind,valueInput,remove);panel.append(row);
  }
  mode.onchange=()=>{
    if(mode.value==='form'){
      try{
        const obj=JSON.parse(input.value||'{}');if(!obj||Array.isArray(obj)||typeof obj!=='object')throw Error('请粘贴JSON对象。');
        containers=new Map();const remember=(value,path='')=>{if(value&&typeof value==='object'){containers.set(path,Array.isArray(value)?'array':'object');for(const [k,v] of Object.entries(value))remember(v,path?path+'.'+k:k);}};remember(obj);
        const entries=headers?Object.entries(obj):flatten(obj);panel.replaceChildren();rows=[];for(const [k,v] of entries)add(k,v);
        const plus=make('button','＋ 新增一对');plus.type='button';plus.onclick=()=>{plus.remove();add();panel.append(plus);};panel.append(plus);input.hidden=true;
      }catch(error){mode.value='json';input.hidden=false;note.textContent=error.message;}
    }else if(!input.checkValidity()){mode.value='form';note.textContent='请先修正无效字段，再切换模式。';}else{panel.replaceChildren();input.hidden=false;}
  };
  input.insertAdjacentElement('afterend',wrapper);
};
