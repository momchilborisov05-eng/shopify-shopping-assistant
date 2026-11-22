"""Build the search evaluation set: a 120-product outdoor catalogue and 58 graded queries.

    python scripts/build_eval_catalog.py      # writes data/eval_catalog.json and eval/search_queries.json

The catalogue is generated from a fixed seed. Every product has hidden ground-truth fields (`_type`,
`_attrs`) that the search engine never reads: it only sees the title, tags, category, description,
features, materials and variants, and those are written from the hidden fields with varied wording
("waterproof", "rainproof", "a membrane keeps the rain out"...), including a few misleading
negations ("not waterproof"). So the engine has to bridge vocabulary, not look up labels.

Relevance labels are computed from the hidden fields by one rule and frozen into the query file:

    2  the product is one of the query's types, has all its attributes and passes its hard filters
    1  it is one of those types but lacks an attribute, or a related type that has all the attributes
    0  anything else, and anything that fails a hard filter (price, size, colour, in stock)
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant.search import colour_family  # noqa: E402

SEED = 20261120
APPAREL = ["XS", "S", "M", "L", "XL", "XXL"]
SHOE = [str(n) for n in range(36, 48)]
COLOURS = ["Black", "Navy", "Ocean Blue", "Forest Green", "Olive", "Slate Grey", "Rust", "Crimson", "Mustard",
           "Sand", "Plum", "Orange"]
BRANDS = ["Kestrel", "Halden", "Tind", "Vidda", "Fjord", "Lynx", "Bjorn", "Saga", "Norr", "Elk", "Rune",
          "Vinter", "Skarn", "Ulv", "Aska", "Myr", "Hav", "Stig"]
MODELS = ["Ridge", "Summit", "Trail", "Peak", "Alpine", "Glacier", "Storm", "Pine", "Arc", "Ember", "Drift",
          "Crest", "Nord", "Tarn", "Scree", "Corrie"]

# type: category, title nouns, attribute probabilities, sizes, colours?, price range, uses
TYPES: dict[str, dict] = {
    "rain_jacket": dict(cat="Jackets", nouns=["Rain Shell", "Rain Jacket", "Storm Shell", "Anorak"],
                        attrs={"waterproof": 1.0, "windproof": 0.6, "lightweight": 0.5, "packable": 0.4},
                        sizes="apparel", price=(89, 329), uses=["hiking", "city rain", "ski touring"]),
    "down_jacket": dict(cat="Jackets", nouns=["Down Jacket", "Down Parka", "Puffer Jacket", "Down Hoody"],
                        attrs={"insulated": 1.0, "packable": 0.6, "lightweight": 0.4, "waterproof": 0.15},
                        sizes="apparel", price=(149, 399), uses=["winter", "cold belays", "city winters"]),
    "synthetic_jacket": dict(cat="Jackets", nouns=["Insulated Jacket", "Synthetic Hoody", "Thermal Jacket"],
                             attrs={"insulated": 1.0, "packable": 0.3, "waterproof": 0.2},
                             sizes="apparel", price=(99, 259), uses=["winter hiking", "cold mornings"]),
    "softshell": dict(cat="Jackets", nouns=["Softshell Jacket", "Softshell Hoody"],
                      attrs={"windproof": 0.8, "lightweight": 0.3}, sizes="apparel", price=(79, 199),
                      uses=["climbing", "fast hikes"]),
    "windbreaker": dict(cat="Jackets", nouns=["Wind Jacket", "Windbreaker"],
                        attrs={"windproof": 1.0, "lightweight": 0.8, "packable": 0.7}, sizes="apparel",
                        price=(49, 149), uses=["running", "breezy ridges"]),
    "hiking_boots": dict(cat="Footwear", nouns=["Hiking Boots", "Mid Boots", "Trekking Boots", "Leather Boots"],
                         attrs={"waterproof": 0.6, "lightweight": 0.3}, sizes="shoe", colours=False,
                         price=(109, 259), uses=["hiking", "multi-day treks", "rocky trails"]),
    "trail_shoes": dict(cat="Footwear", nouns=["Trail Running Shoes", "Trail Runners", "Trail Shoes"],
                        attrs={"waterproof": 0.2, "lightweight": 0.7}, sizes="shoe", colours=False,
                        price=(89, 179), uses=["trail running", "fast hiking"]),
    "winter_boots": dict(cat="Footwear", nouns=["Winter Boots", "Snow Boots"],
                         attrs={"insulated": 1.0, "waterproof": 0.9}, sizes="shoe", colours=False,
                         price=(119, 249), uses=["snow", "winter walks"]),
    "approach_shoes": dict(cat="Footwear", nouns=["Approach Shoes"], attrs={"lightweight": 0.4}, sizes="shoe",
                           colours=False, price=(99, 169), uses=["scrambling", "via ferrata"]),
    "daypack": dict(cat="Backpacks", nouns=["Daypack 22L", "Daypack 28L", "Hiking Pack 24L"],
                    attrs={"lightweight": 0.5, "waterproof": 0.2}, price=(49, 139),
                    uses=["day hikes", "commuting"]),
    "trekking_pack": dict(cat="Backpacks", nouns=["Trekking Pack 45L", "Expedition Pack 60L", "Backpack 50L"],
                          attrs={"lightweight": 0.3}, price=(139, 299), uses=["multi-day treks", "hut-to-hut"]),
    "running_vest": dict(cat="Backpacks", nouns=["Running Vest 8L", "Race Vest 5L"], attrs={"lightweight": 1.0},
                         sizes="apparel", price=(69, 149), uses=["trail running", "ultras"]),
    "base_layer": dict(cat="Clothing", nouns=["Merino Base Layer Top", "Thermal Top", "Base Layer Leggings"],
                       attrs={"merino": 0.6}, sizes="apparel", price=(39, 99), uses=["layering", "ski days"]),
    "fleece": dict(cat="Clothing", nouns=["Fleece Jacket", "Grid Fleece", "Fleece Pullover"],
                   attrs={"insulated": 0.4, "lightweight": 0.3}, sizes="apparel", price=(49, 129),
                   uses=["layering", "cool evenings"]),
    "hiking_pants": dict(cat="Clothing", nouns=["Hiking Pants", "Trekking Trousers", "Convertible Pants"],
                         attrs={"lightweight": 0.5}, sizes="apparel", price=(59, 139), uses=["hiking", "travel"]),
    "tent": dict(cat="Camping", nouns=["Tent 1P", "Tent 2P", "Tent 3P"], attrs={"lightweight": 0.5},
                 price=(149, 549), uses=["backpacking", "festival weekends", "alpine camps"]),
    "sleeping_bag": dict(cat="Camping", nouns=["Sleeping Bag -5°C", "Sleeping Bag 0°C", "Down Quilt"],
                         attrs={"lightweight": 0.5, "packable": 0.4}, price=(89, 379), uses=["backpacking"]),
    "sleeping_mat": dict(cat="Camping", nouns=["Sleeping Mat", "Inflatable Mat"],
                         attrs={"lightweight": 0.6, "insulated": 0.5}, price=(39, 159), uses=["camping"]),
    "stove": dict(cat="Camping", nouns=["Gas Stove", "Camp Stove"], attrs={"lightweight": 0.7},
                  colours=False, price=(29, 119), uses=["camp cooking"]),
    "headlamp": dict(cat="Accessories", nouns=["Headlamp 300", "Headlamp 500", "Head Torch 400"],
                     attrs={"waterproof": 0.5, "lightweight": 0.4}, price=(25, 89), uses=["night hikes", "camp"]),
    "bottle": dict(cat="Accessories", nouns=["Thermo Bottle 0.75L", "Insulated Flask 1L", "Water Bottle 1L"],
                   attrs={"insulated": 0.6}, price=(15, 45), uses=["everyday", "winter tea"]),
    "poles": dict(cat="Accessories", nouns=["Trekking Poles", "Carbon Poles"], attrs={"lightweight": 0.6},
                  colours=False, price=(39, 149), uses=["steep descents"]),
    "beanie": dict(cat="Accessories", nouns=["Merino Beanie", "Wool Beanie", "Fleece Hat"], attrs={"merino": 0.4},
                   price=(19, 45), uses=["cold days"]),
    "gloves": dict(cat="Accessories", nouns=["Insulated Gloves", "Softshell Gloves", "Mittens"],
                   attrs={"insulated": 0.5, "waterproof": 0.4, "windproof": 0.3}, sizes="apparel",
                   price=(25, 89), uses=["winter", "ski days"]),
}
PER_TYPE = 5  # 24 types x 5 = 120 products

PHRASES = {
    "waterproof": ["fully waterproof", "waterproof and breathable", "rainproof", "stormproof, with taped seams",
                   "a 2.5-layer membrane keeps the rain out"],
    "windproof": ["windproof", "blocks the wind", "windstopper fabric", "wind-resistant"],
    "insulated": ["insulated", "warm", "packed with 650-fill down", "synthetic insulation that stays warm when wet",
                  "a thermal lining"],
    "lightweight": ["lightweight", "ultralight", "featherweight", "only {g} g"],
    "packable": ["packable", "packs into its own pocket", "compressible"],
    "merino": ["merino wool", "merino", "fine wool"],
}
NEGATIONS = {"waterproof": "not waterproof, but quick-drying", "insulated": "not insulated, made for movement"}
TAG_WORDS = {"waterproof": "waterproof", "windproof": "windproof", "insulated": "insulated",
             "lightweight": "lightweight", "packable": "packable", "merino": "merino"}


def _price(rng: random.Random, lo: int, hi: int) -> float:
    return float(round(rng.uniform(lo, hi) / 5) * 5 - 1 if rng.random() < 0.7 else round(rng.uniform(lo, hi)))


def build_catalog(seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    products, used_titles, vid = [], set(), 90000
    for type_key, spec in TYPES.items():
        for i in range(PER_TYPE):
            noun = spec["nouns"][i % len(spec["nouns"])]
            while True:
                title = f"{rng.choice(BRANDS)} {rng.choice(MODELS)} {noun}"
                if title not in used_titles:
                    used_titles.add(title)
                    break
            attrs = sorted(a for a, p in spec["attrs"].items() if rng.random() < p)
            if type_key == "tent":
                attrs.append(noun.split()[-1].lower())  # 1p / 2p / 3p
            grams = rng.randrange(180, 1400, 10)
            phrases = [rng.choice(PHRASES[a]).format(g=grams) for a in attrs if a in PHRASES]
            negation = next((NEGATIONS[a] for a in NEGATIONS if a in spec["attrs"] and a not in attrs
                             and rng.random() < 0.15), None)
            use = rng.choice(spec["uses"])
            noun_low = noun.split(" ")[-1].lower() if noun[-1].isdigit() or "°" in noun else noun.lower()
            sentences = []
            if phrases:
                sentences.append(f"A {', '.join(phrases)} {noun_low} for {use}.")
            else:
                sentences.append(f"A dependable {noun_low} for {use}.")
            if negation:
                sentences.append(f"It is {negation}.")
            if type_key == "tent":
                people = {"1p": "one person", "2p": "two people", "3p": "three people"}[attrs[-1]]
                sentences.append(f"Sleeps {people} ({attrs[-1][0]}-person) with a vestibule for packs.")
            sentences.append(rng.choice(["Designed and tested in the Nordic mountains.",
                                         "Backed by a two-year warranty.", "Made with recycled materials.",
                                         "A favourite of our staff."]))
            tags = sorted({TAG_WORDS[a] for a in attrs if a in TAG_WORDS and rng.random() < 0.5}
                          | {spec["cat"].lower(), use.split()[-1]})
            features = [f"Weight {grams} g"] + [p.capitalize() for p in phrases[:2]]
            sizes = {"apparel": None, "shoe": None, None: None}
            size_list: list[str] = []
            if spec.get("sizes") == "apparel":
                a = rng.randrange(0, 3)
                size_list = APPAREL[a:a + rng.randrange(3, 7 - a)]
            elif spec.get("sizes") == "shoe":
                a = rng.randrange(0, 5)
                size_list = SHOE[a:a + rng.randrange(5, 12 - a)]
            del sizes
            colours = rng.sample(COLOURS, rng.randrange(1, 4)) if spec.get("colours", True) else []
            options = (["Size"] if size_list else []) + (["Color"] if colours else [])
            variants = []
            for size in size_list or [None]:
                for colour in colours or [None]:
                    vid += 1
                    opts = {}
                    if size:
                        opts["Size"] = f"EU {size}" if spec.get("sizes") == "shoe" else size
                    if colour:
                        opts["Color"] = colour
                    variants.append({"id": str(vid), "sku": f"EV-{vid}", "options": opts,
                                     "inventory": 0 if rng.random() < 0.25 else rng.randrange(1, 13)})
            handle = title.lower().replace("°c", "c").replace(" ", "-").replace(".", "")
            products.append({
                "handle": handle, "title": title, "category": spec["cat"], "price": _price(rng, *spec["price"]),
                "compare_at_price": None, "rating": round(rng.uniform(3.6, 5.0), 1), "reviews": rng.randrange(3, 400),
                "tags": tags, "description": " ".join(sentences), "features": features,
                "materials": rng.choice(["Recycled polyester", "Nylon ripstop", "Merino wool blend", "Aluminium",
                                         "Full-grain leather and mesh", "Recycled nylon"]),
                "care": "", "fit": "", "options": options, "variants": variants,
                "_type": type_key, "_attrs": attrs,
            })
    return products


JACKETS = ["rain_jacket", "down_jacket", "synthetic_jacket", "softshell", "windbreaker"]
BACKPACKS = ["daypack", "trekking_pack", "running_vest"]
# (query, group, spec): types are fully relevant, related types count as partly relevant.
QUERIES: list[tuple[str, str, dict]] = [
    ("waterproof jacket", "plain", dict(types=JACKETS, attrs=["waterproof"])),
    ("down jacket", "plain", dict(types=["down_jacket"], related=["synthetic_jacket"])),
    ("hiking boots", "plain", dict(types=["hiking_boots"], related=["winter_boots", "approach_shoes"])),
    ("trail running shoes", "plain", dict(types=["trail_shoes"])),
    ("daypack", "plain", dict(types=["daypack"], related=["trekking_pack"])),
    ("2 person tent", "plain", dict(types=["tent"], attrs=["2p"])),
    ("sleeping bag", "plain", dict(types=["sleeping_bag"])),
    ("headlamp", "plain", dict(types=["headlamp"])),
    ("merino base layer", "plain", dict(types=["base_layer"], attrs=["merino"])),
    ("fleece jacket", "plain", dict(types=["fleece"], related=JACKETS)),
    ("trekking poles", "plain", dict(types=["poles"])),
    ("insulated gloves", "plain", dict(types=["gloves"], attrs=["insulated"])),
    ("winter boots", "plain", dict(types=["winter_boots"], related=["hiking_boots"])),
    ("camping stove", "plain", dict(types=["stove"])),
    ("windproof jacket", "plain", dict(types=JACKETS, attrs=["windproof"])),
    ("lightweight backpack", "plain", dict(types=BACKPACKS, attrs=["lightweight"])),
    ("rain coat", "synonym", dict(types=["rain_jacket"], related=JACKETS, attrs=["waterproof"])),
    ("puffer", "synonym", dict(types=["down_jacket"], related=["synthetic_jacket"])),
    ("rucksack for day hikes", "synonym", dict(types=["daypack"], related=["trekking_pack"])),
    ("head torch", "synonym", dict(types=["headlamp"])),
    ("trainers for trail running", "synonym", dict(types=["trail_shoes"])),
    ("thermal top", "synonym", dict(types=["base_layer"])),
    ("flask", "synonym", dict(types=["bottle"])),
    ("mittens", "synonym", dict(types=["gloves"])),
    ("trousers for hiking", "synonym", dict(types=["hiking_pants"])),
    ("waterprof jaket", "typo", dict(types=JACKETS, attrs=["waterproof"])),
    ("hikng boots", "typo", dict(types=["hiking_boots"], related=["winter_boots", "approach_shoes"])),
    ("sleping bag", "typo", dict(types=["sleeping_bag"])),
    ("headlmap", "typo", dict(types=["headlamp"])),
    ("backpak", "typo", dict(types=BACKPACKS)),
    ("fleese", "typo", dict(types=["fleece"])),
    ("trekking polls", "typo", dict(types=["poles"])),
    ("tnet", "typo", dict(types=["tent"])),
    ("водоустойчиво яке", "bulgarian", dict(types=JACKETS, attrs=["waterproof"])),
    ("туристически обувки", "bulgarian", dict(types=["hiking_boots"], related=["trail_shoes", "approach_shoes"])),
    ("раница", "bulgarian", dict(types=BACKPACKS)),
    ("палатка", "bulgarian", dict(types=["tent"])),
    ("спален чувал", "bulgarian", dict(types=["sleeping_bag"])),
    ("челник", "bulgarian", dict(types=["headlamp"])),
    ("ръкавици", "bulgarian", dict(types=["gloves"])),
    ("пухено яке", "bulgarian", dict(types=["down_jacket"], related=["synthetic_jacket"])),
    ("waterproof jacket size M", "filters", dict(types=JACKETS, attrs=["waterproof"], size="M")),
    ("hiking boots size 43", "filters", dict(types=["hiking_boots"], related=["winter_boots", "approach_shoes"],
                                             size="EU 43")),
    ("blue backpack", "filters", dict(types=BACKPACKS, colour="blue")),
    ("down jacket under 200", "filters", dict(types=["down_jacket"], related=["synthetic_jacket"], max_price=200)),
    ("tent under €300", "filters", dict(types=["tent"], max_price=300)),
    ("trail shoes size 38 under 120", "filters", dict(types=["trail_shoes"], size="EU 38", max_price=120)),
    ("red fleece in L", "filters", dict(types=["fleece"], size="L", colour="red")),
    ("black beanie", "filters", dict(types=["beanie"], colour="black")),
    ("jacket between 100 and 200", "filters", dict(types=JACKETS, min_price=100, max_price=200)),
    ("яке размер L под 150", "filters", dict(types=JACKETS, size="L", max_price=150)),
    ("red jacket", "filters", dict(types=JACKETS, colour="red")),
    ("winter boots 44 in stock", "filters", dict(types=["winter_boots"], size="EU 44", in_stock=True)),
    ("lightweight tent", "mixed", dict(types=["tent"], attrs=["lightweight"])),
    ("warm jacket for winter", "mixed", dict(types=["down_jacket", "synthetic_jacket"], related=JACKETS,
                                             attrs=["insulated"])),
    ("packable down jacket", "mixed", dict(types=["down_jacket"], attrs=["packable"])),
    ("ultralight running vest", "mixed", dict(types=["running_vest"], attrs=["lightweight"])),
    ("waterproof hiking boots", "mixed", dict(types=["hiking_boots"], related=["winter_boots", "trail_shoes"],
                                              attrs=["waterproof"])),
]


def passes_filters(p: dict, spec: dict) -> bool:
    if spec.get("max_price") is not None and p["price"] > spec["max_price"]:
        return False
    if spec.get("min_price") is not None and p["price"] < spec["min_price"]:
        return False
    if not (spec.get("size") or spec.get("colour") or spec.get("in_stock")):
        return True
    for v in p["variants"]:
        if spec.get("size") and v["options"].get("Size") != spec["size"]:
            continue
        if spec.get("colour") and colour_family(v["options"].get("Color")) != spec["colour"]:
            continue
        if spec.get("in_stock") and v["inventory"] <= 0:
            continue
        return True
    return False


def grade(p: dict, spec: dict) -> int:
    if not passes_filters(p, spec):
        return 0
    has_attrs = all(a in p["_attrs"] for a in spec.get("attrs", []))
    if p["_type"] in spec["types"]:
        return 2 if has_attrs else 1
    if p["_type"] in spec.get("related", []) and has_attrs:
        return 1
    return 0


def main() -> int:
    products = build_catalog()
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "eval").mkdir(exist_ok=True)
    catalog = {"note": "Generated by scripts/build_eval_catalog.py (seed %d). Fields starting with _ are ground "
                       "truth for the evaluation and are never read by the search engine." % SEED,
               "products": products}
    (ROOT / "data" / "eval_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n",
                                                     encoding="utf-8", newline="\n")
    queries = []
    for text, group, spec in QUERIES:
        qrels = {p["handle"]: g for p in products if (g := grade(p, spec))}
        queries.append({"query": text, "group": group, "spec": spec, "qrels": qrels})
    (ROOT / "eval" / "search_queries.json").write_text(json.dumps(queries, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8", newline="\n")
    empty = [q["query"] for q in queries if not q["qrels"]]
    print(f"{len(products)} products, {len(queries)} queries, "
          f"{sum(len(q['qrels']) for q in queries)} relevance labels; queries without relevant products: {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
