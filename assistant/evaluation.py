"""
Offline evaluation: search quality, intent routing, end-to-end tool calls, handoffs and grounding.

Used by scripts/benchmark.py (the numbers in examples/benchmark.json) and by the tests. Nothing here
calls a model or the network: the conversations run through ShopAssistant with the DemoClient, which
plans with the deterministic router.
"""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .agent import ShopAssistant
from .catalog import DemoStore
from .demo import DemoClient
from .grounding import check, extract_numbers, sources_from_history
from .router import DialogueState, ProductMatcher, route
from .search import SearchEngine, SearchOptions, parse_query
from .storage import Storage

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- ranking metrics

def dcg(gains: list[int]) -> float:
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at(ranked: list[str], qrels: dict[str, int], k: int = 5) -> float:
    ideal = dcg(sorted(qrels.values(), reverse=True)[:k])
    return dcg([qrels.get(h, 0) for h in ranked[:k]]) / ideal if ideal else 0.0


def recall_at(ranked: list[str], qrels: dict[str, int], k: int = 5) -> float:
    """Capped recall: found relevant / min(k, all relevant), so a perfect top-5 scores 1 even with 20 relevant."""
    relevant = {h for h, g in qrels.items() if g > 0}
    if not relevant:
        return 0.0
    return len(relevant & set(ranked[:k])) / min(k, len(relevant))


def shown_precision(ranked: list[str], qrels: dict[str, int], k: int = 5) -> float | None:
    """Of the results actually shown (top k), the share that is relevant. None when nothing is shown."""
    shown = ranked[:k]
    return sum(qrels.get(h, 0) > 0 for h in shown) / len(shown) if shown else None


def mrr(ranked: list[str], qrels: dict[str, int]) -> float:
    for i, h in enumerate(ranked):
        if qrels.get(h, 0) > 0:
            return 1 / (i + 1)
    return 0.0


# ---------------------------------------------------------------- the previous scorer, kept as the baseline

LEGACY_SYNONYMS = {
    "яке": "jacket", "якета": "jacket", "пухено": "down", "водоустойчиво": "waterproof", "топло": "warm",
    "зима": "winter", "полар": "fleece", "термо": "thermal", "вълна": "wool", "мерино": "merino",
    "обувки": "shoes", "боти": "boots", "туристически": "hiking", "бягане": "running", "раница": "backpack",
    "бутилка": "bottle", "термос": "thermos", "шапка": "beanie", "челник": "headlamp", "щеки": "poles",
    "палатка": "camping", "trainers": "running", "torch": "headlamp", "rucksack": "backpack", "puffer": "down",
    "raincoat": "rain", "coat": "jacket", "hat": "beanie", "flask": "bottle",
}
LEGACY_STOPWORDS = {"i", "a", "an", "the", "for", "and", "or", "with", "me", "my", "need", "want", "looking",
                    "some", "something", "do", "you", "have", "any", "under", "below", "up", "to", "max", "eur",
                    "търся", "искам", "за", "и", "с", "на", "в", "до", "под", "имате", "ли", "нещо", "лв"}


def _legacy_words(text: str) -> list[str]:
    return re.findall(r"[\w']+", unicodedata.normalize("NFKC", text or "").lower())


def legacy_search(products: list[dict], query: str, max_price: float | None = None, limit: int = 6) -> list[str]:
    """The keyword scorer this project used before assistant/search.py (title 3, tags 2, body 1 per word)."""
    terms = set()
    for word in _legacy_words(query):
        if word.isdigit() or word in LEGACY_STOPWORDS:
            continue
        terms.add(word)
        if word in LEGACY_SYNONYMS:
            terms.update(_legacy_words(LEGACY_SYNONYMS[word]))
        if word.endswith("s") and len(word) > 3:
            terms.add(word[:-1])
    scored = []
    for p in products:
        if max_price is not None and p["price"] > max_price:
            continue
        title = set(_legacy_words(p["title"]))
        tags = set(_legacy_words(" ".join(p["tags"] + [p["category"]])))
        body = set(_legacy_words(p["description"]))
        score = sum(3 * (t in title) + 2 * (t in tags) + (t in body) for t in terms)
        if terms and not score:
            continue
        scored.append((score, p["rating"], p["handle"]))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    if scored and terms:
        best = scored[0][0]
        scored = [s for s in scored if s[0] >= best * 0.6]
    return [s[2] for s in scored[:limit]]


# ---------------------------------------------------------------- search evaluation

SEARCH_SYSTEMS = {
    "legacy keyword scorer (before)": None,
    "BM25F only": SearchOptions(synonyms=False, typos=False, parse_filters=False, coverage=False),
    "+ coverage cut": SearchOptions(synonyms=False, typos=False, parse_filters=False),
    "+ synonyms & BG translations": SearchOptions(typos=False, parse_filters=False),
    "+ typo tolerance": SearchOptions(parse_filters=False),
    "+ parsed filters (full)": SearchOptions(),
    "full, without the coverage cut": SearchOptions(coverage=False),
}


def evaluate_search(products: list[dict], queries: list[dict], k: int = 5, limit: int = 10) -> dict:
    """nDCG@k, capped recall@k, MRR and zero-result rate for each system, overall and per query group."""
    out = {}
    for name, options in SEARCH_SYSTEMS.items():
        engine = SearchEngine(products, options) if options else None
        rows = []
        for q in queries:
            if engine:
                ranked = [h.product["handle"] for h in engine.search(q["query"], limit=limit).hits]
            else:
                ranked = legacy_search(products, q["query"], parse_query(q["query"]).max_price, limit)
            rows.append({"group": q["group"], "ndcg": ndcg_at(ranked, q["qrels"], k),
                         "recall": recall_at(ranked, q["qrels"], k), "mrr": mrr(ranked, q["qrels"]),
                         "precision": shown_precision(ranked, q["qrels"], k), "zero": not ranked})
        out[name] = {"all": _summarise(rows), "groups": {g: _summarise([r for r in rows if r["group"] == g])
                                                          for g in dict.fromkeys(r["group"] for r in rows)}}
    return out


def _summarise(rows: list[dict]) -> dict:
    n = len(rows) or 1
    shown = [r["precision"] for r in rows if r["precision"] is not None]
    return {"queries": len(rows), "ndcg@5": round(sum(r["ndcg"] for r in rows) / n, 3),
            "recall@5": round(sum(r["recall"] for r in rows) / n, 3), "mrr": round(sum(r["mrr"] for r in rows) / n, 3),
            "precision_of_shown@5": round(sum(shown) / len(shown), 3) if shown else None,
            "zero_result_rate": round(sum(r["zero"] for r in rows) / n, 3)}


# ---------------------------------------------------------------- conversations

@dataclass
class TurnOutcome:
    conv: str
    expected: dict
    intent: str | None = None
    tool: str | None = None
    args: dict = field(default_factory=dict)
    answer: str = ""
    handoff: dict | None = None
    grounding: dict = field(default_factory=dict)
    seconds: float = 0.0
    sources: list[str] = field(default_factory=list)


def run_conversations(conversations: list[dict], store: DemoStore | None = None) -> list[TurnOutcome]:
    """Plays every conversation through ShopAssistant + DemoClient and records what happened on each turn."""
    store = store or DemoStore()
    storage = Storage()
    client = DemoClient(store, word_delay=0)
    assistant = ShopAssistant(client, store, storage, model="demo")
    outcomes = []
    try:
        for conv in conversations:
            cid = storage.create_conversation()
            for turn in conv["turns"]:
                o = TurnOutcome(conv=conv["id"], expected=turn)
                start = time.perf_counter()
                for event in assistant.respond(cid, turn["user"], turn.get("page")):
                    if event["type"] == "done":
                        result = event["result"]
                        o.answer, o.handoff, o.grounding = result.answer, result.handoff, result.grounding
                o.seconds = time.perf_counter() - start
                o.intent = client.last_route.intent if client.last_route else None
                history = storage.get_conversation(cid)["history"]
                calls = _turn_tool_calls(history, turn["user"])
                if calls:
                    o.tool, o.args = calls[0]["name"], calls[0]["input"]
                o.sources = sources_from_history(history)
                outcomes.append(o)
    finally:
        storage.close()
    return outcomes


def _turn_tool_calls(history: list, user_text: str) -> list[dict]:
    """The tool calls made after the last shopper message with this text."""
    start = 0
    for i, m in enumerate(history):
        if m["role"] == "user" and any(b.get("type") == "text" and b.get("text") == user_text
                                       for b in m["content"] if isinstance(b, dict)):
            start = i
    return [b for m in history[start:] if m["role"] == "assistant" for b in m["content"]
            if b.get("type") == "tool_use"]


def _norm(key: str, value) -> str:
    """"EU 42" and "42" are the same shoe size to the search engine."""
    value = str(value or "").lower()
    return re.sub(r"^eu\s*", "", value) if key == "size" else value


def _args_match(expected: dict, actual: dict) -> bool:
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, float | int) and not isinstance(want, bool):
            if got is None or abs(float(got) - want) > 1e-6:
                return False
        elif isinstance(want, str):
            if _norm(key, got) != _norm(key, want):
                return False
        elif got != want:
            return False
    return True


def score_conversations(outcomes: list[TurnOutcome]) -> dict:
    n = len(outcomes)
    intent_ok = sum(o.intent == o.expected["intent"] for o in outcomes)
    tool_ok = sum(o.tool == o.expected.get("tool") for o in outcomes)
    exact = sum(o.tool == o.expected.get("tool") and _args_match(o.expected.get("args") or {}, o.args)
                for o in outcomes)
    with_answer_checks = [o for o in outcomes if o.expected.get("answer_contains")]
    answer_ok = sum(all(s.lower() in o.answer.lower() for s in o.expected["answer_contains"])
                    for o in with_answer_checks)
    want = [bool(o.expected.get("handoff")) for o in outcomes]
    got = [o.handoff is not None for o in outcomes]
    tp = sum(w and g for w, g in zip(want, got, strict=True))
    fp = sum(g and not w for w, g in zip(want, got, strict=True))
    fn = sum(w and not g for w, g in zip(want, got, strict=True))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    prio = [o for o in outcomes if o.expected.get("handoff") and o.handoff]
    prio_ok = sum(o.handoff["priority"] == o.expected.get("priority", "normal") for o in prio)
    checked = sum(o.grounding.get("checked", 0) for o in outcomes)
    violations = sum(len(o.grounding.get("violations", [])) for o in outcomes)
    per_intent = {}
    for intent in dict.fromkeys(o.expected["intent"] for o in outcomes):
        rows = [o for o in outcomes if o.expected["intent"] == intent]
        per_intent[intent] = {"turns": len(rows),
                              "accuracy": round(sum(o.intent == intent for o in rows) / len(rows), 3)}
    failures = [{"conv": o.conv, "user": o.expected["user"], "expected": o.expected["intent"], "got": o.intent,
                 "expected_tool": o.expected.get("tool"), "tool": o.tool}
                for o in outcomes if o.intent != o.expected["intent"] or o.tool != o.expected.get("tool")]
    return {
        "conversations": len({o.conv for o in outcomes}), "turns": n,
        "intent_accuracy": round(intent_ok / n, 3), "per_intent": per_intent,
        "tool_choice_accuracy": round(tool_ok / n, 3), "tool_call_exact_match": round(exact / n, 3),
        "answer_checks_passed": f"{answer_ok}/{len(with_answer_checks)}",
        "answer_check_rate": round(answer_ok / max(1, len(with_answer_checks)), 3),
        "handoff": {"precision": round(precision, 3), "recall": round(recall, 3),
                    "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
                    "expected": sum(want), "opened": sum(got),
                    "priority_accuracy": round(prio_ok / len(prio), 3) if prio else None},
        "grounding": {"numbers_checked": checked, "violations_in_final_answers": violations},
        "failures": failures,
    }


def router_only_accuracy(conversations: list[dict], store: DemoStore | None = None) -> dict:
    """Intent accuracy of the router on single-turn conversations, without running tools."""
    store = store or DemoStore()
    matcher = ProductMatcher(store.all_products())
    rows = [(c["turns"][0], route(DialogueState([(c["turns"][0]["user"], _handle(c["turns"][0]))]), matcher))
            for c in conversations if len(c["turns"]) == 1]
    ok = sum(r.intent == t["intent"] for t, r in rows)
    return {"turns": len(rows), "intent_accuracy": round(ok / max(1, len(rows)), 3)}


def _handle(turn: dict) -> str | None:
    page = turn.get("page") or ""
    return page.rsplit("/", 1)[-1] if page.startswith("/products/") else None


# ---------------------------------------------------------------- grounding detector

def perturb(answer: str, rng) -> tuple[str, str] | None:
    """The answer with one number changed to a value no source contains, or None if it has no checked number."""
    mentions = [m for m in extract_numbers(answer) if m.kind in ("price", "stock", "duration", "percent")]
    if not mentions:
        return None
    m = rng.choice(mentions)
    new_value = round(m.value * rng.choice([1.1, 0.8, 1.5]) + rng.choice([3, 7, 11]))
    return answer[:m.start] + str(new_value) + answer[m.start + len(m.text):], m.kind


def evaluate_grounding_detector(outcomes: list[TurnOutcome], seed: int = 7) -> dict:
    """Recall on answers with one invented figure, and false positives on the untouched answers."""
    import random
    rng = random.Random(seed)
    clean = [o for o in outcomes if o.answer]
    false_pos = sum(not check(o.answer, o.sources).ok for o in clean)
    perturbed = caught = 0
    by_kind: dict[str, list[int]] = {}
    for o in clean:
        changed = perturb(o.answer, rng)
        if not changed:
            continue
        text, kind = changed
        hit = not check(text, o.sources).ok
        perturbed += 1
        caught += hit
        by_kind.setdefault(kind, []).append(hit)
    return {"clean_answers": len(clean), "false_positive_rate": round(false_pos / max(1, len(clean)), 3),
            "perturbed_answers": perturbed, "detection_recall": round(caught / max(1, perturbed), 3),
            "by_kind": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_kind.items())}}
