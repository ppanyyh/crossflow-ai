"""Minimal OpenAI-compatible Chat Completions client.

This module is the ONLY place that talks to an external LLM HTTP endpoint. It
is intentionally tiny and defensive:

- Every call has a hard timeout (``settings.LLM_TIMEOUT_SECONDS``).
- It returns a recognisable failure result (``LLMCallResult`` with
  ``success=False``) instead of raising, for every failure mode: LLM disabled,
  missing key / base URL / model, network error, timeout, non-2xx response, or
  a malformed response body.

It never makes any business decision. The deterministic state machine, quality
gate, supervisor approval and resolve gate all live in ``service.py``.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

import settings


@dataclass
class LLMCallResult:
    success: bool
    content: str = ""
    error: str = ""


def _chat_completions_url() -> str:
    base = settings.LLM_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def chat_completion(messages: list[dict]) -> LLMCallResult:
    """Call the OpenAI-compatible Chat Completions endpoint.

    Returns an ``LLMCallResult``; never raises on the failure modes above.
    """
    if not settings.LLM_ENABLED:
        return LLMCallResult(False, error="LLM 未启用（LLM_ENABLED=false），已回退规则模板")
    if not settings.LLM_API_KEY:
        return LLMCallResult(False, error="缺少 LLM_API_KEY，已回退规则模板")
    if not settings.LLM_BASE_URL:
        return LLMCallResult(False, error="缺少 LLM_BASE_URL，已回退规则模板")
    if not settings.LLM_MODEL:
        return LLMCallResult(False, error="缺少 LLM_MODEL，已回退规则模板")

    url = _chat_completions_url()
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.LLM_API_KEY}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=settings.LLM_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return LLMCallResult(False, error=f"LLM 服务返回 HTTP {exc.code}，已回退规则模板")
    except socket.timeout:
        return LLMCallResult(False, error="LLM 调用超时，已回退规则模板")
    except urllib.error.URLError as exc:
        return LLMCallResult(False, error=f"LLM 网络错误：{getattr(exc, 'reason', exc)}，已回退规则模板")
    except (TimeoutError, OSError) as exc:
        return LLMCallResult(False, error=f"LLM 连接失败：{exc}，已回退规则模板")
    except Exception as exc:  # noqa: BLE001 - defensive last resort, never crash
        return LLMCallResult(False, error=f"LLM 调用失败：{exc}，已回退规则模板")

    try:
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        return LLMCallResult(False, error=f"LLM 返回格式错误：{exc}，已回退规则模板")

    if not isinstance(content, str) or not content.strip():
        return LLMCallResult(False, error="LLM 返回内容为空，已回退规则模板")

    return LLMCallResult(True, content=content.strip())
