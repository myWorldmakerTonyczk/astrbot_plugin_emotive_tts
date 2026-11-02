# 上下文情绪语音 · Emotive TTS

AstrBot 插件：读取当前会话历史，为完整回复生成简短的情绪走向指令，再调用阿里云语音合成。整段合成、不拼接逐句音频。默认最近 **5条历史消息**、**1.12倍语速**。

试听选定的设计依据见 [EXPERIMENT_DECISIONS.md](EXPERIMENT_DECISIONS.md)。效果取决于语音模型、音色和分析模型，不保证每次情绪判断准确。

## 安装

要求：AstrBot >=4.29.3、Python >=3.12、OneBot v11（NapCat / SnowLuma）。支持模型为阿里云提供的、具备自然语言 instruction 的语音合成组合，建议先使用已验证的 CosyVoice v3.5-plus 自定义音色。

将发布 ZIP 通过 AstrBot 插件管理页面的“从文件安装”导入，或解压到 `AstrBot/data/plugins/astrbot_plugin_emotive_tts/`。解压后 `main.py`、`metadata.yaml` 应直接位于插件目录。重载插件，依赖由 AstrBot 安装。

## 首次配置

1. 在“语音API”下拉框选“阿里云”，再填写百炼 API Key、地区、模型和与模型/地域匹配的音色 ID。Key 留空时读取 `DASHSCOPE_API_KEY`。插件包不含用户凭据或预填私人音色。
2. 北京默认完整接口为 `https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer`；其他地域使用官方对应接口，Key、音色也须属于正确地域。阿里模式自动选择该地区官方接口，不使用隐藏的自定义地址或请求头。业务空间ID在折叠配置中可选填写。
3. “语气判断模型 → 使用AstrBot模型”：复用当前会话聊天模型，可用 `analysis_provider_id` 指定另一个已配置的提供商。
4. “语气判断模型 → 自定义分析接口”：填写独立分析服务的完整 `/chat/completions` URL、模型、Key。接口须兼容 OpenAI Chat Completions、非流式 JSON object 输出。第三方服务会收到有限聊天历史、当前输入、回复及可选角色背景；可关闭 `include_persona`。
5. 首次测试填写一条私聊 UMO 白名单，仅在该会话启用。UMO 使用 `/sid` 获取，留空表示不限会话。
6. 关闭 CosyEcho、其他自动语音插件和 AstrBot 内置自动 TTS，防止重复朗读；关闭当前行为配置的流式回复。首版遇到流式结果会跳过，保留框架正常文字输出。
7. 正常与机器人聊天。无单独点歌或TTS命令；自动模式转换正常模型文字回复，已有语音、工具提示、空文本、超长回复均跳过；也支持下述LLM工具。

默认同时发送语音和文字。若只想发语音，关闭 `send_text_with_voice`；只有语音发送成功后才移除原文字，失败仍保留文字。平台返回成功但客户端实际未收到的情况，需要结合QQ端日志排查。

## 参数语义

- `context_messages=5`：本次用户消息之前最近5条 user/assistant 消息，不是5轮。0表示不传历史。当前输入和当前回复另传。
- 历史来自 `on_llm_request` 的 `req.contexts`，在正常生成前截取一次；工具消息、系统消息、图片URL不作为历史传递。历史按 `context_max_chars` 再限制长度。AstrBot会话隔离设置决定请求原本能看到哪些历史，本插件不额外采集群消息。
- `voice_style`：长期声音风格，作为分析输入；分析模型会把它与本次语气压缩为最终instruction，而不是机械追加长指令。
- 生成的instruction强调“先……再……最后……”等自然情绪走向；单一语气时不强凑转折。只输出指导，不重写正文，不把分析存入正常聊天记忆。
- 指令长度按ASCII=1、其他字符=2保守校验，上限100；无效JSON、超时或超长指令回退默认语气，不生硬截断。
- `speech_rate=1.12` 是实际合成参数。分析模型不修改这个数值；避免把文字中的“轻快”当成确定语速。
- 语气分析和语音各发生一次API调用（分析关闭或回退时除外），会增加费用和延迟。正常聊天模型另计。

## 自定义请求头与接入边界

“语音API → 自定义兼容接口”才显示自定义完整URL与 `tts_headers`；`analysis_headers` 仅在“语气判断模型 → 自定义分析接口”时显示。二者分别接受JSON对象，例如：

```json
{"X-DashScope-Workspace":"业务空间ID"}
```

可显式覆盖 `Authorization` 以适配可信兼容网关；不要在截图或版本库里暴露凭据。禁止Host、Content-Length等传输头及换行字符。

本插件只维护阿里云的请求与响应格式：`model` + `input.text/voice/instruction/rate`，返回 `output.audio.url`。**额外请求头不能改变第三方接口的数据格式。** 换用其他厂商需在外部提供阿里兼容代理；首版不支持任意JSON模板或其他厂商原生协议。语气分析独立使用OpenAI兼容协议，不等于TTS兼容OpenAI。

API接口只接受公开HTTPS地址。阿里旧接口返回HTTP OSS链接时只对 `.aliyuncs.com` 自动升级HTTPS。音频下载不带API Key或自定义请求头，禁止跳转、私网地址和超过32MiB的内容，仅接受WAV。

## 排队、回退与临时文件

- 同UMO按“进入合成钩子”的顺序串行，不承诺重排已经并发生成完的LLM回复；不同UMO有有限并发。
- `queue_capacity` 限制总等待/运行任务，`job_timeout` 包括排队；超时或满队列保留文字。
- 音频保存在AstrBot插件数据目录 `astrbot_plugin_emotive_tts/audio/`，默认1小时后清理，最短保留300秒。
- 重载/卸载会取消未完成的插件任务并关闭HTTP会话，不改变其他插件配置。
- 日志只显示失败类别，不打印密钥、签名下载地址、完整上下文或服务商原始错误正文。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

`requirements.txt` 为发布所需依赖；`requirements-dev.txt` 与开发锁文件属于本地开发环境，不应要求Linux服务器照搬Windows锁文件。自动测试使用模拟响应，不发送QQ、不使用API Key。Windows/Python3.13本地测试不替代Linux/Python3.12目标机器联调。

首次服务器联调建议：先仅测试一个私聊UMO，检查自动语气、重复消息、失败回退和音频可播放性，再决定是否开放群聊。首版未自动部署到服务器。

## 0.1.1界面调整

语音API改为“阿里云 / 自定义兼容接口”下拉框，按选择隐藏无关配置。语速、模型和音色仍是共用参数。分析服务有独立中文下拉框；超时、并发等放入折叠项。首次升级会保留旧版自定义地址/请求头，自动选择自定义模式；不会清空已有Key。

## LLM发送语音工具（0.1.2）

工具名：`emotive_send_voice`。安装/重载后可在AstrBot工具管理中查看，聊天模型须支持工具调用并获准使用该工具。插件总开关、私聊/群聊开关、UMO白名单仍生效，只能发送到当前会话，不能指定其他收件人。

- `text`：要朗读的文字，必填，遵守`max_text_chars`限制，会清除Markdown与链接。
- `mode`：可选，默认`auto`。`auto`使用正常LLM请求时截取的上文快照（最近`context_messages`条），与当前用户输入、工具传入正文一起判断语气，走与自动回复相同的分析/回退/合成流程。显式调用auto时，即使`auto_emotion`关闭也会尝试分析。
- `instruction`：可选，默认空。`manual`时必填，直接传给语音模型，完全跳过分析模型；沿用约50汉字/100ASCII的长度校验，不接受尖括号、花括号。`auto`时必须留空，以免混淆两种模式。

自动示例：

```json
{"text":"哼，你终于来找我啦！今天过得怎么样？","mode":"auto"}
```

手动示例：

```json
{"text":"哼，你终于来找我啦！今天过得怎么样？","mode":"manual","instruction":"先俏皮地假装埋怨，再放柔声音关心，语气自然。"}
```

想让模型自己决定什么时候发语音，可关闭“自动将模型回复转成语音”，保留“允许LLM调用发送语音工具”。角色提示词可写：需要用语音交流时调用emotive_send_voice；若已能根据对话判断语气，使用manual并填写简短具体的instruction；成功后不要重复朗读正文。

每次用户消息最多由工具成功发送一次；进行中或成功后的重复调用不再次合成。工具发送成功后，本次最终回复仍可正常发文字，但不会再次被插件自动转成语音；`send_text_with_voice`仅控制自动转换路径。失败不返回密钥或原始服务商错误，模型可继续用文字回复。工具调用不随机抽样（不受`trigger_probability`影响），也可在聊天启用流式时发送完整语音；不提供流式音频合成。自动模式依赖本次请求快照，没有可用历史时只分析当前信息；不额外搜集其他会话消息。

manual能省掉一次分析调用，但instruction效果仍取决于所用模型/音色的指令支持，并非保证逐字控制停顿或音高。

## 项目结构

- `src/`：语气分析、API连接、排队及插件业务实现。
- `main.py`：AstrBot要求的加载入口和事件/工具注册。
- `tests/`：自动测试。
- `docs/`：计划、实验结论和验证记录。
- `dist/`：本地安装包，不纳入Git。

根目录保留AstrBot需要的`metadata.yaml`、`_conf_schema.json`和依赖声明。开发环境、IDE配置、密钥、生成音频均由`.gitignore`排除。
