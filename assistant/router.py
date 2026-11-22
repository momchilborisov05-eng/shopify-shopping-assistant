"""
A deterministic intent router and slot filler: what the shopper wants, and the details needed to do it.

Demo mode (no API key) plans every turn with it, and the evaluation set measures it. It is not a
classifier trained on data; it scores seven intents from cue lexicons (English and Bulgarian), the
conversation's pending question, the page the shopper is on and the catalogue itself, then fills the
intent's slots:

    intent          slots
    product_search  query, min_price, max_price, size, colour, in_stock_only
    product_info    handle, size, colour
    policy          topic
    order_status    order_number, email            (both required before lookup)
    handoff         reason, email, priority        (email required before the ticket)
    greeting        kind: hello | thanks
    out_of_scope    kind: injection | off_topic

``route()`` is a pure function of the dialogue state, so it is cheap to test and to benchmark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .search import STOPWORDS as SEARCH_STOPWORDS
from .search import SYNONYMS, TRANSLATIONS, fold, parse_query, raw_words, stem

INTENTS = ("product_search", "product_info", "policy", "order_status", "handoff", "greeting", "out_of_scope")
# When two intents score the same, the earlier one wins: a complaint beats an order question, and so on.
PRIORITY = ("out_of_scope", "handoff", "order_status", "product_info", "policy", "greeting", "product_search")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ORDER = re.compile(r"(?:#|№|\b)(\d{4,6})\b(?!\s*(?:€|eur|euro|лв|\$|usd|mah|lm|lumen|g\b|ml\b|mm\b))", re.I)
PRICE_WORD_BEFORE = re.compile(r"(?:under|below|over|above|up to|max|до|под|над|€|\$)\s*$", re.I)

HUMAN = ("human", "a person", "real person", "agent", "operator", "someone real", "speak to", "talk to",
         "complain", "complaint", "damaged", "broken", "wrong item", "wrong size was sent", "refund", "cancel",
         "never arrived", "not happy", "unhappy", "човек", "оператор", "служител", "колега", "оплакване",
         "повреден", "повредена", "счупен", "счупена", "сгрешен", "грешен продукт", "възстанов", "откажа",
         "анулир", "жалба", "не пристигна", "недоволен", "недоволна")
# Requests only a person can act on: they outrank an order lookup even when an order number is given.
ACTIONS = ("refund", "cancel", "complain", "complaint", "damaged", "broken", "wrong item", "never arrived",
           "възстанов", "откажа", "анулир", "жалба", "повреден", "повредена", "счупен", "счупена", "сгрешен")
URGENT = ("damaged", "broken", "never arrived", "wrong item", "повреден", "повредена", "счупен", "счупена",
          "не пристигна", "сгрешен")
ORDER_WORDS = ("order", "parcel", "package", "tracking", "track", "where is my", "delivery status", "shipped yet",
               "has it shipped", "поръчка", "поръчката", "пратка", "пратката", "проследяв", "къде е",
               "кога ще пристигне")
POLICY_WORDS = {
    "returns": ("return", "exchange", "send back", "send it back", "връщ", "върна", "замяна", "заменя",
                "размяна"),
    "shipping": ("shipping", "delivery", "deliver", "courier", "speedy", "econt", "ship to", "how long does it take",
                 "доставка", "доставя", "куриер", "еконт", "спиди", "колко време"),
    "payment": ("pay ", "pay?", "payment", "card", "paypal", "cash on delivery", "apple pay", "плащане", "плати",
                "платя", "карта", "наложен"),
    "warranty": ("warranty", "guarantee", "repair", "гаранция", "ремонт"),
    "discounts": ("discount", "promo", "coupon", "code", "gift card", "voucher", "sale", "отстъпка", "промо",
                  "код", "ваучер", "подаръчна карта"),
    "contact": ("contact", "phone", "showroom", "store address", "opening hours", "email you", "контакт",
                "телефон", "шоурум", "магазин на", "работно време"),
    "size_guide": ("size guide", "size chart", "which size", "what size", "sizing", "true to size", "runs small",
                   "runs large", "таблица с размери", "какъв размер", "кой размер", "размерите"),
}
PRODUCT_DETAIL = ("size", "sizes", "colour", "color", "stock", "available", "fit", "fits", "material", "waterproof",
                  "weight", "weigh", "warm enough", "how warm", "itch", "wash", "care", "battery", "капацитет",
                  "размер", "номер", "цвят", "цвета", "налич", "има ли", "имате ли", "материал", "тежи", "стои",
                  "пере")
GREETING = ("hi", "hello", "hey", "hiya", "good morning", "good evening", "здравей", "здравейте", "добър ден",
            "добро утро", "добър вечер", "привет", "хей", "здрасти")
THANKS = ("thanks", "thank you", "thx", "cheers", "благодаря", "мерси", "благодаря ви")
INJECTION = ("ignore previous", "ignore all", "ignore your", "ignore the above", "disregard", "system prompt",
             "your instructions", "your rules", "you are now", "pretend to be", "act as", "developer mode",
             "jailbreak", "reveal your", "игнорирай", "забрави инструкциите", "системния промпт")
OFF_TOPIC = ("weather", "poem", "joke", "homework", "bitcoin", "crypto", "stock market", "politic", "election",
             "recipe", "football", "translate this", "write me", "write an essay", "capital of", "who won",
             "времето", "стихотворение", "виц", "домашно", "рецепта", "политика", "футбол")

PLEASANTRY_WORDS = frozenset(w for p in GREETING + THANKS + ("there", "again", "so", "much", "a", "lot", "ok",
                                                          "okay", "great", "много", "ок", "супер")
                             for w in p.split())

ASK_EMAIL_FOR_TEAM = {"en": "So that a colleague can reply, what's your email address?",
                      "bg": "За да ви отговори колега, напишете ми имейла си."}
ASK_ORDER = {"en": "I can check that. What's your order number and the email address you used for the order?",
             "bg": "Мога да проверя. Напишете ми номера на поръчката и имейла, с който сте поръчали."}
ASK_ORDER_EMAIL = {"en": "And the email address you used for order #{n}?",
                   "bg": "А с кой имейл е направена поръчка №{n}?"}


def has_cue(text: str, cues) -> bool:
    low = fold(text)
    return any(re.search(r"(?<!\w)" + re.escape(c), low) for c in cues)


def detect_lang(text: str) -> str:
    return "bg" if re.search(r"[Ѐ-ӿ]", text) else "en"


def conversation_lang(shopper: list[tuple[str, str | None]]) -> str:
    """The language of the latest message that has words in it (not just an order number or an email)."""
    for text, _ in reversed(shopper):
        words = re.findall(r"[^\W\d_]{3,}", EMAIL.sub(" ", text))
        if words:
            return detect_lang(" ".join(words))
    return "en"


def order_numbers(text: str) -> list[str]:
    out = []
    for m in ORDER.finditer(text):
        if not PRICE_WORD_BEFORE.search(text[:m.start()]):
            out.append(m.group(1))
    return out


@dataclass
class DialogueState:
    """What the router can see: the shopper's messages (with the product page they were on) and our last answer."""

    shopper: list[tuple[str, str | None]]
    previous_answer: str = ""

    @property
    def text(self) -> str:
        return self.shopper[-1][0] if self.shopper else ""

    @property
    def page_handle(self) -> str | None:
        return self.shopper[-1][1] if self.shopper else None


@dataclass
class Route:
    intent: str
    slots: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)  # required slots still unknown: ask for them
    lang: str = "en"

    @property
    def confidence(self) -> float:
        ranked = sorted(self.scores.values(), reverse=True) + [0.0, 0.0]
        return 0.0 if ranked[0] <= 0 else round((ranked[0] - ranked[1]) / ranked[0], 3)


class ProductMatcher:
    """Finds the product a message is about: its handle, its full title, or a name word only it has
    ('Alpine', 'Fjell'). Generic words ('trail running shoes') are a search, not a product."""

    def __init__(self, products: list[dict]):
        self.products = [(p["handle"], p["title"]) for p in products]
        generic = set(SYNONYMS) | {stem(w) for ws in TRANSLATIONS.values() for w in ws}
        counts: dict[str, int] = {}
        self.title_words: dict[str, set[str]] = {}
        for handle, title in self.products:
            words = {stem(w) for w in raw_words(title)
                     if not w.isdigit() and w not in SEARCH_STOPWORDS and w not in ("pair", "set", "top")}
            self.title_words[handle] = words
            for w in words:
                counts[w] = counts.get(w, 0) + 1
        # Words in other products' text make a name ambiguous ("trail" is in many descriptions).
        self.distinctive = {handle: {w for w in words if counts[w] == 1 and len(w) >= 4 and w not in generic}
                            for handle, words in self.title_words.items()}

    def find(self, text: str) -> str | None:
        low = fold(text)
        for handle, title in self.products:
            if handle in low or fold(title) in low:
                return handle
        words = {stem(w) for w in raw_words(text)}
        named = [h for h, ds in self.distinctive.items() if ds & words]
        if len(named) == 1:
            return named[0]
        return None


def route(state: DialogueState, matcher: ProductMatcher) -> Route:
    text = state.text
    lang = conversation_lang(state.shopper)
    everything = " ".join(t for t, _ in state.shopper)
    previous = state.previous_answer or ""
    emails = EMAIL.findall(everything)
    numbers = order_numbers(everything)
    text_no_email = EMAIL.sub(" ", text)
    words = raw_words(text_no_email)
    scores = dict.fromkeys(INTENTS, 0.0)

    # --- cues ---
    pending_team = any(v in previous for v in ASK_EMAIL_FOR_TEAM.values())
    pending_order = any(v in previous for v in ASK_ORDER.values()) or "the email address you used for order" in \
        previous or "с кой имейл е направена поръчка" in previous
    if has_cue(text, HUMAN):
        scores["handoff"] += 3.0
    if has_cue(text, ACTIONS):
        scores["handoff"] += 2.0
    if pending_team:
        scores["handoff"] += 3.0
    if has_cue(text, ORDER_WORDS):
        scores["order_status"] += 2.5
    if pending_order:
        scores["order_status"] += 3.0
    if order_numbers(text) and EMAIL.search(text):
        scores["order_status"] += 2.0
    elif order_numbers(text) and not words:
        scores["order_status"] += 1.5

    product = matcher.find(text_no_email)
    parsed = parse_query(text_no_email)
    wants_detail = has_cue(text, PRODUCT_DETAIL) or bool(parsed.size or parsed.colour)
    if product:
        scores["product_info"] += 2.5 + (1.0 if wants_detail else 0.0)
    elif state.page_handle and (wants_detail or len(text) < 40):
        product = state.page_handle
        scores["product_info"] += 2.5 if wants_detail else 1.6

    topic = None
    for name, cues in POLICY_WORDS.items():
        if has_cue(text + " ", cues) and not (name == "size_guide" and product):
            topic = name
            break
    if topic:
        scores["policy"] += 3.0

    content_words = [w for w in words if w not in SEARCH_STOPWORDS]
    only_pleasantries = bool(words) and all(w in PLEASANTRY_WORDS or w in SEARCH_STOPWORDS for w in words)
    if only_pleasantries and has_cue(text, GREETING + THANKS):
        scores["greeting"] += 3.0
    if has_cue(text, INJECTION):
        scores["out_of_scope"] += 4.0
    if has_cue(text, OFF_TOPIC):
        scores["out_of_scope"] += 2.5
    scores["product_search"] += 1.0 if content_words else 0.2

    best = max(scores.values())
    intent = next(i for i in PRIORITY if scores[i] == best)
    r = Route(intent=intent, scores=scores, lang=lang)

    # --- slots ---
    if intent == "handoff":
        first = next((t for t, _ in state.shopper if has_cue(t, HUMAN)), text)
        r.slots = {"reason": f"Shopper wrote: {first[:300]}", "email": emails[-1] if emails else None,
                   "priority": "urgent" if has_cue(first, URGENT) else "normal"}
        if not emails:
            r.missing = ["email"]
    elif intent == "order_status":
        r.slots = {"order_number": numbers[-1] if numbers else None, "email": emails[-1] if emails else None}
        r.missing = [k for k, v in r.slots.items() if not v]
    elif intent == "policy":
        r.slots = {"topic": topic}
    elif intent == "product_info":
        r.slots = {"handle": product, "size": parsed.size, "colour": parsed.colour}
    elif intent == "greeting":
        r.slots = {"kind": "thanks" if has_cue(text, THANKS) else "hello"}
    elif intent == "out_of_scope":
        r.slots = {"kind": "injection" if has_cue(text, INJECTION) else "off_topic"}
    else:
        query = " ".join(w for w in raw_words(parsed.rest) if w not in SEARCH_STOPWORDS and not w.isdigit()
                         and w not in PLEASANTRY_WORDS and len(w) > 1)
        r.slots = {"query": query[:120] or fold(text)[:120], "min_price": parsed.min_price,
                   "max_price": parsed.max_price, "size": parsed.size, "colour": parsed.colour,
                   "in_stock_only": parsed.in_stock or None}
    r.slots = {k: v for k, v in r.slots.items() if v is not None}
    return r
