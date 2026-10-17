"""Latency-behaviour checks for the voice TTS path.

Verifies the changes that remove dead time from a voice turn:

  1. Circuit breaker - after repeated Edge-TTS failures we stop attempting it
     and go straight to gTTS, instead of paying a doomed websocket handshake
     (and its timeout) on every single turn. This mirrors the production
     symptom: `[Edge-TTS] Error ... 403` then a gTTS fallback on every request.
  2. Phrase cache - repeated canned confirmations are synthesised once.
  3. Language routing still picks the right gTTS language.
  4. EDGE_TTS_ENABLED=false skips Edge-TTS from the very first turn.

Runs fully offline and without the app's third-party dependencies
(fastapi / sqlalchemy / jose are never imported): the TTS helpers are
extracted from `app/mcp/routes.py` via AST and exec'd against stubbed
`edge_tts` / `gTTS` implementations.

Run:
    python scripts/check_tts_latency.py
"""

import ast
import asyncio
import base64
import io
import os
import re
import sys
import time
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTES_PATH = os.path.join(HERE, "..", "app", "mcp", "routes.py")

EDGE_CALLS = []
GTTS_CALLS = []


class FailingCommunicate:
    """Mimics the deployed 403: every connection attempt raises."""

    def __init__(self, text, voice, rate=None, pitch=None):
        self.voice = voice

    async def stream(self):
        EDGE_CALLS.append(self.voice)
        raise RuntimeError("403, message='Invalid response status'")
        # Unreachable, but required for Python to treat this as an async gen.
        yield {}


class StubGTTS:
    def __init__(self, text=None, lang=None):
        GTTS_CALLS.append((text, lang))

    def write_to_fp(self, fp):
        fp.write(b"ID3-fake-mp3-bytes")


class EdgeTTSModule:
    Communicate = FailingCommunicate


# Names to lift out of routes.py. Everything here is pure TTS logic.
WANTED = {
    "_EDGE_TTS_TIMEOUT",
    "_EDGE_TTS_FAILURE_THRESHOLD",
    "_EDGE_TTS_COOLDOWN",
    "_EDGE_TTS_ENABLED",
    "_edge_tts_failures",
    "_edge_tts_disabled_until",
    "_DEFAULT_VOICE",
    "_TTS_CACHE",
    "_TTS_CACHE_MAX",
    "_TTS_CACHE_MAX_CHARS",
    "_edge_tts_available",
    "_record_edge_tts_failure",
    "_record_edge_tts_success",
    "_edge_tts_synthesize",
    "_gtts_lang_for",
    "_gtts_synthesize",
    "_select_voice",
    "_tts_cache_get",
    "_tts_cache_put",
    "_generate_tts_audio",
}


def _assigned_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def load_tts_namespace(env_overrides):
    """Exec only the TTS helpers from routes.py in an isolated namespace."""
    for key, value in env_overrides.items():
        os.environ[key] = value

    with open(ROUTES_PATH, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    keep = []
    found = set()
    for node in tree.body:
        names = _assigned_names(node)
        if any(name in WANTED for name in names):
            keep.append(node)
            found.update(names)

    missing = WANTED - found
    if missing:
        print(f"  WARNING: not found in routes.py: {sorted(missing)}")

    future = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations", asname=None)],
        level=0,
    )
    module = ast.fix_missing_locations(ast.Module(body=[future] + keep, type_ignores=[]))

    ns = {
        "asyncio": asyncio,
        "base64": base64,
        "io": io,
        "os": os,
        "re": re,
        "time": time,
        "OrderedDict": OrderedDict,
        "edge_tts": EdgeTTSModule,
        "gTTS": StubGTTS,
    }
    exec(compile(module, "<tts-helpers>", "exec"), ns)
    return ns


def reset(ns):
    EDGE_CALLS.clear()
    GTTS_CALLS.clear()
    ns["_TTS_CACHE"].clear()
    ns["_edge_tts_failures"] = 0
    ns["_edge_tts_disabled_until"] = 0.0


async def scenario_circuit_breaker(ns):
    """Edge-TTS must stop being attempted once it has failed repeatedly."""
    reset(ns)
    speak = ns["_generate_tts_audio"]
    turns = 6
    # Distinct texts so the cache never masks the breaker behaviour.
    for i in range(turns):
        audio = await speak(f"Your order number {i} is confirmed and on its way.", "english")
        assert audio, f"turn {i} produced no audio"

    threshold = ns["_EDGE_TTS_FAILURE_THRESHOLD"]
    print(f"  edge attempts : {len(EDGE_CALLS)} over {turns} turns (threshold {threshold})")
    print(f"  gtts fallbacks: {len(GTTS_CALLS)} / {turns} turns")
    print(f"  breaker open  : {not ns['_edge_tts_available']()}")
    print(f"  dead round-trips avoided: {turns - len(EDGE_CALLS)} of {turns}")
    return len(EDGE_CALLS) <= threshold and len(GTTS_CALLS) == turns


async def scenario_cache(ns):
    """A repeated canned phrase must only be synthesised once."""
    reset(ns)
    speak = ns["_generate_tts_audio"]
    phrase = "Okay, I've updated your order. Anything else?"
    first = await speak(phrase, "english")
    after_first = len(GTTS_CALLS)
    for _ in range(4):
        again = await speak(phrase, "english")
        assert again == first, "cache returned different audio"
    print(f"  synth calls for 5 identical phrases: {len(GTTS_CALLS)}")
    return first is not None and after_first == 1 and len(GTTS_CALLS) == 1


async def scenario_language_routing(ns):
    """Script detection must still pick the right gTTS language."""
    reset(ns)
    speak = ns["_generate_tts_audio"]
    await speak("உங்கள் ஆர்டர் உறுதி செய்யப்பட்டது.", None)
    tamil = GTTS_CALLS[-1][1] if GTTS_CALLS else None
    await speak("आपका ऑर्डर तैयार है।", None)
    hindi = GTTS_CALLS[-1][1] if GTTS_CALLS else None
    await speak("Your order is ready.", None)
    english = GTTS_CALLS[-1][1] if GTTS_CALLS else None
    print(f"  tamil   -> gtts lang: {tamil}")
    print(f"  hindi   -> gtts lang: {hindi}")
    print(f"  english -> gtts lang: {english}")
    return tamil == "ta" and hindi == "hi" and english == "en"


async def scenario_disabled_flag(factory):
    """EDGE_TTS_ENABLED=false must skip Edge-TTS entirely from turn one."""
    ns = factory({"EDGE_TTS_ENABLED": "false"})
    reset(ns)
    speak = ns["_generate_tts_audio"]
    for i in range(3):
        await speak(f"Confirming your order {i}, thank you for waiting.", "english")
    print(f"  edge attempts with flag off: {len(EDGE_CALLS)}")
    print(f"  gtts calls: {len(GTTS_CALLS)}")
    return len(EDGE_CALLS) == 0 and len(GTTS_CALLS) == 3


async def main():
    base_env = {
        "EDGE_TTS_ENABLED": "true",
        "EDGE_TTS_FAILURE_THRESHOLD": "2",
        "EDGE_TTS_COOLDOWN": "300",
        "EDGE_TTS_TIMEOUT": "1",
    }

    def factory(overrides=None):
        env = dict(base_env)
        env.update(overrides or {})
        return load_tts_namespace(env)

    ns = factory()

    results = {}
    print("circuit breaker")
    results["circuit breaker"] = await scenario_circuit_breaker(ns)
    print("phrase cache")
    results["phrase cache"] = await scenario_cache(ns)
    print("language routing")
    results["language routing"] = await scenario_language_routing(ns)
    print("EDGE_TTS_ENABLED=false")
    results["EDGE_TTS_ENABLED=false"] = await scenario_disabled_flag(factory)

    print()
    failed = 0
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1

    if failed:
        print(f"\n{failed} scenario(s) failed")
        return 1
    print("\nAll TTS latency scenarios passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

