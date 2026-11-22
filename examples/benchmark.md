# Benchmark

Generated 2026-11-20 by `python scripts/benchmark.py` on Windows AMD64, Python 3.12.3. All numbers are measured offline; nothing here calls a model.

## Search (58 queries, 120 products, 527 graded labels)

| System | nDCG@5 | Recall@5 | MRR | Precision of shown top 5 | Zero results |
|---|---:|---:|---:|---:|---:|
| legacy keyword scorer (before) | 0.589 | 0.566 | 0.813 | 0.812 | 12.1% |
| BM25F only | 0.570 | 0.558 | 0.680 | 0.772 | 24.1% |
| + coverage cut | 0.429 | 0.392 | 0.560 | 0.915 | 43.1% |
| + synonyms & BG translations | 0.633 | 0.630 | 0.699 | 0.927 | 27.6% |
| + typo tolerance | 0.761 | 0.754 | 0.837 | 0.935 | 13.8% |
| + parsed filters (full) | 0.914 | 0.891 | 1.000 | 0.965 | 0.0% |
| full, without the coverage cut | 0.939 | 0.943 | 1.000 | 0.928 | 0.0% |

nDCG@5 by query group:

| System | plain | synonym | typo | bulgarian | filters | mixed |
|---|---:|---:|---:|---:|---:|---:|
| legacy keyword scorer (before) | 0.765 | 0.627 | 0.232 | 0.366 | 0.608 | 0.835 |
| BM25F only | 0.909 | 0.606 | 0.284 | 0.000 | 0.546 | 0.847 |
| + coverage cut | 0.872 | 0.432 | 0.000 | 0.000 | 0.269 | 0.768 |
| + synonyms & BG translations | 0.920 | 0.878 | 0.000 | 0.882 | 0.229 | 0.853 |
| + typo tolerance | 0.920 | 0.878 | 0.933 | 0.882 | 0.229 | 0.853 |
| + parsed filters (full) | 0.920 | 0.878 | 0.933 | 0.882 | 0.968 | 0.853 |
| full, without the coverage cut | 0.953 | 0.892 | 0.949 | 0.917 | 0.968 | 0.932 |

Queries per group: plain 16, synonym 9, typo 8, bulgarian 8, filters 12, mixed 5

## Conversations (offline mode: router + tools + templated answers)

| | Dev set | Held-out set |
|---|---:|---:|
| Conversations / turns | 67 / 78 | 28 / 30 |
| Intent accuracy | 100.0% | 86.7% |
| Tool choice accuracy | 100.0% | 86.7% |
| Tool call exact match (tool + expected args) | 100.0% | 86.7% |
| Answer checks passed | 57/57 | 9/13 |
| Handoff precision / recall | 1.00 / 1.00 | 1.00 / 0.67 |
| Handoff priority correct | 1.0 | 1.0 |
| Numbers checked by the grounding guard | 232 | 67 |
| Ungrounded numbers in final answers | 0 | 0 |

The dev set was used while building the router (two router bugs it exposed were fixed). The held-out set was written afterwards and scored once, without changes. Held-out misses:

- `ho11` "is the thermo bottle dishwasher safe?": expected product_info, got product_search
- `ho16` "hey, when will my stuff arrive?": expected order_status, got product_search
- `ho19` "The zip on my backpack broke after a week, I want my money back. ivan.dimitrov@example.com": expected handoff, got product_search
- `ho23` "Мерси, това е всичко": expected greeting, got product_search

## Grounding guard

Each answer from both sets had one price, stock, duration or percentage changed to a value no tool result contains. The guard caught 94% of 66 perturbed answers (by kind: duration 15/17, percent 1/1, price 45/47, stock 1/1) and flagged 0% of the 108 untouched answers.

## Latency

| Operation | p50 | p95 | n |
|---|---:|---:|---:|
| router | 2.41 ms | 5.34 ms | 2010 |
| search demo store 12 products | 0.47 ms | 1.16 ms | 1740 |
| search 120 products | 2.19 ms | 5.36 ms | 1740 |
| full turn demo mode | 3.71 ms | 7.16 ms | 108 |

Index build for 120 products: 26.2 ms. A full turn in demo mode includes the SQLite write; in Claude mode the model call dominates.

## Claude mode

not measured (needs paid API calls; this benchmark runs fully offline).
