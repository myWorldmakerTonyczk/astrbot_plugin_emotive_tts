# 自定义同步语音接口

在“连接与音色”选择“自定义兼容接口”。分别填写完整HTTPS地址、请求头、请求体，再选择音频获取方式。阿里直连及平台阿里配置复用不受这些模板影响。

## 两种编辑模式

请求头和请求体都可切换JSON粘贴/逐项填写。逐项模式可新增、删除key/value；嵌套key用 `input.text`，数组用 `items.0.text`。含字面点号的key请使用JSON模式。自定义值可选文本或JSON类型（数字、布尔、对象、数组）；请求头仅支持文本。两种模式同步为同一份JSON，不执行脚本或表达式。

变量为 `<text>`、`<instruction>`、`<model>`、`<voice>`、`<speech_rate>`、`<seed>`、`<api_key>`。JSON中将变量写在引号内；整值替换时语速和种子仍为数字，正文中的引号和换行自动编码，不递归替换正文里的标记。seed=-1时省略整值为该变量的对象字段。正文、音色、模型字段名称由服务商协议决定，语速单位不自动换算。

```json
{
  "model": "<model>",
  "text": "<text>",
  "voice": "<voice>",
  "instruction": "<instruction>",
  "speed": "<speech_rate>",
  "seed": "<seed>"
}
```

默认保留阿里式请求体以兼容既有配置。请求头未指定Content-Type时补application/json；默认根据API Key补Bearer鉴权，可关闭自动补全，或用显式Authorization覆盖。已保存请求头和自定义请求体可能包含凭据，因此不回显，留空保留原值；重新编辑应完整填写。清除请求体会恢复默认模板。

## 音频获取

|方式|配置|
|---|---|
|JSON中的音频网址|填写网址字段路径，例如output.audio.url，然后下载；下载不会携带合成请求的Key或请求头|
|直接返回音频文件|响应体即音频，不填字段路径|
|JSON中的Base64|填写编码所在路径，例如data.audio，解码保存|
|JSON中的Hex|填写编码所在路径，例如data.audio，解码保存|

字段路径支持 `data.audio`、`outputs[0].audio` 和JSON Pointer `/data/audio`。只处理常见同步JSON HTTP接口，暂不处理SSML原始请求体、SSE、WebSocket、任务轮询、动态签名、裸PCM。支持WAV/MP3/OGG/FLAC音频，根据实际内容识别，不把JSON错误响应当音频。

保存配置后点击“测试当前已保存的接口”生成一句测试语音，显示格式和实际instruction，并可在页面播放。此操作会调用所选语气和语音API，不发送聊天或保存记忆。批量比较仍使用种子试听；只有服务端支持seed且请求体引用它时，改变种子才可能影响结果。对方不支持instruction时，模板不能补出情绪控制能力。
