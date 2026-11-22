"""The evaluation harness and the benchmark script (smoke)."""

import json
import subprocess
import sys
from pathlib import Path

from assistant.evaluation import (
    EVAL_DIR,
    load_json,
    mrr,
    ndcg_at,
    recall_at,
    run_conversations,
    score_conversations,
)

ROOT = Path(__file__).resolve().parents[1]


def test_ranking_metrics():
    qrels = {"a": 2, "b": 1, "c": 1}
    assert ndcg_at(["a", "b", "c"], qrels) == 1.0
    assert ndcg_at(["x", "a"], qrels) < ndcg_at(["a", "x"], qrels)
    assert ndcg_at(["x"], {}) == 0.0
    assert recall_at(["a", "x"], qrels) == 1 / 3
    assert mrr(["x", "y", "b"], qrels) == 1 / 3


def test_eval_files_are_consistent():
    queries = load_json(EVAL_DIR / "search_queries.json")
    handles = {p["handle"] for p in load_json(ROOT / "data" / "eval_catalog.json")["products"]}
    assert len(queries) >= 50 and all(q["qrels"] for q in queries)
    assert all(set(q["qrels"]) <= handles for q in queries)
    for name in ("conversations.json", "conversations_holdout.json"):
        for conv in load_json(EVAL_DIR / name)["conversations"]:
            for turn in conv["turns"]:
                assert {"user", "intent", "tool"} <= set(turn)


def test_eval_catalog_is_reproducible():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_eval_catalog
    finally:
        sys.path.pop(0)
    saved = load_json(ROOT / "data" / "eval_catalog.json")["products"]
    assert build_eval_catalog.build_catalog() == saved


def test_conversation_scoring_on_a_small_set():
    convs = [c for c in load_json(EVAL_DIR / "conversations.json")["conversations"] if c["id"] in ("o02", "h03")]
    outcomes = run_conversations(convs)
    score = score_conversations(outcomes)
    assert score["turns"] == 5 and score["intent_accuracy"] == 1.0
    assert score["handoff"]["opened"] == 1 and score["handoff"]["priority_accuracy"] == 1.0
    assert score["grounding"]["violations_in_final_answers"] == 0


def test_benchmark_quick_run_writes_reports(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "benchmark.py"), "--quick", "--out", str(tmp_path)],
                         capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    report = json.loads((tmp_path / "benchmark.json").read_text(encoding="utf-8"))
    assert report["claude_mode"].startswith("not measured")
    full = report["search"]["+ parsed filters (full)"]["all"]
    assert full["ndcg@5"] > report["search"]["legacy keyword scorer (before)"]["all"]["ndcg@5"]
    assert "## Search" in (tmp_path / "benchmark.md").read_text(encoding="utf-8")
