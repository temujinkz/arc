"""Crusoe evidence worker: public case packets only, no SDK/MCP or private vault.

API/capability and per-million token pricing contract checked against Crusoe's
first-party documentation on 2026-10-17. Catalog metadata is refreshed per run.
"""
import asyncio
import json
import math
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator

from .eval_adapters import validate_worker_scope
from .eval_model import MAX_OUTPUT, MAX_REQUEST_BYTES, save_json
from .evidence import EvidenceReader
from .models import RunPolicy, WorkerResult
from .parsing import parse_model_json
from .prompts import ROLES


class CrusoeConfig(BaseModel):
    api_key: SecretStr
    model: str = Field(min_length=1)
    base_url: str = "https://api.inference.crusoecloud.com/v1"
    max_output_tokens: int = Field(default=MAX_OUTPUT, ge=1)

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("Crusoe endpoint must be an HTTPS base URL without credentials/query")
        return value.rstrip("/")

    @classmethod
    def from_env(cls):
        key = os.getenv("CRUSOE_API_KEY", "").strip()
        model = os.getenv("CRUSOE_WORKER_MODEL", "").strip()
        if not key or not model:
            raise ValueError("Configure CRUSOE_API_KEY and CRUSOE_WORKER_MODEL")
        return cls(api_key=key, model=model,
                   base_url=os.getenv("CRUSOE_BASE_URL", cls.model_fields["base_url"].default),
                   max_output_tokens=int(os.getenv("CRUSOE_MAX_OUTPUT_TOKENS", str(MAX_OUTPUT))))

    def headers(self):
        return {"Authorization": "Bearer " + self.api_key.get_secret_value(),
                "Content-Type": "application/json"}


class ModelSpec(BaseModel):
    model: str
    context_length: int = Field(strict=True, gt=0)
    max_output_tokens: int = Field(strict=True, gt=0)
    output_parameter: str
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    request_price: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)

    @property
    def reservation_usd(self):
        # Reserve the catalog's full input context, not an unreliable token estimate.
        return float((self.context_length * self.input_per_million
                      + self.max_output_tokens * self.output_per_million) / Decimal(1_000_000)
                     + self.request_price)


async def inspect_model(client: httpx.AsyncClient, config: CrusoeConfig) -> ModelSpec:
    if os.getenv("ARC_RESEARCH_OFFLINE") == "1":
        raise ValueError("Offline mode forbids Crusoe network access")
    try:
        response = await client.get(config.base_url + "/models", headers=config.headers(), follow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f"Crusoe catalog HTTP {response.status_code}; no inference started")
        matches = [m for m in response.json()["data"] if m["id"] == config.model]
        if len(matches) != 1:
            raise ValueError("Configured Crusoe model is not in the current catalog")
        entry = matches[0]
        supported = entry.get("supported_parameters", [])
        parameter = next((p for p in ("max_completion_tokens", "max_tokens") if p in supported), None)
        if "response_format" not in supported or not parameter:
            raise ValueError("Crusoe worker needs JSON output and an output-token limit")
        maximum = entry["top_provider"]["max_completion_tokens"]
        if type(maximum) is not int or config.max_output_tokens > maximum:
            raise ValueError("Requested output exceeds the model's published completion limit")
        spec = ModelSpec(model=config.model, context_length=entry["context_length"],
                         max_output_tokens=config.max_output_tokens, output_parameter=parameter,
                         input_per_million=entry["pricing"]["prompt"],
                         output_per_million=entry["pricing"]["completion"],
                         request_price=entry["pricing"].get("request", 0))
        if spec.context_length <= spec.max_output_tokens:
            raise ValueError("Crusoe context cannot hold this request")
        return spec
    except httpx.RequestError:
        raise ValueError("Crusoe catalog unavailable; no inference started") from None
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Crusoe catalog lacks usable capability/pricing metadata") from None


class CrusoeWorker:
    def __init__(self, client, config, spec, directory: Path, board):
        if spec.model != config.model:
            raise ValueError("Crusoe configuration and catalog model must match")
        self.client, self.config, self.spec, self.board = client, config, spec, board
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        if (directory / "usage.json").exists():
            raise ValueError("Existing Crusoe work must not be automatically resumed")
        self.calls = []
        self.halted = False
        self.lock = asyncio.Lock()
        self.save()

    @property
    def total_cost(self):
        return sum(call.get("cost_usd", 0) for call in self.calls)

    @property
    def usage_unknown(self):
        return any(call["status"] == "pending" for call in self.calls)

    def save(self):
        save_json(self.directory / "usage.json", {
            "provider": "crusoe", "model": self.spec.model,
            "catalog": self.spec.model_dump(mode="json"),
            "total_cost_usd": self.total_cost, "usage_unknown": self.usage_unknown,
            "halted": self.halted, "calls": self.calls,
            "basis": "Catalog list-price estimate; all prompt tokens charged at uncached rate. Not account billing or credit balance.",
        })

    async def run(self, task, packet, remaining_budget):
        async with self.lock:
            return await self._run(task, packet, remaining_budget)

    async def _run(self, task, packet, remaining_budget):
        call = None
        cost = 0
        try:
            if self.halted or self.usage_unknown:
                return WorkerResult(status="blocked", cost_usd=None if self.usage_unknown else 0,
                                    unresolved=["Crusoe stopped; prior outcome requires review"])
            if os.getenv("ARC_RESEARCH_OFFLINE") == "1":
                raise ValueError("Offline mode forbids Crusoe inference")
            if (len(self.calls) >= RunPolicy().max_total_tasks
                    or any(c["task_id"] == task.id for c in self.calls)):
                raise ValueError("Crusoe task already attempted or task limit reached")
            if not math.isfinite(remaining_budget) or self.spec.reservation_usd > remaining_budget:
                raise ValueError("Remaining budget cannot cover the Crusoe request reservation")
            if packet.task_id != task.id or packet.hypothesis.id != task.hypothesis_id:
                raise ValueError("Crusoe packet outside assigned task")
            reader = EvidenceReader(self.board, task.run_id)
            for span in packet.spans:
                if span.run_id != task.run_id or reader.read_span(span.id) != span:
                    raise ValueError("Crusoe packet does not match immutable public case evidence")
            body = {
                "model": self.spec.model,
                "messages": [{"role": "system", "content": ROLES[task.role]
                    + "\nReturn only JSON matching the schema below. Return at most two concise findings. "
                    "Self-authored evidence supports attributed self-reports, not independent verification. "
                    "No tools are available. Request children only for distinct questions answerable from supplied spans. "
                    "Copy exact hypothesis/span IDs. Leave reviewed false; omit cost_usd.\n"
                    + json.dumps(WorkerResult.model_json_schema(), separators=(",", ":"))},
                    {"role": "user", "content": packet.model_dump_json()}],
                "response_format": {"type": "json_object"},
                self.spec.output_parameter: self.spec.max_output_tokens,
            }
            wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            if len(wire) > MAX_REQUEST_BYTES or len(wire) + self.spec.max_output_tokens > self.spec.context_length:
                raise ValueError("Crusoe packet exceeds application/model request bounds")
            call = {"task_id": task.id, "status": "pending", "reserved_usd": self.spec.reservation_usd}
            self.calls.append(call)
            self.save()  # Unknown outcomes cannot trigger another request or paid fallback.
            ticket = len(self.calls) - 1
            save_json(self.directory / f"request-{ticket:02d}.json", body)
            response = await self.client.post(self.config.base_url + "/chat/completions",
                                              headers=self.config.headers(), content=wire, follow_redirects=False)
            call["http_status"] = response.status_code
            call["rate_limits"] = {k: v for k, v in response.headers.items() if k.startswith("x-ratelimit-")}
            if "retry-after" in response.headers:
                call["retry_after"] = response.headers["retry-after"]
            if response.status_code != 200:
                self.halted = True
                raise ValueError(f"Crusoe HTTP {response.status_code}; no retry or provider fallback")
            raw = response.json()
            save_json(self.directory / f"response-{ticket:02d}.json", raw)
            usage = raw.get("usage", {})
            counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
            if any(type(n) is not int or n < 0 for n in counts):
                raise ValueError("Crusoe response has unknown token usage")
            if raw.get("model") != self.spec.model:
                raise ValueError("Crusoe returned an unexpected model; usage pricing requires review")
            cost = float((counts[0] * self.spec.input_per_million + counts[1] * self.spec.output_per_million)
                         / Decimal(1_000_000) + self.spec.request_price)
            call.update(status="complete", usage=usage, cost_usd=cost)
            if counts[0] > self.spec.context_length or counts[1] > self.spec.max_output_tokens:
                self.halted = True
            self.save()
            self.board.record_usage(task.id, task.version, cost)
            choices = raw.get("choices", [])
            if self.halted or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                raise ValueError("Crusoe result incomplete; no retry")
            message = choices[0]["message"]
            if message.get("tool_calls") or message.get("refusal"):
                raise ValueError("Crusoe worker returned unsupported tool call/refusal")
            result = parse_model_json(message["content"], WorkerResult)
            validate_worker_scope(result, packet)
            for finding in result.findings:
                finding.reviewed = False
                finding.review_note = ""
            result.cost_usd = cost
            return result
        except asyncio.CancelledError:
            self.save()
            raise
        except Exception as exc:
            if call is not None:
                call["error"] = type(exc).__name__
            self.save()
            # Don't surface provider bodies, exception strings or credentials.
            return WorkerResult(status="blocked", cost_usd=None if self.usage_unknown else cost,
                                unresolved=[f"Crusoe {type(exc).__name__}: worker blocked; no automatic retry"])
