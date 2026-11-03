# 开发说明

## 模块边界

- `main.py`：AstrBot加载入口，声明事件和LLM工具。仅做框架绑定，必须放在插件根目录。工具通过src/tools/tool.py提供的注册函数接入。
- `src/plugin.py`：事件编排、会话权限、发送与生命周期。自动回复和工具共用任务跟踪及合成发送流程。
- `src/director.py`：结合当前事件快照生成instruction；选择分析接口并统一回退，不写入普通聊天历史。
- `src/tools/tool.py`：LLM语音工具的参数说明、注册及调用处理；注册时绑定根入口模块，支持框架重载和禁用。
- `src/requests.py`：验证LLM工具参数，产生不可变VoiceRequest；验证阶段不发起网络请求。
- `src/emotion.py`：纯文本和历史处理，不依赖框架状态或网络。
- `src/configuration.py`：配置迁移及两种接口模式的解析。
- `src/network.py`：HTTP请求、响应校验、下载与凭据隔离。
- `src/queue_manager.py`：有限并发和同会话串行队列。
- `src/events.py`：当前消息上的状态键，避免把不同会话的状态存进共享变量。

`src/view/settings.py`提供视图设置API；`views/settings/`存放由AstrBot扫描的HTML/CSS/JS与表单声明。根Schema保留隐藏字段以维护默认值与迁移，所有字段均隐藏于插件配置，统一在视图里编辑。新增参数时同步更新两个Schema及视图分组。视图仅通过官方bridge读写设置，不直接访问管理面板凭据。

## 编写约定

使用包内相对导入，不使用`from src...`，避免插件安装后与其他包冲突。对外接口和异步流程标明参数与返回类型；注释解释约束与原因。LLM工具参数说明集中在src/tools/tool.py的docstring中，由AstrBot解析。

网络调用保持异步。需要落盘的音频使用插件数据目录，不存入源码目录。自动回复与工具调用都通过统一任务入口执行，保证卸载时可取消。取消异常继续传播，普通服务错误按现有策略回退；不把密钥、原始响应、完整上下文写入日志。

新增功能先判断属于哪个模块，不在事件入口里堆积传输细节或参数校验。配置字段更名必须考虑已有配置迁移。

## 本地检查

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```

自动测试使用模拟API和发送接口。最终仍需在真实AstrBot中重载，检查配置、工具调用及目标消息平台的语音播放。打包必须保留根main.py和完整src包，排除虚拟环境、IDE配置、密钥及生成音频。
