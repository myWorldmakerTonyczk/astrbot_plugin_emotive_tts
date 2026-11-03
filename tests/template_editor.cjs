const fs=require('node:fs'),assert=require('node:assert/strict');
class Node{
 constructor(tag){this.tagName=tag;this.children=[];this.value='';this.listeners={};this.validity='';}
 append(...nodes){for(const n of nodes){n.remove?.();n.parent=this;this.children.push(n);}}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 setAttribute(){} addEventListener(k,f){this.listeners[k]=f;}dispatchEvent(e){this.listeners[e.type]?.();}
 insertAdjacentElement(_,node){this.editor=node;}
 setCustomValidity(value){this.validity=value;}checkValidity(){return !this.validity;}
}
const window={},document={createElement:tag=>new Node(tag)};
new Function('window','document',fs.readFileSync(process.argv[2]+'/views/settings/template-editor.js','utf8'))(window,document);
const input=new Node('textarea');input.value='{"input":{"text":"<text>","rate":"<speech_rate>"},"stream":false}';
window.EmotiveTemplateEditor(input,'tts_body_template');
const [toolbar,panel,note]=input.editor.children,mode=toolbar.children[0];
mode.value='form';mode.onchange();assert(input.hidden);assert.equal(panel.children.length,4);
const [name,kind,value]=panel.children[2].children;assert.equal(name.value,'stream');
value.value='true';value.listeners.input();assert.equal(JSON.parse(input.value).stream,true);
mode.value='json';mode.onchange();assert.equal(input.hidden,false);assert.equal(JSON.parse(input.value).input.text,'<text>');
mode.value='form';mode.onchange();const row=panel.children[0];row.children[0].value='__proto__.x';row.children[0].listeners.input();assert(!input.checkValidity());
row.children[0].value='input.text';row.children[0].listeners.input();assert(input.checkValidity());
console.log('Template editor passed: nested JSON/form roundtrip, typed constants, variable preservation, invalid path rejection.');

const numeric=new Node('textarea');numeric.value='{"obj":{"0":"x"},"array":["a","b"]}';window.EmotiveTemplateEditor(numeric,'tts_body_template');const editorMode=numeric.editor.children[0].children[0];editorMode.value='form';editorMode.onchange();const first=numeric.editor.children[1].children[0];first.children[2].value='changed';first.children[2].listeners.input();const output=JSON.parse(numeric.value);assert(!Array.isArray(output.obj));assert(Array.isArray(output.array));assert.equal(output.obj['0'],'changed');
