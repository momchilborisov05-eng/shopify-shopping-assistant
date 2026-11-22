"""The grounding guard: numbers in answers must come from tool results or the shopper."""

import json

from assistant.agent import ShopAssistant
from assistant.catalog import DemoStore
from assistant.grounding import FALLBACK, check, enforce, extract_numbers, sources_from_history
from assistant.storage import Storage

TOOL = json.dumps({"title": "Alpine Shell Jacket", "price": 189.0, "stock": "only 2 left",
                   "policy": "Returns within 30 days. Delivery in 1-2 business days, €4.90."})


def kinds(text):
    return [(m.text, m.kind) for m in extract_numbers(text)]


def test_extract_labels_number_kinds():
    assert kinds("It costs €189 and only 2 left.") == [("189", "price"), ("2", "stock")]
    assert kinds("Returns within 30 days, 20% off.") == [("30", "duration"), ("20", "percent")]
    assert kinds("Arrives 2026-11-04.") == [("2026", "date"), ("11", "date"), ("04", "date")]
    assert kinds("29,90 лв") == [("29,90", "price")]


def test_extract_skips_list_markers_urls_and_codes():
    assert kinds("1. **Alpine Shell Jacket**") == []
    assert kinds("Track it [here](https://speedy.bg/track?n=123456)") == []
    assert kinds("SKU AL-M-200 and tracking SP6612043918") == []


def test_grounded_answer_passes():
    report = check("**Alpine Shell Jacket** costs €189, only 2 left. Returns within 30 days.", [TOOL])
    assert report.ok and len(report.checked) == 3


def test_invented_price_and_saving_are_caught():
    report = check("It costs €179, you save €10 compared to last week.", [TOOL])
    assert {v.text for v in report.violations} == {"179", "10"}


def test_numbers_from_the_shopper_are_allowed():
    assert check("Here are jackets under €200.", [TOOL, "a jacket under 200 please"]).ok
    assert not check("Here are jackets under €200.", [TOOL]).ok


def test_small_counts_are_not_checked_but_stock_is():
    assert check("Here are 3 options.", [TOOL]).ok
    assert not check("Only 3 left!", [TOOL]).ok


def test_decimal_comma_and_dot_match():
    assert check("Delivery costs 4,90 €.", [TOOL]).ok


def test_enforce_removes_only_the_bad_sentence_and_adds_a_note():
    answer = "The **Alpine Shell Jacket** costs €189. It is 15% off today! Want sizes?"
    fixed = enforce(answer, check(answer, [TOOL]), "en")
    assert "15%" not in fixed and "€189" in fixed and "Want sizes?" in fixed and FALLBACK["en"] in fixed
    bg = enforce("Струва €150.", check("Струва €150.", [TOOL]), "bg")
    assert bg == FALLBACK["bg"]


def test_enforce_drops_a_list_item_with_its_marker():
    answer = "Options:\n1. Jacket A: €189\n2. Jacket B: €99\nShall I check sizes?"
    fixed = enforce(answer, check(answer, [TOOL]), "en")
    assert "€99" not in fixed and "2." not in fixed and "1. Jacket A: €189" in fixed


def test_sources_are_tool_results_and_shopper_text_only():
    history = [{"role": "user", "content": [{"type": "text", "text": "under 50"}]},
               {"role": "assistant", "content": [{"type": "text", "text": "€999"}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": TOOL}]}]
    sources = sources_from_history(history)
    assert "under 50" in sources and TOOL in sources and "€999" not in sources


class _Final:
    def __init__(self, text):
        from anthropic.types.beta import BetaMessage
        self.msg = BetaMessage.model_validate({
            "id": "m", "type": "message", "role": "assistant", "model": "x", "stop_reason": "end_turn",
            "stop_sequence": None, "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1}})
        self.text_stream = [text]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _HallucinatingClient:
    """A model that answers without tools and invents a price."""

    def __init__(self):
        self.beta = self
        self.messages = self

    def stream(self, **_):
        return _Final("Sure! The **Alpine Shell Jacket** is on sale for €149 this week. Anything else?")


def test_agent_replaces_an_answer_with_an_invented_price():
    storage = Storage()
    assistant = ShopAssistant(_HallucinatingClient(), DemoStore(), storage, model="demo")
    cid = storage.create_conversation()
    events = list(assistant.respond(cid, "is the alpine jacket on sale?"))
    result = events[-1]["result"]
    assert result.grounding["violations"] == [{"text": "149", "kind": "price"}]
    assert "€149" not in result.answer and FALLBACK["en"] in result.answer
    assert any(e["type"] == "replace" for e in events)
    saved = storage.get_conversation(cid)["history"][-1]["content"][-1]["text"]
    assert "€149" not in saved  # later turns don't see the invented figure
    assert {"name": "grounding_guard", "error": True} in result.tools
    storage.close()
