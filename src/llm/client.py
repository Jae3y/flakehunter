"""Traced Gemini client shared by the baseline and the agent.

One implementation serves both arms deliberately. The comparison is only fair
if the baseline and the agent get the same model and the same call semantics,
and sharing the client makes that structural rather than something to
remember: they cannot drift apart because there is only one of them.

Every call opens a trajectory turn and writes it, including when the call
fails. There is no code path that reaches the API without being traced.

The transport is ``urllib`` from the standard library rather than an SDK --
see `DECISIONS.md` D-001. The surface needed is one POST.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from src.telemetry.tracer import Tracer, Turn

__all__ = [
    "DEFAULT_MODEL",
    "GeminiClient",
    "LLMError",
    "LLMResponse",
]

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

#: Verified callable with this credential by ``scripts/check_llm.py``.
#: Overridden by ``FLAKEHUNTER_MODEL``.
DEFAULT_MODEL = "gemini-3.6-flash"

#: HTTP statuses worth retrying: rate limits and transient server faults.
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})

#: Attempts per call, including the first.
MAX_ATTEMPTS = 5

#: Base backoff, doubled per attempt.
BACKOFF_S = 8.0

#: Longest we will honour a server-supplied retry delay before giving up. A
#: per-minute rate limit says "retry in 30s" and is worth waiting out; a
#: per-day quota also returns 429 with a short retryDelay, and waiting there
#: just burns the session.
MAX_HONOURED_RETRY_S = 90.0

#: Quota ids that will not clear within a session, whatever retryDelay says.
NON_RECOVERABLE_QUOTA = ("PerDay", "PerProjectPerModel-FreeTier")


class LLMError(RuntimeError):
    """Raised when a call could not be completed after retries."""


def _retry_delay_seconds(error: Mapping[str, Any]) -> float | None:
    """Extract ``RetryInfo.retryDelay`` from a Google API error body."""
    for item in error.get("details", []):
        raw = item.get("retryDelay")
        if isinstance(raw, str) and raw.endswith("s"):
            try:
                return float(raw[:-1])
            except ValueError:
                return None
    return None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One completed model call."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    thinking_tokens: int
    total_tokens: int
    latency_ms: int
    attempts: int
    finish_reason: str

    @property
    def billed_output_tokens(self) -> int:
        """Output tokens billed: visible completion plus hidden reasoning.

        Gemini 3.x charges reasoning tokens as output. Reporting only
        ``candidatesTokenCount`` would understate spend, sometimes by an order
        of magnitude on a short answer that required long deliberation.
        """
        return self.completion_tokens + self.thinking_tokens

    def json(self) -> Any:
        """Parse the reply as JSON, tolerating a markdown code fence.

        Raises:
            ValueError: If the reply will not parse, naming truncation as
                the cause when the model hit its output ceiling.
        """
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Truncation and malformed output want different fixes: one is a
            # budget problem, the other a prompt problem. Name which it was.
            if self.finish_reason == "MAX_TOKENS":
                raise ValueError(
                    f"reply was truncated at the output ceiling after "
                    f"{self.billed_output_tokens} output tokens; "
                    f"raise max_output_tokens"
                ) from exc
            raise ValueError(
                f"reply was not valid JSON ({exc}); "
                f"finish={self.finish_reason}, {len(text)} chars"
            ) from exc


class GeminiClient:
    """Calls Gemini, records every call to the trajectory.

    Args:
        tracer: Trajectory writer. Required -- there is no untraced mode.
        model: Model identifier. Defaults to ``FLAKEHUNTER_MODEL`` then
            :data:`DEFAULT_MODEL`.
        api_key: Credential. Defaults to ``GEMINI_API_KEY``.
        max_output_tokens: Ceiling per call. Generous by default: patches
            carry the complete new contents of every changed file, and Gemini
            draws reasoning tokens from this same budget, so a value tuned for
            the visible answer truncates the reply mid-JSON.
    """

    def __init__(
        self,
        tracer: Tracer,
        model: str | None = None,
        api_key: str | None = None,
        max_output_tokens: int = 32768,
    ) -> None:
        self.tracer = tracer
        self.model = model or os.environ.get("FLAKEHUNTER_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise LLMError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self.max_output_tokens = max_output_tokens
        self.total_prompt_tokens = 0
        self.total_output_tokens = 0
        self.calls = 0

    def complete(
        self,
        *,
        agent_name: str,
        instruction: str,
        system: str | None = None,
        temperature: float = 0.0,
        response_schema: Mapping[str, Any] | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Make one model call and record it as a trajectory turn.

        Args:
            agent_name: Which component is calling, for the trajectory.
            instruction: The user-role prompt.
            system: Optional system instruction.
            temperature: 0.0 by default -- the same case should produce the
                same reasoning between runs wherever the model allows it.
            response_schema: JSON schema to constrain the reply. When given,
                the model is asked for ``application/json``.
            max_output_tokens: Per-call override.

        Returns:
            The completed response.

        Raises:
            LLMError: If every attempt failed.
        """
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": instruction}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens or self.max_output_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if response_schema is not None:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = dict(response_schema)

        full_instruction = f"{system}\n\n---\n\n{instruction}" if system else instruction

        with self.tracer.turn(agent_name, self.model, full_instruction) as turn:
            turn.call(
                "gemini.generateContent",
                model=self.model,
                temperature=temperature,
                max_output_tokens=payload["generationConfig"]["maxOutputTokens"],
                structured=response_schema is not None,
                instruction_chars=len(full_instruction),
            )
            response = self._send(payload, turn)

            # A reply cut off at the budget is unusable when it is structured:
            # the JSON simply ends mid-string. Retry once with more room rather
            # than surfacing a parse error the caller cannot act on.
            if response.finish_reason == "MAX_TOKENS":
                widened = payload["generationConfig"]["maxOutputTokens"] * 2
                turn.reflect(
                    f"{turn.reflection}\nreply hit the output ceiling "
                    f"({payload['generationConfig']['maxOutputTokens']}); "
                    f"retrying with {widened}".strip()
                )
                payload["generationConfig"]["maxOutputTokens"] = widened
                response = self._send(payload, turn)

            turn.respond(
                stdout=response.text,
                exit_code=0,
                duration_ms=response.latency_ms,
            )
            turn.spend(response.prompt_tokens, response.billed_output_tokens)
            turn.reflect(
                f"{self.model} replied in {response.latency_ms} ms after "
                f"{response.attempts} attempt(s); finish={response.finish_reason}; "
                f"tokens prompt={response.prompt_tokens} "
                f"completion={response.completion_tokens} "
                f"thinking={response.thinking_tokens}"
            )

        self.calls += 1
        self.total_prompt_tokens += response.prompt_tokens
        self.total_output_tokens += response.billed_output_tokens
        return response

    def _send(self, payload: dict[str, Any], turn: Turn) -> LLMResponse:
        """POST with retries, returning the parsed response."""
        url = f"{API_ROOT}/models/{self.model}:generateContent?key={self.api_key}"
        started = time.perf_counter()
        last_detail = "no attempt made"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            status, body = self._post(url, payload)
            if status == 200:
                return self._parse(body, started, attempt)

            error = body.get("error", {})
            detail = str(error.get("message", body))[:200]
            last_detail = f"HTTP {status}: {detail}"
            retryable = status in RETRYABLE or status == 0

            # A 429 carries structured detail saying which quota was hit and
            # how long to wait. A per-day quota is not something a retry loop
            # can outlast, so say so plainly rather than sleeping four times
            # and reporting a generic failure.
            if status == 429:
                quota_ids = [
                    violation.get("quotaId", "")
                    for item in error.get("details", [])
                    for violation in item.get("violations", [])
                ]
                if any(
                    marker in quota
                    for quota in quota_ids
                    for marker in NON_RECOVERABLE_QUOTA
                ):
                    raise LLMError(
                        f"{self.model}: daily quota exhausted ({', '.join(quota_ids)}). "
                        "This will not clear by retrying. Either wait for the quota "
                        "to reset, switch FLAKEHUNTER_MODEL to a model with its own "
                        "allowance, or move off the free tier."
                    )
                delay = _retry_delay_seconds(error)
                if delay is not None and delay <= MAX_HONOURED_RETRY_S:
                    turn.reflect(
                        f"{turn.reflection}\nrate limited; honouring "
                        f"server retryDelay of {delay:.0f}s".strip()
                    )
                    time.sleep(delay)
                    continue
            turn.reflect(
                f"{turn.reflection}\nattempt {attempt}: {last_detail}"
                f"{' (retrying)' if retryable and attempt < MAX_ATTEMPTS else ''}".strip()
            )
            if not retryable or attempt == MAX_ATTEMPTS:
                break
            time.sleep(BACKOFF_S * (2 ** (attempt - 1)))

        raise LLMError(f"{self.model} call failed: {last_detail}")

    @staticmethod
    def _post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST JSON, returning (status, body) without raising on HTTP error."""
        request = urllib.request.Request(url, data=json.dumps(payload).encode())
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"error": {"message": raw[:300]}}
        except Exception as exc:  # noqa: BLE001 - any transport fault is retryable
            return 0, {"error": {"message": f"{type(exc).__name__}: {exc}"}}

    def _parse(
        self, body: Mapping[str, Any], started: float, attempts: int
    ) -> LLMResponse:
        """Turn a successful API body into an :class:`LLMResponse`."""
        candidates = body.get("candidates") or []
        first = candidates[0] if candidates else {}
        parts = first.get("content", {}).get("parts", []) or []
        text = "".join(part.get("text", "") for part in parts)
        usage = body.get("usageMetadata", {})
        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=int(usage.get("promptTokenCount", 0)),
            completion_tokens=int(usage.get("candidatesTokenCount", 0)),
            thinking_tokens=int(usage.get("thoughtsTokenCount", 0)),
            total_tokens=int(usage.get("totalTokenCount", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            finish_reason=str(first.get("finishReason", "UNKNOWN")),
        )

    def usage_summary(self) -> dict[str, int]:
        """Cumulative spend for this client."""
        return {
            "calls": self.calls,
            "prompt_tokens": self.total_prompt_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_output_tokens,
        }
