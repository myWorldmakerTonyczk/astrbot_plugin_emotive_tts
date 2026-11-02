"""Small HTTP transport; credentials never follow returned audio URLs."""

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .configuration import speech_connection


class ServiceError(RuntimeError):
    """Only sanitized diagnostics, never provider response bodies or URLs."""


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
            if resp.status != 200:
                raise ServiceError(f"API HTTP {resp.status}")
            raw = await read_limited(resp, 1024 * 1024)
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError):
                raise ServiceError("invalid JSON response") from None

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
        if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise ServiceError("expected WAV audio")
        return data

    async def synthesize(self, config, text, instruction):
        timeout = float(config.get("tts_timeout", 30))
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
        endpoint, extra_headers = speech_connection(config)
        headers = headers_from_config(extra_headers, config.get("api_key", ""))
        async with asyncio.timeout(timeout):
            data = await self.post_json(endpoint, body, headers, timeout)
            if data.get("code"):
                raise ServiceError("provider returned an error")
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
