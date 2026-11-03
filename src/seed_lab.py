"""Persistent seed comparisons using the same director and transport as chat."""

import asyncio
import copy
import io
import json
import os
import random
import re
import time
import uuid
import wave
from pathlib import Path

from astrbot.api import logger

from .configuration import PLATFORM
from .custom_request import audio_extension
from .director import resolve_instruction
from .emotion import history_snapshot
from .events import SNAPSHOT
from .network import ServiceError, safe_error_message
from .platform_tts import platform_connection_config

PRESETS = [
    {
        "id": "happy",
        "title": "开心 · 分享好消息",
        "contexts": [
            {"role": "user", "content": "我之前准备了很久的比赛，今天终于出结果了。"},
            {"role": "assistant", "content": "快告诉我，怎么样？"},
            {"role": "user", "content": "拿到第一名了！多亏你一直陪着我。"},
        ],
        "reply": "哇，你真的做到了！我就知道你可以，今天可得好好庆祝一下。",
    },
    {
        "id": "sad",
        "title": "低落 · 舍不得的小东西",
        "contexts": [
            {"role": "assistant", "content": "你送我的那枚小书签，我找了一整天都没找到。"},
            {"role": "user", "content": "没关系，下次我再送你一个。"},
            {"role": "assistant", "content": "可那是你第一次送我的礼物。"},
            {"role": "user", "content": "是不是有点舍不得？"},
        ],
        "reply": "嗯，有一点。明明昨天还放在这里的，怎么就找不到了呢。",
    },
    {
        "id": "comfort",
        "title": "安慰 · 安静陪伴",
        "contexts": [
            {"role": "user", "content": "今天面试又没过，我有点累了。"},
            {"role": "assistant", "content": "你为这次面试准备了很久，肯定不好受。"},
            {"role": "user", "content": "先别帮我分析原因，就陪我待一会儿，好吗？"},
        ],
        "reply": "好，先歇一会儿吧。我在这儿陪着你，不想说话也没关系。",
    },
    {
        "id": "playful",
        "title": "俏皮 · 熟人重逢",
        "contexts": [
            {"role": "user", "content": "忙完我就来找你，给你带好吃的。"},
            {"role": "assistant", "content": "那我可等着了，说好了不许赖账。"},
            {"role": "user", "content": "我来啦，甜点也带来了。有没有想我？"},
        ],
        "reply": "你终于来了，我还以为你把我忘了呢。看在甜点的份上，这次就原谅你啦。",
    },
    {
        "id": "panic",
        "title": "心虚 · 急忙拦人",
        "contexts": [
            {"role": "user", "content": "桌上那个盒子怎么歪了，你是不是碰过？"},
            {"role": "assistant", "content": "没有吧，可能是光线暗，你看错了。"},
            {"role": "user", "content": "那我现在打开检查一下。"},
        ],
        "reply": "等一下，先别打开！我就碰了一小下，真的只有一小下。糟糕，好像闯祸了。",
    },
]

SUMMARY_KEYS = (
    "tts_mode",
    "tts_provider_id",
    "tts_model",
    "voice",
    "speech_rate",
    "tts_seed",
    "voice_persona",
    "auto_emotion",
    "analysis_mode",
    "analysis_provider_id",
    "analysis_model",
    "context_messages",
    "context_max_chars",
    "fallback_instruction",
)


class PreviewEvent:
    def __init__(self, scene, config):
        history = history_snapshot(
            scene["contexts"][:-1],
            int(config.get("context_messages", 5)),
            int(config.get("context_max_chars", 4000)),
        )
        self.snapshot = {
            "history": history,
            "user": scene["contexts"][-1]["content"],
            "persona": "",
        }

    def get_extra(self, key):
        return self.snapshot if key == SNAPSHOT else None


def playback_wav(data):
    """Replace placeholder frame counts without modifying PCM samples."""
    if audio_extension(data) != "wav":
        return data
    try:
        with wave.open(io.BytesIO(data), "rb") as src:
            params = src.getparams()
            pcm = src.readframes(len(data))
        out = io.BytesIO()
        with wave.open(out, "wb") as dst:
            dst.setparams(params)
            dst.writeframes(pcm)
        return out.getvalue()
    except (wave.Error, EOFError):
        raise ServiceError("expected WAV audio") from None


class SeedLab:
    MAX_BATCHES = 20

    def __init__(self, plugin, defaults):
        self.plugin = plugin
        self.defaults = defaults
        self.root = Path(plugin.data_dir).parent / "seed_trials"
        self.root.mkdir(parents=True, exist_ok=True)
        self.batches = {}
        self.task = None
        for path in self.root.glob("*.json"):
            try:
                batch = json.loads(path.read_text(encoding="utf-8"))
                if not re.fullmatch(r"[0-9a-f]{32}", batch["id"]):
                    continue
                if batch["status"] == "running":
                    batch["status"] = "interrupted"
                    for sample in batch["samples"]:
                        if sample["status"] in ("pending", "running"):
                            sample["status"] = "interrupted"
                    self._store(batch)
                self.batches[batch["id"]] = batch
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("[EmotiveTTS] 一份种子试听记录无法读取，已跳过")

    def _store(self, batch):
        batch["updated_at"] = time.time()
        path = self.root / (batch["id"] + ".json")
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def get(self, batch_id):
        if not isinstance(batch_id, str) or batch_id not in self.batches:
            raise ValueError("试听记录不存在，请刷新列表。")
        return self.batches[batch_id]

    def listing(self):
        return {
            "presets": copy.deepcopy(PRESETS),
            "selected_seed": self.plugin.config.get("tts_seed", -1),
            "batches": [
                copy.deepcopy(b)
                for b in sorted(self.batches.values(), key=lambda b: b["created_at"], reverse=True)
            ],
        }

    def start(self, config, count, config_revision, seeds=None):
        manual = seeds is not None
        if manual and (
            not isinstance(seeds, list)
            or not 1 <= len(seeds) <= 4
            or any(type(seed) is not int or not 0 <= seed <= 65535 for seed in seeds)
            or len(set(seeds)) != len(seeds)
        ):
            raise ValueError("请指定1至4个不重复的整数种子，范围0至65535。")
        if not manual and (type(count) is not int or not 2 <= count <= 4):
            raise ValueError("每批请选择2至4个种子。")
        if self.task and not self.task.done():
            raise ValueError("已有一批试听正在生成，请等待完成或停止。")
        if len(self.batches) >= self.MAX_BATCHES:
            raise ValueError("已保留20批对比，请先删除不需要的记录。")
        config = {**self.defaults, **copy.deepcopy(dict(config))}
        original_mode = config.get("tts_mode")
        if config.get("tts_mode") == PLATFORM:
            config = copy.deepcopy(platform_connection_config(self.plugin.context, config))
        if not config.get("voice") and original_mode != "自定义兼容接口":
            raise ValueError("请先保存有效的语音音色。")
        config["api_key"] = config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
        if any(len(s["reply"]) > int(config["max_text_chars"]) for s in PRESETS):
            raise ValueError("当前正文长度上限小于预设台词长度，请先调大后保存。")
        current = config.get("tts_seed", -1)
        seeds = (
            list(seeds)
            if manual
            else ([current] if type(current) is int and 0 <= current <= 65535 else [])
        )
        rng = random.SystemRandom()
        while not manual and len(seeds) < count:
            seed = rng.randrange(65536)
            if seed not in seeds:
                seeds.append(seed)
        batch_id = uuid.uuid4().hex
        summary = {key: config.get(key) for key in SUMMARY_KEYS}
        summary["tts_mode"] = original_mode
        batch = {
            "id": batch_id,
            "created_at": time.time(),
            "status": "running",
            "seeds": seeds,
            "config_revision": config_revision,
            "settings": summary,
            "scenes": copy.deepcopy(PRESETS),
            "instructions": {},
            "applied_seed": None,
            "samples": [
                {"id": f"{s['id']}_{seed}", "scene": s["id"], "seed": seed, "status": "pending"}
                for s in PRESETS
                for seed in seeds
            ],
        }
        (self.root / batch_id).mkdir()
        self._store(batch)
        self.batches[batch_id] = batch
        self.task = asyncio.create_task(self._run(batch, config))
        self.plugin.jobs.add(self.task)
        self.task.add_done_callback(self.plugin.jobs.discard)
        self.task.add_done_callback(self._task_done)
        return copy.deepcopy(batch)

    @staticmethod
    def _task_done(task):
        if not task.cancelled() and task.exception() is not None:
            logger.warning("[EmotiveTTS] 种子试听任务保存异常，请检查数据目录权限")

    async def _run(self, batch, config):
        try:
            for scene in batch["scenes"]:
                trace = {}
                async with asyncio.timeout(float(config["job_timeout"])):
                    async with self.plugin.gate.enter("seed-preview:" + batch["id"]):
                        instruction = await resolve_instruction(
                            config,
                            self.plugin.context,
                            self.plugin.http,
                            PreviewEvent(scene, config),
                            scene["reply"],
                            trace=trace,
                        )
                batch["instructions"][scene["id"]] = {"instruction": instruction, **trace}
                self._store(batch)
                for sample in [s for s in batch["samples"] if s["scene"] == scene["id"]]:
                    sample["status"] = "running"
                    self._store(batch)
                    try:
                        async with asyncio.timeout(float(config["job_timeout"])):
                            async with self.plugin.gate.enter("seed-preview:" + batch["id"]):
                                data = await self.plugin.http.synthesize(
                                    {**config, "tts_seed": sample["seed"]},
                                    scene["reply"],
                                    instruction,
                                )
                        data = playback_wav(data)
                        path = self.root / batch["id"] / (sample["id"] + ".wav")
                        await asyncio.to_thread(path.write_bytes, data)
                        sample["status"] = "ok"
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 -- keep remaining comparisons
                        sample.update(status="failed", error=safe_error_message(exc))
                    self._store(batch)
            batch["status"] = (
                "complete" if all(s["status"] == "ok" for s in batch["samples"]) else "partial"
            )
        except asyncio.CancelledError:
            batch["status"] = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 -- preserve already generated audio
            batch.update(status="failed", error=safe_error_message(exc))
        finally:
            for sample in batch["samples"]:
                if sample["status"] in ("running", "pending"):
                    sample["status"] = (
                        "cancelled" if batch["status"] == "cancelled" else "interrupted"
                    )
            self._store(batch)

    async def cancel(self, batch_id):
        batch = self.get(batch_id)
        if batch["status"] == "running" and self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            # A task cancelled before its first instruction never enters finally.
            if batch["status"] == "running":
                batch["status"] = "cancelled"
                for sample in batch["samples"]:
                    if sample["status"] in ("pending", "running"):
                        sample["status"] = "cancelled"
                self._store(batch)

    def audio(self, batch_id, sample_id):
        batch = self.get(batch_id)
        sample = next(
            (s for s in batch["samples"] if s["id"] == sample_id and s["status"] == "ok"), None
        )
        if sample is None:
            raise ValueError("这份音频尚未生成成功。")
        return (self.root / batch["id"] / (sample["id"] + ".wav")).read_bytes()

    async def delete(self, batch_id):
        batch = self.get(batch_id)
        if batch["status"] == "running":
            raise ValueError("请先停止生成，再删除这批记录。")
        # Paths are derived only from stored IDs, never a client-provided path.
        for sample in batch["samples"]:
            (self.root / batch["id"] / (sample["id"] + ".wav")).unlink(missing_ok=True)
        (self.root / batch["id"]).rmdir()
        (self.root / (batch["id"] + ".json")).unlink()
        del self.batches[batch_id]
