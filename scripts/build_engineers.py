"""Build src/elite_companion/data/engineers.json from EDEngineer's blueprints plus engineer bases (coords from EDSM)."""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "elite_companion" / "data" / "engineers.json"

BASES = {  # ship engineers: system, base
    "Felicity Farseer": ("Deciat", "Farseer Inc"),
    "Elvira Martuuk": ("Khun", "Long Sight Base"),
    "The Dweller": ("Wyrd", "Black Hide"),
    "Liz Ryder": ("Eurybia", "Demolition Unlimited"),
    "Tod McQuinn": ("Wolf 397", "Trophy Camp"),
    "Juri Ishmaak": ("Giryak", "Pater's Memorial"),
    "Zacariah Nemo": ("Yoru", "Nemo Cyber Party Base"),
    "Lei Cheung": ("Laksak", "Trader's Rest"),
    "Selene Jean": ("Kuk", "Prospector's Rest"),
    "Hera Tani": ("Kuwemaki", "The Jet's Hole"),
    "Broo Tarquin": ("Muang", "Broo's Legacy"),
    "Marco Qwent": ("Sirius", "Qwent Research Base"),
    "Ram Tah": ("Meene", "Phoenix Base"),
    "Lori Jameson": ("Shinrarta Dezhra", "Jameson Base"),
    "Tiana Fortune": ("Achenar", "Fortune's Loss"),
    "The Sarge": ("Beta-3 Tucani", "The Beach"),
    "Didi Vatermann": ("Leesti", "Vatermann LLC"),
    "Bill Turner": ("Alioth", "Turner Metallics Inc"),
    "Colonel Bris Dekker": ("Sol", "Dekker's Yard"),
    "Professor Palin": ("Arque", "Abel Laboratory"),
    "Etienne Dorn": ("Los", "Kraken's Retreat"),
    "Marsha Hicks": ("Tir", "The Watchtower"),
    "Mel Brandon": ("Luchtaine", "The Brig"),
    "Petra Olmanova": ("Asura", "Sanctuary"),
    "Chloe Sedesi": ("Shenve", "Cinder Dock"),
}


UNLOCKS = {
    "Elvira Martuuk": (
        "public knowledge",
        "travel 300 ly from your starting location",
        "donate 3 Soontill Relics"
    ),
    "The Dweller": (
        "public knowledge",
        "deal with 5 black markets",
        "donate 500,000 credits"
    ),
    "Liz Ryder": (
        "public knowledge",
        "be Friendly with Eurybia Blue Mafia",
        "donate 200 landmines"
    ),
    "Felicity Farseer": (
        "public knowledge",
        "reach exploration rank Scout",
        "donate 1 Meta-Alloy"
    ),
    "Tod McQuinn": (
        "public knowledge",
        "earn 15 bounty vouchers",
        "hand in 100,000 credits of bounty vouchers"
    ),
    "Zacariah Nemo": (
        "Elvira Martuuk",
        "get an invitation from the Party of Yoru",
        "donate 25 Xihe Companions"
    ),
    "Lei Cheung": (
        "The Dweller",
        "trade on 50 different markets",
        "donate 200 Gold"
    ),
    "Hera Tani": (
        "Liz Ryder",
        "reach Empire rank Outsider",
        "donate 50 Kamitra Cigars"
    ),
    "Juri Ishmaak": (
        "Felicity Farseer",
        "earn 50 combat bonds",
        "hand in combat bonds (the wiki lists both 100,000 and 1,000,000 credits)"
    ),
    "Selene Jean": (
        "Tod McQuinn",
        "mine 500 tons of ore",
        "donate 10 Painite"
    ),
    "Marco Qwent": (
        "Elvira Martuuk",
        "get an invitation from Sirius Corporation (Sirius needs a permit)",
        "donate 25 Modular Terminals"
    ),
    "Ram Tah": (
        "Lei Cheung",
        "reach exploration rank Surveyor",
        "donate 50 Classified Scan Databanks"
    ),
    "Broo Tarquin": (
        "Hera Tani",
        "reach combat rank Competent",
        "donate 50 Fujin Tea"
    ),
    "Colonel Bris Dekker": (
        "Juri Ishmaak",
        "be Friendly with the Federation (Sol needs a permit)",
        "hand in combat bonds (the wiki lists both 1,000,000 and 10,000,000 credits)"
    ),
    "Didi Vatermann": (
        "Selene Jean",
        "reach trade rank Merchant",
        "donate 50 Lavian Brandy"
    ),
    "Professor Palin": (
        "Marco Qwent",
        "travel 5,000 ly from your starting location",
        "donate 25 Unknown Fragments (he has since moved to Arque; check in game)"
    ),
    "Lori Jameson": (
        "Marco Qwent",
        "reach combat rank Dangerous (Shinrarta Dezhra needs a permit)",
        "donate 25 Kongga Ale"
    ),
    "Tiana Fortune": (
        "Hera Tani",
        "be Friendly with the Empire (Achenar needs a permit)",
        "donate 50 Decoded Emission Data"
    ),
    "The Sarge": (
        "Juri Ishmaak",
        "reach Federal Navy rank Midshipman",
        "donate 50 Aberrant Shield Pattern Analysis"
    ),
    "Bill Turner": (
        "Selene Jean",
        "be Friendly with the Alliance (Alioth needs a permit)",
        "donate 50 Bromellite"
    )
}


def coords(system):
    url = "https://www.edsm.net/api-v1/system?" + urllib.parse.urlencode({"systemName": system, "showCoordinates": 1})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "elite-companion"}), timeout=20) as r:
        d = json.load(r)
    return d.get("coords") if isinstance(d, dict) else None


SOURCE = "https://raw.githubusercontent.com/msarilar/EDEngineer/master/EDEngineer/Resources/Data/blueprints.json"
with urllib.request.urlopen(SOURCE, timeout=60) as r:
    blueprints = json.loads(r.read().decode("utf-8-sig"))
modules: dict = {}
ingredients: dict = {}
names_seen = set()
for b in blueprints:
    t, name, grade = b.get("Type"), b.get("Name"), b.get("Grade")
    eng = [e for e in b.get("Engineers", []) if not e.startswith("@")]
    if not t or not name or not eng or t in ("Suit", "Weapon", "Unlock"):
        continue
    names_seen.update(eng)
    entry = modules.setdefault(t, {}).setdefault(name, {})
    key = str(grade) if grade else "experimental"
    entry[key] = sorted(set(entry.get(key, [])) | set(eng))
    # what one roll of this blueprint grade (or the experimental) costs, in materials
    ingredients.setdefault(t, {}).setdefault(name, {})[key] = {i["Name"]: i["Size"] for i in b.get("Ingredients", [])}

engineers = {}
for name, (system, base) in BASES.items():
    c = coords(system)
    time.sleep(0.3)
    engineers[name] = {"system": system, "base": base, "coords": [c["x"], c["y"], c["z"]] if c else None,
                       "colonia": system in ("Los", "Tir", "Luchtaine", "Asura", "Shenve")}
    if name in UNLOCKS:
        via, invite, unlock = UNLOCKS[name]
        engineers[name]["unlock"] = {"found_via": via, "invitation": invite, "unlock": unlock}
missing = sorted(n for n in names_seen if n not in BASES)
OUT.parent.mkdir(exist_ok=True)
json.dump({"source": "Blueprints: EDEngineer (github.com/msarilar/EDEngineer, data of 2024-03). Bases: hand table "
                     "(2026-10), coordinates from EDSM. " + "Unlock requirements: Alioth Elite wiki (wiki.alioth.net/index.php/Elite_4/Engineers), community data that may be dated. Colonia engineers aren't covered.",
           "engineers": engineers, "modules": modules, "ingredients": ingredients}, open(OUT, "w", encoding="utf-8"), indent=0)
print("module types", len(modules), "engineers", len(engineers), "no coords:",
      [n for n, e in engineers.items() if not e["coords"]], "engineers in blueprints without a base:", missing)
