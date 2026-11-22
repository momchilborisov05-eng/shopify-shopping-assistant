"""Measure the offline assistant and write examples/benchmark.json and examples/benchmark.md.

    python scripts/benchmark.py            # full run (a few seconds)
    python scripts/benchmark.py --quick    # fewer latency repetitions, for CI

Everything runs offline: search on the generated 120-product catalogue (data/eval_catalog.json), the
router and the full tool loop with the DemoClient on the demo store. Claude-mode quality is not measured
here (it would need paid API calls) and is reported as "not measured".
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant.catalog import DemoStore  # noqa: E402
from assistant.evaluation import (  # noqa: E402
    EVAL_DIR,
    evaluate_grounding_detector,
    evaluate_search,
    load_json,
    router_only_accuracy,
    run_conversations,
    score_conversations,
)
from assistant.router import DialogueState, ProductMatcher, route  # noqa: E402
from assistant.search import SearchEngine  # noqa: E402

OUT = ROOT / "examples"


def percentiles(samples: list[float]) -> dict:
    ms = sorted(s * 1000 for s in samples)
    q = statistics.quantiles(ms, n=20) if len(ms) >= 2 else ms * 19
    return {"n": len(ms), "p50_ms": round(statistics.median(ms), 3), "p95_ms": round(q[18], 3)}


def timed(fn, reps: int) -> list[float]:
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer latency repetitions (CI smoke run)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    reps = 3 if args.quick else 30

    catalog = load_json(ROOT / "data" / "eval_catalog.json")["products"]
    queries = load_json(EVAL_DIR / "search_queries.json")
    dev = load_json(EVAL_DIR / "conversations.json")["conversations"]
    holdout = load_json(EVAL_DIR / "conversations_holdout.json")["conversations"]
    store = DemoStore()

    t0 = time.perf_counter()
    search = evaluate_search(catalog, queries)
    dev_outcomes = run_conversations(dev, store)
    hold_outcomes = run_conversations(holdout, store)
    conv = {"dev": score_conversations(dev_outcomes), "holdout": score_conversations(hold_outcomes)}
    router = {"dev": router_only_accuracy(dev, store), "holdout": router_only_accuracy(holdout, store)}
    detector = evaluate_grounding_detector(dev_outcomes + hold_outcomes)

    # latency
    t = time.perf_counter()
    engine = SearchEngine(catalog)
    build_ms = (time.perf_counter() - t) * 1000
    demo_engine = store.engine
    matcher = ProductMatcher(store.all_products())
    texts = [c["turns"][0]["user"] for c in dev]
    search_samples = []
    demo_samples = []
    router_samples = []
    for _ in range(reps):
        for q in queries:
            search_samples += timed(lambda q=q: engine.search(q["query"], limit=10), 1)
            demo_samples += timed(lambda q=q: demo_engine.search(q["query"], limit=6), 1)
        for text in texts:
            router_samples += timed(lambda text=text: route(DialogueState([(text, None)]), matcher), 1)
    turn_samples = [o.seconds for o in dev_outcomes + hold_outcomes]
    latency = {
        "index_build_120_products_ms": round(build_ms, 2),
        "search_120_products": percentiles(search_samples),
        "search_demo_store_12_products": percentiles(demo_samples),
        "router": percentiles(router_samples),
        "full_turn_demo_mode": {**percentiles(turn_samples),
                                "note": "router + tool + answer + grounding check + SQLite write, word delay off"},
    }

    report = {
        "generated": time.strftime("%Y-%m-%d"),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "quick": args.quick,
        "seconds": round(time.perf_counter() - t0, 1),
        "data": {"search_catalog_products": len(catalog), "search_queries": len(queries),
                 "relevance_labels": sum(len(q["qrels"]) for q in queries),
                 "dev_conversations": len(dev), "holdout_conversations": len(holdout)},
        "search": search,
        "router_single_turn": router,
        "conversations": conv,
        "grounding_detector": detector,
        "latency": latency,
        "claude_mode": "not measured (needs paid API calls; this benchmark runs fully offline)",
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
    (args.out / "benchmark.md").write_text(render_md(report), encoding="utf-8", newline="\n")
    full = search["+ parsed filters (full)"]["all"]
    print(f"search nDCG@5 {full['ndcg@5']} (legacy {search['legacy keyword scorer (before)']['all']['ndcg@5']}), "
          f"intent dev {conv['dev']['intent_accuracy']} / holdout {conv['holdout']['intent_accuracy']}, "
          f"grounding recall {detector['detection_recall']}, wrote {args.out / 'benchmark.json'}")
    return 0


def render_md(r: dict) -> str:
    s, c, d, lat = r["search"], r["conversations"], r["grounding_detector"], r["latency"]
    lines = [
        "# Benchmark",
        "",
        f"Generated {r['generated']} by `python scripts/benchmark.py` on {r['machine']}. All numbers are measured "
        "offline; nothing here calls a model.",
        "",
        f"## Search ({r['data']['search_queries']} queries, {r['data']['search_catalog_products']} products, "
        f"{r['data']['relevance_labels']} graded labels)",
        "",
        "| System | nDCG@5 | Recall@5 | MRR | Precision of shown top 5 | Zero results |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, v in s.items():
        a = v["all"]
        lines.append(f"| {name} | {a['ndcg@5']:.3f} | {a['recall@5']:.3f} | {a['mrr']:.3f} | "
                     f"{a['precision_of_shown@5']:.3f} | "
                     f"{a['zero_result_rate']:.1%} |")
    groups = list(next(iter(s.values()))["groups"])
    lines += ["", "nDCG@5 by query group:", "", "| System | " + " | ".join(groups) + " |",
              "|---|" + "---:|" * len(groups)]
    for name, v in s.items():
        lines.append(f"| {name} | " + " | ".join(f"{v['groups'][g]['ndcg@5']:.3f}" for g in groups) + " |")
    lines += ["", "Queries per group: " + ", ".join(f"{g} {next(iter(s.values()))['groups'][g]['queries']}"
                                                     for g in groups), ""]
    lines += ["## Conversations (offline mode: router + tools + templated answers)", "",
              "| | Dev set | Held-out set |", "|---|---:|---:|"]
    rows = [("Conversations / turns", lambda x: f"{x['conversations']} / {x['turns']}"),
            ("Intent accuracy", lambda x: f"{x['intent_accuracy']:.1%}"),
            ("Tool choice accuracy", lambda x: f"{x['tool_choice_accuracy']:.1%}"),
            ("Tool call exact match (tool + expected args)", lambda x: f"{x['tool_call_exact_match']:.1%}"),
            ("Answer checks passed", lambda x: x["answer_checks_passed"]),
            ("Handoff precision / recall", lambda x: f"{x['handoff']['precision']:.2f} / {x['handoff']['recall']:.2f}"),
            ("Handoff priority correct", lambda x: str(x["handoff"]["priority_accuracy"])),
            ("Numbers checked by the grounding guard", lambda x: str(x["grounding"]["numbers_checked"])),
            ("Ungrounded numbers in final answers", lambda x: str(x["grounding"]["violations_in_final_answers"]))]
    for label, fn in rows:
        lines.append(f"| {label} | {fn(c['dev'])} | {fn(c['holdout'])} |")
    lines += ["", "The dev set was used while building the router (two router bugs it exposed were fixed). The "
              "held-out set was written afterwards and scored once, without changes. Held-out misses:", ""]
    lines += [f"- `{f['conv']}` \"{f['user']}\": expected {f['expected']}, got {f['got']}"
              for f in c["holdout"]["failures"]] or ["- none"]
    lines += ["", "## Grounding guard", "",
              f"Each answer from both sets had one price, stock, duration or percentage changed to a value no tool "
              f"result contains. The guard caught {d['detection_recall']:.0%} of {d['perturbed_answers']} perturbed "
              f"answers (by kind: {', '.join(f'{k} {v}' for k, v in d['by_kind'].items())}) and flagged "
              f"{d['false_positive_rate']:.0%} of the {d['clean_answers']} untouched answers.", "",
              "## Latency", "", "| Operation | p50 | p95 | n |", "|---|---:|---:|---:|"]
    for key in ("router", "search_demo_store_12_products", "search_120_products", "full_turn_demo_mode"):
        v = lat[key]
        lines.append(f"| {key.replace('_', ' ')} | {v['p50_ms']:.2f} ms | {v['p95_ms']:.2f} ms | {v['n']} |")
    lines += ["", f"Index build for 120 products: {lat['index_build_120_products_ms']:.1f} ms. A full turn in demo "
              "mode includes the SQLite write; in Claude mode the model call dominates.", "",
              "## Claude mode", "", r["claude_mode"] + ".", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
