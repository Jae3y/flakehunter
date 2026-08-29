"""Probe the configured LLM provider: is the key valid, and what models exist?

Run before any phase that spends tokens. It answers three questions in order,
so a failure names its own cause instead of surfacing later as a confusing
error inside the agent loop:

1. Is ``GEMINI_API_KEY`` present in the environment at all?
2. Does it authenticate, and by which scheme? A key beginning ``AIza`` is an
   API key and travels as a query parameter; one beginning ``AQ.`` or ``ya29.``
   is an OAuth access token and travels as a bearer header. Both are tried.
3. Which models does this credential actually have access to? The model is
   discovered rather than hard-coded, so a guess at a name that no longer
   exists cannot silently become a phase-long failure.

    docker compose run --rm flakehunter python scripts/check_llm.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

#: Preference order when picking a default model.
#:
#: Flash-tier first: the agent loop runs 12 cases x up to 5 hypothesis rounds,
#: so per-call cost compounds. Listing a model is not proof it can be called --
#: retired models still appear in ListModels but return 404 on generateContent
#: for new keys -- so these are *tried in order* until one actually generates.
PREFERRED = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
)


def request(url: str, *, bearer: str | None = None, payload: dict | None = None):
    """Issue a JSON request, returning (status, body) without raising."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:400]}
    except Exception as exc:  # noqa: BLE001 - report any transport failure
        return 0, {"transport_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    """Report key presence, working auth scheme, and available models."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    print("=" * 74)
    print("LLM PROVIDER CHECK")
    print("=" * 74)
    if not key:
        print("\n  GEMINI_API_KEY: NOT SET")
        print("  BLOCKED: no credential available.")
        return 2
    print(f"\n  GEMINI_API_KEY: set, {len(key)} chars, prefix {key[:6]!r}")

    schemes: list[tuple[str, str, str | None]] = [
        ("query param (?key=)", f"{API_ROOT}/models?key={key}", None),
        ("bearer header", f"{API_ROOT}/models", key),
    ]

    working: tuple[str, str | None] | None = None
    models: list[str] = []
    for label, url, bearer in schemes:
        status, body = request(url, bearer=bearer)
        if status == 200:
            models = [
                m["name"].removeprefix("models/")
                for m in body.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            print(f"  auth via {label}: OK ({len(models)} models)")
            working = (label, bearer)
            break
        detail = body.get("error", {}).get("message", body)
        print(f"  auth via {label}: HTTP {status} -- {str(detail)[:160]}")

    if working is None:
        print("\n  BLOCKED: the credential did not authenticate under any scheme.")
        return 2

    print(f"\n  models listed ({len(models)}):")
    for name in sorted(models):
        print(f"    {name}")

    override = os.environ.get("FLAKEHUNTER_MODEL", "").strip()
    candidates = [override] if override else []
    candidates += [m for m in PREFERRED if m in models and m != override]
    candidates += [m for m in sorted(models) if m not in candidates]

    label, bearer = working
    print("\n  probing generateContent in preference order:")
    for model in candidates:
        url = f"{API_ROOT}/models/{model}:generateContent"
        if bearer is None:
            url += f"?key={key}"
        status, body = request(
            url,
            bearer=bearer,
            payload={
                "contents": [{"parts": [{"text": "Reply with the single word: ready"}]}],
                "generationConfig": {"maxOutputTokens": 2048, "temperature": 0},
            },
        )
        if status != 200:
            message = body.get("error", {}).get("message", str(body))
            print(f"    {model:<34} HTTP {status} -- {str(message)[:90]}")
            continue

        parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        usage = body.get("usageMetadata", {})
        print(f"    {model:<34} HTTP 200 -- {text[:40]!r}")
        print(
            f"\n  tokens on that call: prompt={usage.get('promptTokenCount')} "
            f"completion={usage.get('candidatesTokenCount')} "
            f"total={usage.get('totalTokenCount')}"
        )
        print(f"\n  READY. auth={label}  model={model}")
        print(f"  export FLAKEHUNTER_MODEL={model}")
        return 0

    print("\n  BLOCKED: listing worked but no model would generate.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
