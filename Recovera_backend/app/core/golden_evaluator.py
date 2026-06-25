"""Golden question evaluator for deterministic routing coverage.

Usage:
    python -m app.core.golden_evaluator

This lightweight evaluator checks the static question-profile layer without
calling any LLMs or database.  It is intended as a quick regression guard before
shipping changes to prompts, templates, or chart grammar.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.question_patterns import detect_question_profile, should_use_profile_fast_path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUITE = ROOT / "data" / "evaluation" / "golden_questions.json"


def evaluate_golden_questions(path: str | Path = DEFAULT_SUITE) -> dict[str, Any]:
    suite = json.loads(Path(path).read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case in suite:
        q = case["question"]
        p = detect_question_profile(q)
        row = {
            "id": case.get("id"),
            "question": q,
            "family": p.family,
            "intent": p.intent,
            "route": p.route,
            "template_key": p.template_key,
            "fast_path": should_use_profile_fast_path(p),
            "confidence": p.confidence,
        }
        results.append(row)

        expected = {
            "family": case.get("expected_family"),
            "intent": case.get("expected_intent"),
            "route": case.get("expected_route"),
            "template_key": case.get("expected_template"),
        }
        case_failures = []
        if expected["family"] and p.family != expected["family"]:
            case_failures.append(f"family expected {expected['family']} got {p.family}")
        if expected["intent"] and p.intent != expected["intent"]:
            case_failures.append(f"intent expected {expected['intent']} got {p.intent}")
        if expected["route"] and p.route != expected["route"]:
            case_failures.append(f"route expected {expected['route']} got {p.route}")
        if expected["template_key"] and p.template_key != expected["template_key"]:
            case_failures.append(f"template expected {expected['template_key']} got {p.template_key}")
        prefix = case.get("expected_template_prefix")
        if prefix and not p.template_key.startswith(prefix):
            case_failures.append(f"template prefix expected {prefix} got {p.template_key}")
        if p.intent != "general_query" and p.route != "rag_only" and not should_use_profile_fast_path(p):
            case_failures.append("expected deterministic fast-path")

        if case_failures:
            failures.append({"id": case.get("id"), "question": q, "failures": case_failures, "actual": row})

    return {
        "total": len(suite),
        "passed": len(suite) - len(failures),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }


def main() -> int:
    report = evaluate_golden_questions()
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
