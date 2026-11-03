"""Small HTTP transport; credentials never follow returned audio URLs."""

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .configuration import speech_connection
from .custom_request import audio_extension


class ServiceError(RuntimeError):
    """Only sanitized diagnostics, never provider response bodies or URLs."""


ERROR_HINTS = {
    "invalid request template": "请求头或请求体模板无效，请检查JSON及变量标记",
    "invalid audio response mapping": "未能提取音频，请检查获取方式、字段路径和编码；支持WAV/MP3/OGG/FLAC",
    "unsupported audio format": "未识别到支持的音频文件，支持WAV、MP3、OGG、FLAC，不支持裸PCM",
    "invalid TTS seed": "语音种子须为-1或0至65535的整数",
    "unsupported platform TTS protocol": "目前仅支持复用阿里dashscope_tts提供商配置，其他协议暂不支持",
    "voice API key is required": "所选平台语音提供商未填写有效API Key",
    "platform TTS provider is unavailable": "未选择平台语音模型，或所选提供商当前不可用",
    "invalid platform audio file": "平台语音模型未返回有效的本地音频文件",
    "voice ID is required": "未填写语音音色ID，请在语音设置中填写",
    "invalid speech rate or timeout": "语速或超时设置超出允许范围",
    "missing audio URL": "合成响应未返回音频下载地址",
    "expected WAV audio": "下载内容不是有效WAV音频",
    "provider returned an error": "语音服务返回错误",
    "endpoint must be a public HTTPS URL": "接口或下载地址必须为公开HTTPS地址",
    "private address rejected": "接口或下载地址解析到了非公网地址",
    "invalid custom header": "额外请求头格式不合法",
    "headers must be a JSON object": "额外请求头必须是JSON对象",
    "invalid JSON response": "服务响应不是有效JSON",
    "response too large": "服务响应超过大小限制",
    "invalid analysis response": "语气分析响应结构不符合要求",
}
PROVIDER_CODES = {
    "InvalidParameter",
    "InvalidApiKey",
    "InvalidAPIKey",
    "ModelNotFound",
    "AccessDenied",
    "QuotaExceeded",
    "Throttling",
    "InvalidInput",
    "DataInspectionFailed",
    "InternalError",
    "BadRequest",
    "InvalidParameterValue",
}


def safe_error_message(exc: Exception) -> str:
    """Log only our fixed diagnostics/status codes, never arbitrary exception text."""
    if isinstance(exc, ServiceError):
        message = str(exc)
        if message in ERROR_HINTS:
            return ERROR_HINTS[message]
        if re.fullmatch(r"(?:API|audio) HTTP [1-5][0-9]{2}", message):
            return message
        if message.startswith("provider code ") and message[14:] in PROVIDER_CODES:
            return "服务错误代码: " + message[14:]
        return "ServiceError（未分类服务错误）"
    if isinstance(exc, TimeoutError):
        return "请求或排队超时"
    return type(exc).__name__


def response_error(data) -> str | None:
    code = data.get("code") if isinstance(data, dict) else None
    return "provider code " + code if isinstance(code, str) and code in PROVIDER_CODES else None


def validate_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
        raise ServiceError("endpoint must be a public HTTPS URL")
    try:
        ip = ipaddress.ip_address(p.hostname)
    except ValueError:
        return
    if not ip.is_global:
        raise ServiceError("private address rejected")


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.delegate = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await self.delegate.resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(a["host"]).is_global for a in addresses):
            raise OSError("private address rejected")
        return addresses

    async def close(self):
        await self.delegate.close()


def headers_from_config(raw, key=""):
    extra = json.loads(raw or "{}") if isinstance(raw, str) else raw
    if not isinstance(extra, dict):
        raise ServiceError("headers must be a JSON object")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    forbidden = {"host", "content-length", "transfer-encoding", "connection"}
    for k, v in extra.items():
        if (
            not isinstance(k, str)
            or not isinstance(v, str)
            or k.lower() in forbidden
            or any(c in k + v for c in "\r\n")
        ):
            raise ServiceError("invalid custom header")
        # Allow explicit Authorization override for compatible gateways.
        for existing in list(headers):
            if existing.lower() == k.lower():
                del headers[existing]
        headers[k] = v
    return headers


async def read_limited(response, limit):
    chunks, size = [], 0
    async for block in response.content.iter_chunked(65536):
        size += len(block)
        if size > limit:
            raise ServiceError("response too large")
        chunks.append(block)
    return b"".join(chunks)


class HttpServices:
    def __init__(self):
        self.session = None

    async def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=PublicResolver()), trust_env=False
            )
        return self.session

    async def post_json(self, url, body, headers, timeout):
        validate_url(url)
        session = await self._session()
        async with session.post(
            url,
            json=body,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            raw = await read_limited(resp, 1024 * 1024)
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError):
                if resp.status != 200:
                    raise ServiceError(f"API HTTP {resp.status}") from None
                raise ServiceError("invalid JSON response") from None
            if resp.status != 200:
                raise ServiceError(response_error(data) or f"API HTTP {resp.status}")
            return data

    async def download_audio(self, url, timeout):
        # Legacy Aliyun replies can contain HTTP OSS links. Upgrade only official
        # OSS domains to HTTPS; do not permit plaintext arbitrary downloads.
        parsed = urlsplit(url)
        if parsed.scheme == "http" and (parsed.hostname or "").endswith(".aliyuncs.com"):
            url = urlunsplit(parsed._replace(scheme="https"))
        validate_url(url)
        session = await self._session()
        # No API/custom headers on audio download. Reject redirects explicitly.
        async with session.get(
            url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                raise ServiceError(f"audio HTTP {resp.status}")
            data = await read_limited(resp, 32 * 1024 * 1024)
        try:
            audio_extension(data)
        except ValueError:
            raise ServiceError("unsupported audio format") from None
        return data

    async def post_audio_request(self, url, body, headers, timeout):
        validate_url(url)
        session = await self._session()
        async with session.post(
            url,
            json=body,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if not 200 <= resp.status < 300:
                raise ServiceError(f"API HTTP {resp.status}")
            return await read_limited(resp, 48 * 1024 * 1024)

    async def synthesize(self, config, text, instruction):
        timeout = float(config.get("tts_timeout", 30))
        if config.get("tts_mode") in ("自定义兼容接口", "custom") and not config.get(
            "_platform_aliyun"
        ):
            from .custom_request import synthesize_custom

            seed = config.get("tts_seed", -1)
            if type(seed) is not int or not -1 <= seed <= 65535:
                raise ServiceError("invalid TTS seed")
            rate = float(config.get("speech_rate", 1.12))
            if not 0.5 <= rate <= 2.0 or not 0 < timeout <= 120:
                raise ServiceError("invalid speech rate or timeout")
            return await synthesize_custom(self, config, text, instruction)
        if not config.get("voice"):
            raise ServiceError("voice ID is required")
        rate = float(config.get("speech_rate", 1.12))
        if not 0.5 <= rate <= 2.0 or not 0 < timeout <= 120:
            raise ServiceError("invalid speech rate or timeout")
        body = {
            "model": config.get("tts_model", "cosyvoice-v3.5-plus"),
            "input": {
                "text": text,
                "voice": config.get("voice", ""),
                "format": "wav",
                "sample_rate": 24000,
                "rate": rate,
                "instruction": instruction,
                "language_hints": ["zh"],
            },
        }
        seed = config.get("tts_seed", -1)
        if type(seed) is not int or not -1 <= seed <= 65535:
            raise ServiceError("invalid TTS seed")
        if seed >= 0:
            body["input"]["seed"] = seed
        endpoint, extra_headers = speech_connection(config)
        headers = headers_from_config(extra_headers, config.get("api_key", ""))
        async with asyncio.timeout(timeout):
            data = await self.post_json(endpoint, body, headers, timeout)
            if data.get("code"):
                raise ServiceError(response_error(data) or "provider returned an error")
            url = (data.get("output") or {}).get("audio", {}).get("url")
            if not isinstance(url, str):
                raise ServiceError("missing audio URL")
            return await self.download_audio(url, timeout)

    async def analyze_external(self, config, system, payload):
        data = await self.post_json(
            config.get("analysis_endpoint", ""),
            {
                "model": config.get("analysis_model", ""),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": payload},
                ],
                "stream": False,
                "max_tokens": 250,
                "response_format": {"type": "json_object"},
            },
            headers_from_config(
                config.get("analysis_headers", "{}"), config.get("analysis_api_key", "")
            ),
            float(config.get("analysis_timeout", 12)),
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ServiceError("invalid analysis response") from None

    async def close(self):
        if self.session:
            await self.session.close()
