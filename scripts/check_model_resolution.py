"""Smoke-check Gemini model resolution without needing httpx or an API key.

Ensures a speech-synthesis / image / embedding model configured for a
reasoning role is downgraded to a text-capable model, which is what prevents
the `400 Bad Request ... :generateContent` seen with `gemini-3.8-flash-tts`.

Run:
    python scripts/check_model_resolution.py
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_PATH = os.path.join(HERE, "..", "app", "mcp", "client.py")

WANTED = {
    "DEFAULT_MODEL",
    "_RETIRED_MODELS",
    "_NON_TEXT_MODEL_MARKERS",
    "_normalize_model_name",
    "_is_text_capable",
    "_resolve_text_model",
}


def load_helpers() -> dict:
    """Exec only the pure model-resolution helpers from client.py."""
    with open(CLIENT_PATH, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANTED:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(name in WANTED for name in names):
                keep.append(node)

    # PEP 604 annotations (`str | None`) are evaluated eagerly on older
    # interpreters, so defer them when exec'ing the extracted helpers.
    future_import = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations", asname=None)],
        level=0,
    )

    module = ast.fix_missing_locations(
        ast.Module(body=[future_import] + keep, type_ignores=[])
    )
    namespace: dict = {}
    exec(compile(module, "<client-helpers>", "exec"), namespace)
    return namespace


def main() -> int:
    ns = load_helpers()
    resolve = ns["_resolve_text_model"]
    is_text = ns["_is_text_capable"]
    fallback = "gemini-2.5-flash"

    # (configured model, expected text-capable?, expected resolved model)
    cases = [
        ("gemini-3.8-flash-tts", False, fallback),
        ("gemini-3.5-transcribe", False, fallback),
        ("gemini-3-pro-image", False, fallback),
        ("gemini-embedding-001", False, fallback),
        ("gemini-3.8-flash", True, "gemini-3.8-flash"),
        ("gemini-2.5-pro", True, "gemini-2.5-pro"),
        # retired alias must still normalise
        ("gemini-1.5-flash", True, fallback),
    ]

    failures = 0
    print(f"{'configured':<24} {'text?':<6} {'resolved':<20} result")
    for model, expect_text, expect_resolved in cases:
        actual_text = is_text(model)
        actual_resolved = resolve(model, fallback)
        ok = actual_text == expect_text and actual_resolved == expect_resolved
        failures += 0 if ok else 1
        print(
            f"{model:<24} {str(actual_text):<6} {actual_resolved:<20} "
            f"{'PASS' if ok else 'FAIL'}"
        )

    print()
    if failures:
        print(f"{failures} case(s) FAILED")
        return 1
    print("All model-resolution cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


