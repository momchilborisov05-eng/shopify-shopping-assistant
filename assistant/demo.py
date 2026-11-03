"""
Demo mode: a stand-in for the Claude client, so the assistant can be tried
without an API key (DEMO_MODE=1).

It plans with simple keyword rules instead of a model, but it calls the same
tools on the same store, in the same loop, so search, product details, stock,
policies, order lookup, handoff tickets, product cards and the dashboard all
work exactly as they do with Claude. It answers in Bulgarian or English. Claude
understands far more than these rules do.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from anthropic.types.beta import BetaMessage

from .catalog import STOPWORDS, DemoStore

DEMO_MODEL = "demo"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ORDER = re.compile(r"(?:#|№|\b)(\d{4,6})\b")
PRICE_CAP = re.compile(r"(?:under|below|up to|max|до|под|не повече от)\s*€?\s*(\d{2,4})", re.I)
SIZE = re.compile(r"\b(xxl|xl|xs|s|m|l)\b|\b(?:eu\s*)?(3\d|4\d)\b", re.I)

HUMAN = ("human", "person", "agent", "operator", "someone real", "complain", "damaged", "broken", "wrong item",
         "refund", "cancel", "човек", "оператор", "служител", "колега", "оплакване", "повреден", "счупен",
         "сгрешен", "грешен продукт", "възстанов", "откажа", "анулир", "жалба")
ORDER_WORDS = ("order", "parcel", "package", "tracking", "track", "where is my", "delivery status", "поръчка",
               "поръчката", "пратка", "пратката", "проследяв", "къде е", "кога ще пристигне")
POLICY_WORDS = {
    "returns": ("return", "exchange", "send back", "връщ", "върна", "замяна", "заменя", "размяна"),
    "shipping": ("shipping", "delivery", "deliver", "courier", "speedy", "econt", "доставка", "доставя",
                 "куриер", "еконт", "спиди", "колко време"),
    "payment": ("pay", "card", "paypal", "cash on delivery", "плащане", "плати", "карта", "наложен"),
    "warranty": ("warranty", "guarantee", "repair", "гаранция", "ремонт"),
    "discounts": ("discount", "promo", "coupon", "code", "gift card", "voucher", "отстъпка", "промо", "код",
                  "ваучер", "подаръчна карта"),
    "contact": ("contact", "phone", "showroom", "store address", "контакт", "телефон", "шоурум", "магазин на"),
    "size_guide": ("size guide", "size chart", "which size", "what size", "таблица с размери", "какъв размер",
                   "кой размер", "размерите"),
}
PRODUCT_DETAIL = ("size", "sizes", "colour", "color", "stock", "available", "fit", "material", "waterproof",
                  "weight", "размер", "номер", "цвят", "цвета", "налич", "има ли", "имате ли", "материал",
                  "тежи", "стои")
GREETING = ("hi", "hello", "hey", "здравей", "здравейте", "добър ден", "привет", "хей")
ASK_EMAIL_FOR_TEAM = {"en": "So that a colleague can reply, what's your email address?",
                      "bg": "За да ви отговори колега, напишете ми имейла си."}
ASK_ORDER = {"en": "I can check that. What's your order number and the email address you used for the order?",
             "bg": "Мога да проверя. Напишете ми номера на поръчката и имейла, с който сте поръчали."}
STATUS = {
    "in_transit": ("is on its way with {company}", "пътува със {company}"),
    "delivered": ("was delivered on {delivered_at}", "е доставена на {delivered_at}"),
    "unfulfilled": ("is being packed and will ship soon", "се подготвя за изпращане"),
    "returned": ("was returned and refunded on {refunded_at}", "е върната и парите са възстановени на {refunded_at}"),
    "partially_fulfilled": ("is partly shipped with {company}", "е изпратена частично със {company}"),
}


def _lang(text: str) -> str:
    return "bg" if re.search(r"[\u0400-\u04FF]", text) else "en"


def _conversation_lang(shopper: list[tuple[str, str | None]]) -> str:
    """The language of the latest message that has words in it (not just an order number or an email)."""
    for text, _ in reversed(shopper):
        words = re.findall(r"[^\W\d_]{3,}", EMAIL.sub(" ", text))
        if words:
            return _lang(" ".join(words))
    return "en"


def _money(value: float) -> str:
    return f"€{value:.0f}" if float(value).is_integer() else f"€{value:.2f}"


def _shopper_texts(messages: list) -> list[tuple[str, str | None]]:
    """(what the shopper wrote, product handle of the page) for every shopper message, oldest first."""
    out = []
    for m in messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        texts = [b["text"] for b in m["content"] if b.get("type") == "text"]
        if not texts:
            continue
        handle = None
        for t in texts[1:]:
            found = re.search(r"handle '([a-z0-9-]+)'", t)
            handle = found.group(1) if found else handle
        out.append((texts[0], handle))
    return out


def _assistant_texts(messages: list) -> list[str]:
    return ["".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
            for m in messages if m["role"] == "assistant"]


def _has(text: str, words) -> bool:
    low = text.lower()
    return any(re.search(r"(?<!\w)" + re.escape(w), low) for w in words)


class _Stream:
    def __init__(self, message: BetaMessage, text: str):
        self.message = message
        self.text_stream = self._words(text)

    @staticmethod
    def _words(text):
        for i, word in enumerate(text.split(" ")):
            if word:
                time.sleep(0.012)
            yield word if i == 0 else " " + word

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def _message(content: list[dict], stop_reason: str, text_len: int = 0) -> BetaMessage:
    return BetaMessage.model_validate({
        "id": f"msg_demo_{uuid.uuid4().hex[:12]}", "type": "message", "role": "assistant", "model": DEMO_MODEL,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": max(20, text_len // 4)},
    })


class DemoClient:
    """Implements the one call the assistant makes: client.beta.messages.stream(...)."""

    def __init__(self, store: DemoStore):
        self.store = store
        self.beta = self
        self.messages = self

    def stream(self, *, messages, **_):
        last = messages[-1]["content"]
        if isinstance(last, list) and last and all(b.get("type") == "tool_result" for b in last):
            text = self._answer(messages)
            return _Stream(_message([{"type": "text", "text": text}], "end_turn", len(text)), text)
        plan = self._plan(messages)
        if isinstance(plan, str):
            return _Stream(_message([{"type": "text", "text": plan}], "end_turn", len(plan)), plan)
        name, args = plan
        block = {"type": "tool_use", "id": f"toolu_demo_{uuid.uuid4().hex[:12]}", "name": name, "input": args}
        return _Stream(_message([block], "tool_use"), "")

    # ----- deciding what to do -----

    def _plan(self, messages: list):
        shopper = _shopper_texts(messages)
        text, page_handle = shopper[-1]
        lang = _conversation_lang(shopper)
        everything = " ".join(t for t, _ in shopper)
        previous_answer = (_assistant_texts(messages) or [""])[-1]
        emails = EMAIL.findall(everything)
        email = emails[-1] if emails else None
        numbers = ORDER.findall(everything)
        number = numbers[-1] if numbers else None

        wants_team = _has(text, HUMAN) or (ASK_EMAIL_FOR_TEAM[lang] in previous_answer
                                          or ASK_EMAIL_FOR_TEAM["en"] in previous_answer)
        if wants_team:
            if not email:
                return ASK_EMAIL_FOR_TEAM[lang]
            first_request = next((t for t, _ in shopper if _has(t, HUMAN)), text)
            return "handoff_to_human", {"reason": f"Shopper wrote: {first_request[:300]}", "email": email,
                                        "priority": "urgent" if _has(first_request, ("damaged", "broken",
                                                                                     "повреден", "счупен"))
                                        else "normal"}

        asked_for_order = ASK_ORDER["en"] in previous_answer or ASK_ORDER["bg"] in previous_answer
        if _has(text, ORDER_WORDS) or asked_for_order or (ORDER.search(text) and EMAIL.search(text)):
            if number and email:
                return "lookup_order", {"order_number": number, "email": email}
            if number and not email:
                return ("And the email address you used for order #{n}?" if lang == "en"
                        else "А с кой имейл е направена поръчка №{n}?").format(n=number)
            return ASK_ORDER[lang]

        product = self._product_in(text) or (page_handle if _has(text, PRODUCT_DETAIL) or len(text) < 40 else None)
        for topic, words in POLICY_WORDS.items():
            if _has(text, words) and not (topic == "size_guide" and product):
                return "get_store_policy", {"topic": topic}
        if product:
            return "get_product", {"handle": product}

        words = re.findall(r"\w+", text.lower())
        if words and len(words) <= 3 and all(w in " ".join(GREETING) for w in words):
            return (f"Hi! I'm the {self.store.name} assistant. I can help you find gear, choose a size, or "
                    "check your order. What are you looking for?" if lang == "en" else
                    f"Здравейте! Аз съм асистентът на {self.store.name}. Мога да ви помогна да изберете "
                    "екипировка и размер или да проверя поръчка. Какво търсите?")
        keywords = [w for w in words if w not in STOPWORDS and not w.isdigit() and len(w) > 1]
        args = {"query": " ".join(keywords)[:120] or text[:120]}
        cap = PRICE_CAP.search(text)
        if cap:
            args["max_price"] = float(cap.group(1))
        return "search_products", args

    def _product_in(self, text: str) -> str | None:
        low = text.lower()
        for product in self.store.all_products():
            first = product["title"].split()[0].lower()
            if product["handle"] in low or product["title"].lower() in low or (len(first) > 4 and first in low):
                return product["handle"]
        return None

    # ----- answering from the tool results -----

    def _answer(self, messages: list) -> str:
        calls = {b["id"]: b for b in messages[-2]["content"] if b.get("type") == "tool_use"}
        result_block = messages[-1]["content"][0]
        call = calls[result_block["tool_use_id"]]
        result = json.loads(result_block["content"])
        shopper = _shopper_texts(messages)
        text = shopper[-1][0]
        lang = _conversation_lang(shopper)
        if result_block.get("is_error"):
            return ("Sorry, something went wrong on my side. Shall I pass your question to the team?" if lang == "en"
                    else "Съжалявам, нещо се обърка. Да предам ли въпроса ви на колега?")
        return getattr(self, "_answer_" + call["name"])(result, lang, text)

    def _answer_search_products(self, result, lang, text):
        products = result["products"][:3]
        if not products:
            return ("I couldn't find anything that matches. Could you tell me a bit more, for example what "
                    "you'll use it for or your budget?" if lang == "en" else
                    "Не намерих нищо подходящо. Разкажете ми малко повече, например за какво ще го ползвате "
                    "или какъв е бюджетът ви?")
        lines = ["Here are some good options:" if lang == "en" else "Ето няколко добри варианта:"]
        for p in products:
            sale = ""
            if p["compare_at_price"]:
                sale = (f" (on sale, was {_money(p['compare_at_price'])})" if lang == "en"
                        else f" (намалено от {_money(p['compare_at_price'])})")
            rating = f" · ★ {p['rating']}" if p.get("rating") else ""
            stock = "" if p["in_stock"] else (" · sold out right now" if lang == "en" else " · в момента е изчерпан")
            lines.append(f"- **{p['title']}**: {_money(p['price'])}{sale}{rating}{stock}")
        lines.append("Shall I check sizes and stock for one of them?" if lang == "en"
                     else "Да проверя ли размерите и наличността на някой от тях?")
        return "\n".join(lines)

    def _answer_get_product(self, p, lang, text):
        if p.get("not_found"):
            return "I couldn't find that product." if lang == "en" else "Не намерих този продукт."
        variants = p["variants"]
        asked = SIZE.search(text)
        size = None
        if asked:
            size = (asked.group(1) or "").upper() or f"EU {asked.group(2)}"
        price = _money(p["price"])
        head = (f"**{p['title']}** costs {price}." if lang == "en" else f"**{p['title']}** струва {price}.")
        if size and any("Size" in v["options"] for v in variants):
            matching = [v for v in variants if v["options"].get("Size", "").upper() == size.upper()]
            ok = [v for v in matching if v["available"]]
            if not matching:
                body = (f"It doesn't come in size {size}." if lang == "en" else f"Няма размер {size}.")
            elif ok and not any(v["options"].get("Color") for v in ok):
                body = (f"Size {size} is available ({ok[0]['stock']})." if lang == "en"
                        else f"Размер {size} е наличен ({ok[0]['stock']}).")
            elif ok:
                where = ", ".join(f"{v['options']['Color']} ({v['stock']})" for v in ok)
                body = (f"Size {size} is available: {where}." if lang == "en"
                        else f"Размер {size} е наличен: {where}.")
            else:
                body = (f"Size {size} is sold out right now." if lang == "en"
                        else f"Размер {size} в момента е изчерпан.")
            if p.get("fit") and lang == "en":
                body += f" Fit: {p['fit']}"
            return f"{head} {body}"
        available = [v["title"] for v in variants if v["available"]]
        sold_out = [v["title"] for v in variants if not v["available"]]
        if lang == "en":
            parts = [head, p["description"]]
            if p.get("options"):
                parts.append("Available now: " + (", ".join(available) or "none") + ".")
                if sold_out:
                    parts.append("Sold out: " + ", ".join(sold_out) + ".")
            if p.get("fit"):
                parts.append(f"Fit: {p['fit']}")
        else:
            parts = [head]
            if p.get("options"):
                parts.append("В наличност: " + (", ".join(available) or "няма") + ".")
                if sold_out:
                    parts.append("Изчерпани: " + ", ".join(sold_out) + ".")
            parts.append("Искате ли да ви помогна с размера?")
        return " ".join(parts)

    def _answer_get_store_policy(self, policy, lang, text):
        if policy.get("missing"):
            return ("I don't have that information. Shall I ask the team?" if lang == "en"
                    else "Нямам тази информация. Да попитам ли колега?")
        body = self.store.policy_bg(policy["topic"]) if lang == "bg" else policy["body"]
        return body + ("\n\nAnything else I can help with?" if lang == "en" else "\n\nС какво друго да помогна?")

    def _answer_lookup_order(self, order, lang, text):
        if order.get("not_found") or order.get("blocked"):
            return ("I couldn't find an order with that number and email. Could you check both? They must be the "
                    "ones from your order confirmation." if lang == "en" else
                    "Не намерих поръчка с този номер и имейл. Проверете ги, моля: трябва да са като в "
                    "потвърждението на поръчката.")
        tracking = order.get("tracking") or {}
        fields = {**order, "company": tracking.get("company") or ""}
        default = ("is {fulfillment_status}", "е със статус {fulfillment_status}")
        en, bg = STATUS.get(order["fulfillment_status"], default)
        status = (en if lang == "en" else bg).format(**fields)
        items = ", ".join(f"{i['title']} ({i['variant']})" for i in order["items"])
        if lang == "en":
            out = [f"Thanks, {order['customer_first_name']}! Order {order['number']} {status}."]
            if tracking.get("url"):
                out.append(f"Tracking: [{tracking['number']}]({tracking['url']}).")
            if order.get("estimated_delivery") and order["fulfillment_status"] != "delivered":
                out.append(f"Expected delivery: {order['estimated_delivery']}.")
            out.append(f"Items: {items}.")
        else:
            out = [f"Благодаря, {order['customer_first_name']}! Поръчка {order['number']} {status}."]
            if tracking.get("url"):
                out.append(f"Проследяване: [{tracking['number']}]({tracking['url']}).")
            if order.get("estimated_delivery") and order["fulfillment_status"] != "delivered":
                out.append(f"Очаквана доставка: {order['estimated_delivery']}.")
            out.append(f"Продукти: {items}.")
        notes = [f"{i['title']}: {i['note']}" for i in order["items"] if i.get("note")]
        if notes:
            out.append(("Note: " if lang == "en" else "Бележка: ") + "; ".join(notes) + ".")
        return " ".join(out)

    def _answer_handoff_to_human(self, result, lang, text):
        where = ("by email" if result.get("reply_by") == "email" else "here") if lang == "en" else \
            ("по имейл" if result.get("reply_by") == "email" else "тук")
        if lang == "en":
            return (f"I've passed this to our team (ticket #{result['ticket']}). They'll reply {where}, Monday to "
                    "Friday 9:00-18:00, usually within 2 hours.")
        return (f"Предадох въпроса на екипа (сигнал №{result['ticket']}). Ще ви отговорят {where}, от понеделник "
                "до петък 9:00-18:00, обикновено до 2 часа.")
