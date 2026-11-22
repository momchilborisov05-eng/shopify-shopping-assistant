"""The search engine: BM25F ranking, parsed filters, synonyms, Bulgarian and typos."""

import pytest

from assistant.catalog import DemoStore
from assistant.search import (
    SearchEngine,
    SearchOptions,
    TypoIndex,
    colour_family,
    max_edits,
    normalize_size,
    osa_distance,
    parse_query,
)


@pytest.fixture(scope="module")
def engine():
    return DemoStore().engine


def titles(result):
    return [h.product["title"] for h in result.hits]


@pytest.mark.parametrize("query, field, value", [
    ("jacket under €200", "max_price", 200),
    ("boots below 150 euro", "max_price", 150),
    ("tent over 100", "min_price", 100),
    ("jacket between 100 and 200", "min_price", 100),
    ("jacket 100-200 eur", "max_price", 200),
    ("яке под 150 лв", "max_price", 150),
    ("waterproof jacket size M", "size", "M"),
    ("boots size 43", "size", "43"),
    ("боти номер 42", "size", "42"),
    ("green fleece in xl", "size", "XL"),
    ("navy jacket", "colour", "blue"),
    ("червено яке", "colour", "red"),
])
def test_parse_query_extracts_filters(query, field, value):
    assert getattr(parse_query(query), field) == value


def test_parse_query_keeps_product_words_and_drops_filter_words():
    parsed = parse_query("green jacket xl in stock under 200")
    assert parsed.in_stock and parsed.colour == "green" and parsed.size == "XL" and parsed.max_price == 200
    assert "jacket" in parsed.terms and "200" not in parsed.terms and "green" not in parsed.terms


def test_bare_numbers_are_not_sizes_without_a_sized_product():
    assert parse_query("expedition pack 55").size is None
    assert parse_query("trail shoes 42").size == "42"


def test_size_and_colour_helpers():
    assert normalize_size("eu 42") == normalize_size("42") == "42"  # matches the "EU 42" variant
    assert normalize_size("xl") == "XL"
    assert colour_family("Forest Green") == "green" and colour_family("Sky Blue") == "blue"
    assert colour_family(None) is None


def test_osa_distance_counts_a_transposition_as_one_edit():
    assert osa_distance("jakcet", "jacket") == 1
    assert osa_distance("waterprof", "waterproof") == 1
    assert osa_distance("tent", "tent") == 0
    assert [max_edits(w) for w in ("cap", "jacket", "waterproof")] == [0, 1, 2]


def test_typo_index_corrects_to_the_most_common_close_word():
    index = TypoIndex({"jacket": 5, "packet": 1, "boots": 3})
    assert index.correct("jaket") == ["jacket"]
    assert index.correct("bot") == []  # too short to correct


def test_bm25f_ranks_a_title_match_above_a_body_match(engine):
    assert titles(engine.search("headlamp"))[0] == "Beacon 400 Headlamp"
    assert titles(engine.search("fleece"))[0] == "Trail Grid Fleece"


def test_typos_synonyms_and_bulgarian_reach_the_same_product(engine):
    for query in ("waterproof jacket", "waterprof jaket", "rain coat", "водоустойчиво яке"):
        assert titles(engine.search(query))[0] == "Alpine Shell Jacket", query
    result = engine.search("waterprof jaket")
    assert result.corrections  # the engine reports what it corrected


def test_compound_words_are_merged(engine):
    assert "Beacon 400 Headlamp" in titles(engine.search("head torch"))


def test_variant_filters_keep_only_matching_variants(engine):
    hit = engine.search("green jacket xl in stock").hits[0]
    assert hit.product["title"] == "Alpine Shell Jacket"
    assert hit.variants and all(v["options"]["Size"] == "XL" and v["inventory"] > 0 for v in hit.variants)
    assert all(colour_family(v["options"]["Color"]) == "green" for v in hit.variants)


def test_hard_filters_exclude_products(engine):
    assert all(h.product["price"] <= 100 for h in engine.search("jacket under 100").hits)
    assert "Fjell Daypack 28L" in titles(engine.search("blue backpack"))
    assert "Expedition Pack 55+10L" not in titles(engine.search("blue backpack"))


def test_explicit_arguments_override_parsed_ones(engine):
    assert engine.search("jacket under 300", max_price=200).parsed.max_price == 300
    assert all(h.product["price"] <= 200 for h in engine.search("jacket under 300", max_price=200).hits)


def test_no_match_returns_nothing_rather_than_noise(engine):
    assert engine.search("surfboard").hits == []


def test_ablation_options_change_behaviour():
    products = DemoStore().engine.products
    plain = SearchEngine(products, SearchOptions(synonyms=False, typos=False))
    assert plain.search("waterprof jaket").hits == []
    assert SearchEngine(products).search("waterprof jaket").hits


def test_store_search_reports_matching_variants():
    found = DemoStore().search_products("green jacket xl")
    assert found[0]["title"] == "Alpine Shell Jacket"
    assert found[0]["matching_variants"] and "XL" in found[0]["matching_variants"][0]["title"]
