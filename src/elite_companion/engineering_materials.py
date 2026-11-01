"""What an engineering blueprint costs in materials, grade by grade, against what they hold.

Ingredient lists per roll come from EDEngineer (data/engineers.json). A grade usually takes several rolls
to fill, and the number varies, so "rolls you can afford" is the useful figure.
"""

from __future__ import annotations

from . import engineers


def _squash(s: str) -> str:
    """Lower-case letters and digits only, for forgiving name matches."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def blueprint_name(module_type: str, query: str | None) -> list[str]:
    """Blueprints for this module type that match `query` (all of them when no query). Exact
    matches win; otherwise any blueprint whose name contains the words, or the other way round.
    Experimental effects are left out: they aren't blueprints.
    """
    bps = [b for b, g in engineers.data()["ingredients"].get(module_type, {}).items() if any(k != "experimental" for k in g)]
    if not query:
        return bps
    q = _squash(query)
    exact = [b for b in bps if _squash(b) == q]
    return exact or [b for b in bps if q in _squash(b) or _squash(b) in q]


def held_counts(materials: dict) -> dict[str, int]:
    """GameState.materials ({category: {internal: (display, count)}}) -> {display lower: count}."""
    out = {}
    for items in materials.values():
        for internal, (display, count) in items.items():
            out[_squash(display)] = count
            out[_squash(internal)] = count
    return out


def check(module: str, blueprint: str | None, held: dict, up_to_grade: int = 5) -> dict:
    """Grade by grade (up to `up_to_grade`): materials per roll, what's held, how many rolls that pays
    for, and what's short for one roll. Also the total shortfall across grades. With several
    matching blueprints and none named, returns the list to choose from.
    """
    t = engineers.module_type(module)
    if not t or t not in engineers.data()["ingredients"]:
        return {"module": module, "error": f"No engineering blueprints found for {module}."}
    names = blueprint_name(t, blueprint)
    if not names:
        return {"module": t, "error": f"No blueprint like '{blueprint}' for {t}.",
                "blueprints": blueprint_name(t, None)}
    if len(names) > 1 and not blueprint:
        return {"module": t, "blueprints": names, "note": "Say which blueprint to check the materials for."}
    bp = names[0]
    grades = engineers.data()["ingredients"][t][bp]
    rows, shopping = [], {}
    for g in sorted((k for k in grades if k != "experimental"), key=int):
        if int(g) > up_to_grade:
            continue
        need = grades[g]
        have = {m: held.get(_squash(m), 0) for m in need}
        rolls = min((have[m] // q for m, q in need.items()), default=0)
        short = {m: q - have[m] for m, q in need.items() if have[m] < q}
        for m in short:
            shopping[m] = shopping.get(m, 0) + short[m]
        rows.append({"grade": int(g), "per_roll": need, "you_have": have, "rolls_you_can_afford": rolls,
                     "short_for_one_roll": short or None})
    return {"module": t, "blueprint": bp, "grades": rows,
            "missing_for_one_roll_each": shopping or None,
            "experimental_cost": grades.get("experimental"),
            "note": "Costs are per roll; a grade usually takes a few rolls to fill (fewer at higher engineer rank)."}


def experimental_cost(module: str, effect: str, held: dict) -> dict:
    """What one experimental effect costs, what's held and what's short."""
    t = engineers.module_type(module)
    effects = {e: g["experimental"] for e, g in engineers.data()["ingredients"].get(t or "", {}).items()
               if "experimental" in g}
    hit = next((e for e in effects if _squash(effect) in _squash(e)), None)
    if not hit:
        return {"module": t or module, "error": f"No experimental like '{effect}'.", "experimentals": sorted(effects)}
    need = effects[hit]
    have = {m: held.get(_squash(m), 0) for m in need}
    return {"module": t, "experimental": hit, "cost": need, "you_have": have,
            "short": {m: q - have[m] for m, q in need.items() if have[m] < q} or None}


MATERIALS_SPEC = {
    "name": "check_engineering_materials",
    "description": "Materials for an engineering blueprint, grade by grade, against what they hold: cost per roll, "
                   "how many rolls they can afford at each grade, and what's missing. Give module (e.g. 'shield "
                   "generator') and blueprint (e.g. 'reinforced'); or experimental (e.g. 'hi-cap') for an "
                   "experimental effect's cost. Use for 'can I do grade 4 reinforced shields?', 'what do I need for "
                   "boosters?'.",
    "input_schema": {"type": "object", "properties": {
        "module": {"type": "string"}, "blueprint": {"type": "string"}, "experimental": {"type": "string"},
        "up_to_grade": {"type": "integer"}}, "required": ["module"]},
}
