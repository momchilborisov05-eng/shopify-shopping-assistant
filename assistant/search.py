"""
Product search written by hand: query parsing, BM25F ranking, synonyms and typo tolerance.

A shopper types "waterprof jaket in M under €200, blue". This module turns that into

- text terms: ``waterproof`` (typo fixed), ``jacket`` (typo fixed, plus its synonyms ``shell``, ``parka``...)
- hard filters parsed out of the words: size ``M``, colour family ``blue``, price ``<= 200``

and ranks the catalogue with BM25F over three weighted fields (title, tags + category, description +
features + materials). Filters are applied per variant, so "blue in M" needs one variant that is both.

No search library is used. The pieces, all in this file:

- ``tokenize``: Unicode-aware tokenizer (Latin and Cyrillic), stopwords, a small plural stemmer.
- ``SYNONYM_GROUPS`` / ``TRANSLATIONS``: English equivalence classes and Bulgarian-to-English words.
- ``TypoIndex``: a SymSpell-style deletion index over the catalogue vocabulary; candidates are verified
  with the optimal-string-alignment (Damerau-Levenshtein) distance.
- ``parse_query``: price ranges, sizes (letter and EU), colours (EN/BG), "in stock".
- ``SearchEngine``: the inverted index with per-field term frequencies and BM25F scoring, a coverage
  rule (a product must match most of the query's concepts) and variant-level filters.

Everything is deterministic and runs in well under a millisecond per query on the demo catalogue.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

# ---------- text ----------

STOPWORDS = frozenset("""
i a an the for and or with me my need want looking look search some something do you have any up to of in on
is it this that can please find show get buy which what good best new one ones also very really just like
would could should will am are be been was were your our we us they them it's i'm im
търся искам трябва ми за и с на в до под имате ли някакво някаква някакви нещо моля един една едно ще
може да по от са е се аз ние вие тези това този тази които какво кой коя кое
""".split())

# Words that only carry filters or currency; parse_query reads them, the ranker ignores them.
FILTER_WORDS = frozenset("""
eur euro euros € usd $ лв лева евро size sizes eu uk us размер размера номер under below less than
cheaper max maximum over above more from between price priced costs cost budget stock available
availability now налични наличен налично наличност цена струва
""".split())


def fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def stem(word: str) -> str:
    """A deliberately small English plural stemmer: boots -> boot, batteries -> battery, gloves -> glove."""
    if len(word) <= 3 or not word.isascii() or not word.isalpha():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def raw_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:'[^\W_]+)?", fold(text))


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    out = []
    for word in raw_words(text):
        if not keep_stopwords and (word in STOPWORDS or word in FILTER_WORDS):
            continue
        out.append(stem(word))
    return out


# ---------- synonyms ----------

# Each group is an equivalence class: a query word matches the others at SYNONYM_WEIGHT.
SYNONYM_GROUPS: list[set[str]] = [
    {"jacket", "coat", "shell", "parka", "anorak", "hardshell"},
    {"waterproof", "rainproof", "stormproof", "watertight"},
    {"rain", "raincoat", "rainjacket"},
    {"down", "puffer", "puffy"},
    {"insulated", "insulation", "warm", "thermal"},
    {"lightweight", "ultralight", "featherweight", "light"},
    {"packable", "compressible", "stuffable"},
    {"backpack", "rucksack", "pack", "daypack", "knapsack"},
    {"boot", "boots"},
    {"shoe", "trainer", "sneaker", "runner"},
    {"headlamp", "headtorch", "torch", "flashlight", "lamp"},
    {"bottle", "flask", "thermos"},
    {"beanie", "hat", "toque"},
    {"glove", "mitt", "mitten"},
    {"pant", "trouser"},
    {"fleece", "pullover"},
    {"tent", "shelter"},
    {"pole", "stick"},
    {"base", "baselayer", "thermal"},
    {"hiking", "hike", "trekking", "trek", "walking"},
    {"running", "run", "jogging"},
    {"winter", "cold", "snow"},
    {"sleeping", "sleep"},
    {"bag", "quilt"},
    {"windproof", "windbreaker", "windstopper"},
]

# Bulgarian shopper words -> the catalogue's English words (one way; the catalogue is in English).
TRANSLATIONS: dict[str, tuple[str, ...]] = {
    "яке": ("jacket",), "якета": ("jacket",), "якето": ("jacket",), "шуба": ("jacket",), "парка": ("parka",),
    "пухено": ("down",), "пухенка": ("down", "jacket"),
    "дъжд": ("rain",), "дъждобран": ("rain", "jacket"), "водоустойчиво": ("waterproof",),
    "водоустойчиви": ("waterproof",), "водоустойчив": ("waterproof",), "водоустойчива": ("waterproof",),
    "непромокаемо": ("waterproof",), "непромокаеми": ("waterproof",), "топло": ("warm",), "топла": ("warm",),
    "топли": ("warm",), "зима": ("winter",), "зимно": ("winter",), "зимни": ("winter",), "зимна": ("winter",),
    "студ": ("cold",), "поларено": ("fleece",), "полар": ("fleece",), "блуза": ("midlayer",),
    "термо": ("thermal",), "термобельо": ("base", "layer"), "бельо": ("base", "layer"), "вълна": ("wool",),
    "вълнена": ("wool",), "мерино": ("merino",), "обувки": ("shoe",), "обувка": ("shoe",), "боти": ("boot",),
    "туристически": ("hiking",), "бягане": ("running",), "маратонки": ("trainer", "running"),
    "раница": ("backpack",), "раници": ("backpack",), "чанта": ("bag",), "лаптоп": ("laptop",),
    "бутилка": ("bottle",), "термос": ("thermos",), "шапка": ("beanie",), "шапки": ("beanie",),
    "челник": ("headlamp",), "фенер": ("headlamp",), "фенерче": ("headlamp",), "щеки": ("pole",),
    "щека": ("pole",), "подарък": ("gift",), "палатка": ("tent",), "палатки": ("tent",),
    "къмпинг": ("camping",), "планина": ("mountain",), "преход": ("hiking",), "спален": ("sleeping",),
    "чувал": ("bag",), "ръкавици": ("glove",), "ръкавица": ("glove",), "панталон": ("pant",),
    "панталони": ("pant",), "леко": ("lightweight",), "лека": ("lightweight",), "леки": ("lightweight",),
    "ветровка": ("windproof", "jacket"), "чорапи": ("sock",), "шорти": ("short",), "постелка": ("mat",),
    "котлон": ("stove",), "очила": ("sunglasses",),
}

SYNONYM_WEIGHT = 0.7
TYPO_WEIGHT = 0.8


def _synonym_map() -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for group in SYNONYM_GROUPS:
        stems = {stem(w) for w in group}
        for w in stems:
            out[w] |= stems - {w}
    return out


SYNONYMS = _synonym_map()

# ---------- query attributes ----------

LETTER_SIZES = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL")
COLOUR_FAMILIES: dict[str, tuple[str, ...]] = {
    "black": ("black", "jet", "onyx", "черен", "черно", "черна", "черни"),
    "white": ("white", "snow", "ivory", "бял", "бяло", "бяла", "бели"),
    "grey": ("grey", "gray", "graphite", "charcoal", "slate", "steel", "silver", "сив", "сиво", "сива", "сиви"),
    "blue": ("blue", "navy", "ocean", "cobalt", "azure", "denim", "teal", "син", "синьо", "синя", "сини"),
    "green": ("green", "forest", "olive", "moss", "sage", "pine", "khaki", "зелен", "зелено", "зелена", "зелени"),
    "red": ("red", "crimson", "scarlet", "rust", "burgundy", "червен", "червено", "червена", "червени"),
    "orange": ("orange", "ember", "flame", "оранжев", "оранжево", "оранжева"),
    "yellow": ("yellow", "mustard", "lemon", "жълт", "жълто", "жълта"),
    "purple": ("purple", "violet", "plum", "лилав", "лилаво", "лилава"),
    "pink": ("pink", "rose", "coral", "розов", "розово", "розова"),
    "brown": ("brown", "tan", "chocolate", "coyote", "кафяв", "кафяво", "кафява"),
    "beige": ("beige", "sand", "stone", "bone", "бежов", "бежово", "бежова"),
}
_COLOUR_WORD = {w: fam for fam, words in COLOUR_FAMILIES.items() for w in words}
# Words that name a colour only inside a variant name ("Steel", "Stone") are too ambiguous to read as a
# colour request in a query ("stainless steel bottle"), so queries use the plain colour names only.
_QUERY_COLOUR = {w: fam for w, fam in _COLOUR_WORD.items()
                 if w not in ("steel", "stone", "snow", "silver", "slate", "rust", "forest", "ocean", "pine",
                              "sand", "bone", "flame", "ember", "rose", "lemon", "jet", "denim")}

NUM = r"(\d{1,5}(?:[.,]\d{1,2})?)"
CUR = r"(?:€|eur|euro|euros|евро|лв\.?|лева|\$|usd)"
RANGE = re.compile(rf"(?:between|от|from)\s*{CUR}?\s*{NUM}\s*{CUR}?\s*(?:and|to|до|-|–)\s*{CUR}?\s*{NUM}\s*{CUR}?",
                   re.I)
DASH_RANGE = re.compile(rf"{CUR}\s*{NUM}\s*(?:-|–)\s*{CUR}?\s*{NUM}|{NUM}\s*(?:-|–)\s*{NUM}\s*{CUR}", re.I)
MAX_PRICE = re.compile(rf"(?:under|below|less than|cheaper than|up to|max(?:imum)?|at most|<|<=|до|под|"
                       rf"не повече от|по-евтин(?:о|а|и)? от)\s*{CUR}?\s*{NUM}\s*{CUR}?", re.I)
MIN_PRICE = re.compile(rf"(?:over|above|more than|at least|from|>|>=|над|поне)\s*{CUR}?\s*{NUM}\s*{CUR}?", re.I)
BARE_PRICE = re.compile(rf"{CUR}\s*{NUM}|{NUM}\s*{CUR}", re.I)
SIZE_EXPLICIT = re.compile(r"(?:\b(?:size|sz)|размер|номер|№)\s*:?\s*(?:eu\s*)?"
                           r"(xxs|xs|s|m|l|xl|xxl|3xl|\d{2}(?:\.5)?)\b"
                           r"|\beu\s*(\d{2}(?:\.5)?)\b", re.I)
SIZE_IN = re.compile(r"\b(?:in|в)\s+(?:a\s+|an\s+)?(xxs|xs|s|m|l|xl|xxl|3xl)\b", re.I)
SIZE_BARE_LETTER = re.compile(r"(?<![\w'])(xxs|xs|xl|xxl|3xl)(?![\w'])", re.I)
SIZE_BARE_EU = re.compile(r"(?<![\w.,€$])(3[5-9]|4[0-9])(?![\w.,%€]|\s*(?:€|eur|euro|лв|l\b|л\b|litre|liter))", re.I)
IN_STOCK = re.compile(r"\b(?:in stock|available now|available today|ready to ship)\b|наличн|в наличност", re.I)
SIZED_THINGS = ("boot", "shoe", "trainer", "sneaker", "runner", "sandal", "footwear", "обувк", "боти", "маратонк")


@dataclass
class ParsedQuery:
    text: str
    terms: list[str]
    min_price: float | None = None
    max_price: float | None = None
    size: str | None = None
    colour: str | None = None
    in_stock: bool = False
    rest: str = ""  # the text with the filter phrases cut out

    def filters(self) -> dict:
        return {k: v for k, v in (("min_price", self.min_price), ("max_price", self.max_price), ("size", self.size),
                                  ("colour", self.colour), ("in_stock", self.in_stock or None)) if v is not None}


def _num(value: str) -> float:
    return float(value.replace(",", "."))


def normalize_size(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().upper().removeprefix("EU").strip()
    return value or None


def colour_family(name: str | None) -> str | None:
    """'Forest Green' -> green, 'Ocean Blue' -> blue, 'Steel' -> grey. None when it isn't a colour."""
    for word in raw_words(name or ""):
        if word in _COLOUR_WORD:
            return _COLOUR_WORD[word]
    return None


def parse_query(text: str) -> ParsedQuery:
    """Pulls price, size, colour and stock filters out of a shopper's words; the rest are search terms."""
    low = fold(text)
    rest = low
    q = ParsedQuery(text=text, terms=[])

    def cut(match: re.Match):
        nonlocal rest
        rest = rest.replace(match.group(0), " ", 1)

    m = RANGE.search(rest) or DASH_RANGE.search(rest)
    if m:
        lo, hi = sorted(_num(g) for g in m.groups() if g)
        q.min_price, q.max_price = lo, hi
        cut(m)
    else:
        m = MAX_PRICE.search(rest)
        if m:
            q.max_price = _num(m.group(1))
            cut(m)
        m = MIN_PRICE.search(rest)
        if m:
            q.min_price = _num(m.group(1))
            cut(m)
        if q.max_price is None and q.min_price is None:
            m = BARE_PRICE.search(rest)
            if m:  # "a €100 jacket": treat a lone price as the budget
                q.max_price = _num(next(g for g in m.groups() if g))
                cut(m)

    m = SIZE_EXPLICIT.search(rest) or SIZE_IN.search(rest) or SIZE_BARE_LETTER.search(rest)
    if m:
        q.size = normalize_size(next(g for g in m.groups() if g))
        cut(m)
    elif any(t in rest for t in SIZED_THINGS):
        m = SIZE_BARE_EU.search(rest)
        if m:
            q.size = m.group(1)
            cut(m)

    for word in raw_words(rest):
        if word in _QUERY_COLOUR:
            q.colour = _QUERY_COLOUR[word]
            rest = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", rest, count=1)
            break

    m = IN_STOCK.search(rest)
    if m:
        q.in_stock = True
        cut(m)

    q.rest = rest
    q.terms = tokenize(rest)
    return q


# ---------- typo tolerance ----------


def osa_distance(a: str, b: str, limit: int = 3) -> int:
    """Optimal string alignment distance (Levenshtein plus adjacent transpositions), cut off at `limit`."""
    if abs(len(a) - len(b)) >= limit:
        return limit
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best >= limit:
            return limit
        prev2, prev = prev, cur
    return min(prev[-1], limit)


def max_edits(word: str) -> int:
    """How many typos a word of this length may carry: none under 4 letters, 1 up to 7, 2 from 8."""
    if len(word) < 4 or not word.isalpha():
        return 0
    return 1 if len(word) < 8 else 2


def _deletes(word: str, depth: int) -> set[str]:
    out = {word}
    frontier = {word}
    for _ in range(depth):
        nxt = set()
        for w in frontier:
            for i in range(len(w)):
                nxt.add(w[:i] + w[i + 1:])
        out |= nxt
        frontier = nxt
    return out


class TypoIndex:
    """SymSpell-style: every vocabulary word is stored under all its 1- and 2-letter deletions, so a
    misspelled query word finds its candidates by looking up its own deletions (no vocabulary scan)."""

    def __init__(self, vocabulary: dict[str, int]):
        self.vocabulary = vocabulary  # word -> document frequency
        self._index: dict[str, set[str]] = defaultdict(set)
        for word in vocabulary:
            if max_edits(word):
                for d in _deletes(word, 2):
                    self._index[d].add(word)

    def correct(self, word: str) -> list[str]:
        """The closest vocabulary words within the allowed distance, most frequent first; [] if none."""
        edits = max_edits(word)
        if not edits or word in self.vocabulary:
            return []
        candidates: set[str] = set()
        for d in _deletes(word, edits):
            candidates |= self._index.get(d, set())
        scored = []
        for c in candidates:
            if c[0] != word[0] and edits == 1:  # one typo in a short word: the first letter is usually right
                continue
            dist = osa_distance(word, c, edits + 1)
            if dist <= edits:
                scored.append((dist, -self.vocabulary[c], c))
        if not scored:
            return []
        scored.sort()
        best = scored[0][0]
        return [c for d, _, c in scored if d == best][:2]


# ---------- the index ----------

FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "body": 1.0}
FIELD_B = {"title": 0.3, "tags": 0.5, "body": 0.75}
K1 = 1.2
MIN_COVERAGE = 0.6  # a product must match at least 60 % of the query's concepts


@dataclass
class SearchOptions:
    synonyms: bool = True
    typos: bool = True
    parse_filters: bool = True
    coverage: bool = True


@dataclass
class Hit:
    product: dict
    score: float
    coverage: float
    matched: list[str]
    variants: list[dict] = field(default_factory=list)  # the variants that satisfy the size/colour filter


@dataclass
class SearchResult:
    hits: list[Hit]
    parsed: ParsedQuery
    expansions: dict[str, list[str]]  # query term -> the terms it also matched (synonyms, typo fixes)
    corrections: dict[str, str]


def _variant_size(variant: dict) -> str | None:
    opts = variant.get("options") or {}
    return normalize_size(opts.get("Size") or opts.get("size"))


def _variant_colour(variant: dict) -> str | None:
    opts = variant.get("options") or {}
    return colour_family(opts.get("Color") or opts.get("Colour") or opts.get("color"))


class SearchEngine:
    """BM25F over the catalogue's products (DemoStore's JSON shape)."""

    def __init__(self, products: list[dict], options: SearchOptions | None = None):
        self.options = options or SearchOptions()
        self.products = list(products)
        self.n = len(self.products)
        # term -> {doc index: {field: tf}}
        self.postings: dict[str, dict[int, dict[str, int]]] = defaultdict(dict)
        self.lengths: list[dict[str, int]] = []
        for i, p in enumerate(self.products):
            fields = {
                "title": tokenize(p.get("title", "")),
                "tags": tokenize(" ".join(list(p.get("tags") or []) + [p.get("category", "")])),
                "body": tokenize(" ".join([p.get("description", ""), " ".join(p.get("features") or []),
                                           p.get("materials", "") or ""])),
            }
            self.lengths.append({f: len(ts) for f, ts in fields.items()})
            for f, ts in fields.items():
                for t in ts:
                    entry = self.postings[t].setdefault(i, {})
                    entry[f] = entry.get(f, 0) + 1
        self.avg = {f: (sum(L[f] for L in self.lengths) / self.n if self.n else 1.0) or 1.0 for f in FIELD_WEIGHTS}
        self.df = {t: len(docs) for t, docs in self.postings.items()}
        self.typos = TypoIndex(self.df)

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def _bm25f(self, term: str, doc: int) -> float:
        tfs = self.postings.get(term, {}).get(doc)
        if not tfs:
            return 0.0
        tf = 0.0
        for f, n in tfs.items():
            norm = 1 - FIELD_B[f] + FIELD_B[f] * self.lengths[doc][f] / self.avg[f]
            tf += FIELD_WEIGHTS[f] * n / norm
        return self.idf(term) * tf / (K1 + tf)

    def merge_compounds(self, terms: list[str]) -> list[str]:
        """'head torch' -> 'headtorch', 'day pack' -> 'daypack' when the joined word is a known term."""
        out: list[str] = []
        i = 0
        while i < len(terms):
            if i + 1 < len(terms):
                joined = stem(terms[i] + terms[i + 1])
                if joined in self.df or (self.options.synonyms and joined in SYNONYMS):
                    out.append(joined)
                    i += 2
                    continue
            out.append(terms[i])
            i += 1
        return out

    def expand(self, terms: list[str]) -> tuple[list[list[tuple[str, float]]], dict, dict]:
        """One concept per query word: [(term, weight), ...] with the word itself, synonyms and typo fixes."""
        concepts, expansions, corrections = [], {}, {}
        for word in terms:
            variants: dict[str, float] = {}
            base = list(TRANSLATIONS.get(word, ())) if self.options.synonyms else []
            for t in base or [word]:
                t = stem(t)
                variants[t] = 1.0
                if t not in self.df and self.options.typos:
                    for fixed in self.typos.correct(t):
                        variants.setdefault(fixed, TYPO_WEIGHT)
                        corrections[word] = fixed
            if self.options.synonyms:
                for t, w in list(variants.items()):
                    for syn in SYNONYMS.get(t, ()):
                        variants.setdefault(syn, w * SYNONYM_WEIGHT)
            extra = [t for t in variants if t != word]
            if extra:
                expansions[word] = extra
            concepts.append(sorted(variants.items(), key=lambda kv: -kv[1]))
        return concepts, expansions, corrections

    def _variant_filter(self, product: dict, size: str | None, colour: str | None,
                        in_stock: bool) -> tuple[bool, list[dict]]:
        variants = product.get("variants") or []
        if not (size or colour):
            ok = (not in_stock) or any(v.get("inventory", 0) > 0 for v in variants)
            return ok, []
        matching = [v for v in variants
                    if (not size or _variant_size(v) == size) and (not colour or _variant_colour(v) == colour)]
        if in_stock:
            matching = [v for v in matching if v.get("inventory", 0) > 0]
        return bool(matching), matching

    def search(self, query: str, *, category: str | None = None, min_price: float | None = None,
               max_price: float | None = None, size: str | None = None, colour: str | None = None,
               in_stock: bool = False, limit: int = 6) -> SearchResult:
        parsed = parse_query(query) if self.options.parse_filters else ParsedQuery(query, tokenize(query))
        # Explicit arguments (from the model or the router) win over what was parsed from the words.
        min_price = min_price if min_price is not None else parsed.min_price
        max_price = max_price if max_price is not None else parsed.max_price
        size = normalize_size(size) or parsed.size
        colour = (colour_family(colour) or fold(colour)) if colour else parsed.colour
        in_stock = in_stock or parsed.in_stock
        # Repeated words count once: "jacket jacket" is not twice as relevant.
        terms = self.merge_compounds(list(dict.fromkeys(parsed.terms)))
        concepts, expansions, corrections = self.expand(terms)

        hits = []
        for i, p in enumerate(self.products):
            if category and fold(p.get("category", "")) != fold(category):
                continue
            price = p.get("price", 0)
            if max_price is not None and price > max_price + 1e-9:
                continue
            if min_price is not None and price < min_price - 1e-9:
                continue
            ok, variants = self._variant_filter(p, size, colour, in_stock)
            if not ok:
                continue
            score, matched = 0.0, []
            for word, concept in zip(terms, concepts, strict=True):
                best = max((w * self._bm25f(t, i) for t, w in concept), default=0.0)
                if best > 0:
                    score += best
                    matched.append(word)
            coverage = len(matched) / len(terms) if terms else 1.0
            if terms and not matched:
                continue
            if self.options.coverage and coverage < MIN_COVERAGE:
                continue
            stocked = any(v.get("inventory", 0) > 0 for v in (variants or p.get("variants") or []))
            final = score * (0.5 + 0.5 * coverage) * (1.0 if stocked else 0.8)
            hits.append(Hit(p, final, coverage, matched, variants))
        # Ties (and filter-only queries with no words) are broken by rating, then by title for stability.
        hits.sort(key=lambda h: (-round(h.score, 9), -(h.product.get("rating") or 0), h.product.get("title", "")))
        if self.options.coverage and hits and terms:
            best = hits[0].score
            hits = [h for h in hits if h.score >= best * 0.25]
        return SearchResult(hits[:limit], parsed, expansions, corrections)
