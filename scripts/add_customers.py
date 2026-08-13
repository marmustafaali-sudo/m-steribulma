#!/usr/bin/env python3
"""
Daily automation worker: pop the next N candidates off data/backfill_manifest.json's
queue, geocode them, dedupe against data/customers.json, append, and rebuild index.html.

Usage:
    python3 scripts/add_customers.py [--limit N] [--dry-run]

Design notes:
- The manifest (data/backfill_manifest.json) is the persistent state between daily
  runs - it lives in the repo, not in any session's memory, since each day's
  automation run is a fresh session with no recollection of prior days.
- Dedup is by normalized company name against the existing customers.json, so a
  company already on the site (e.g. GICA, Tosyalı Algérie, AQS were already there
  before this pipeline existed) gets silently skipped rather than duplicated.
- Never fabricates a phone/email/website - the 'iletisim' field is only populated
  from what the research batch actually verified; a missing contact detail stays
  blank, exactly like the "never fabricate" rule used throughout this project's
  manual research.
- Geocoding is offline (scripts/geocode.py) since this sandbox can't reach live
  geocoding APIs. Approximate placements are marked in the note text so nobody
  mistakes a country-level fallback for a precise pin.
"""
import argparse
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode import geocode_city  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT, "data", "backfill_manifest.json")
CUSTOMERS_PATH = os.path.join(ROOT, "data", "customers.json")
SOURCES_DIR = os.path.join(ROOT, "data", "backfill_sources")


def _norm_name(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def build_iletisim(candidate):
    parts = []
    tel = candidate.get("telefon", "").strip()
    eposta = candidate.get("eposta", "").strip()
    web = candidate.get("web_sitesi", "").strip()
    if tel:
        parts.append(tel)
    if eposta:
        parts.append(eposta)
    if web:
        parts.append(web.replace("https://", "").replace("http://", "").rstrip("/"))
    return " · ".join(parts)


def build_note(candidate, approximate_location):
    text = candidate.get("degerlendirme", "").strip()
    if approximate_location:
        text = (text + " (harita konumu yaklaşık - küçük yerleşim için il/eyalet merkezine sabitlendi)").strip()
    return text


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="override manifest's per_run count")
    ap.add_argument("--dry-run", action="store_true", help="don't write any files, just print what would happen")
    args = ap.parse_args()

    manifest = load_json(MANIFEST_PATH)
    customers = load_json(CUSTOMERS_PATH)
    existing_names = {_norm_name(c["ad"]) for c in customers}

    limit = args.limit if args.limit is not None else manifest.get("per_run", 4)
    tasks = manifest["tasks"]
    cursor = manifest["cursor"]

    added, skipped_dup, failed = [], [], []
    remaining = limit

    while remaining > 0 and cursor["task_i"] < len(tasks):
        task = tasks[cursor["task_i"]]
        source_path = os.path.join(SOURCES_DIR, task["file"])
        candidates = load_json(source_path)

        if cursor["item_i"] >= len(candidates):
            cursor["task_i"] += 1
            cursor["item_i"] = 0
            continue

        candidate = candidates[cursor["item_i"]]
        cursor["item_i"] += 1

        name = candidate["firma_adi"]
        if _norm_name(name) in existing_names:
            skipped_dup.append(name)
            continue

        city = candidate.get("sehir", "")
        country_or_state_country = candidate.get("ulke") or "Fransa" if task["bolge"] == "Fransa" else candidate.get("ulke")
        # France source records have no 'ulke' field (implicitly France);
        # US source records use 'eyalet' (state) instead of 'ulke'.
        if task["bolge"] == "Fransa":
            country_tr = "Fransa"
            state = None
        elif task["bolge"] == "ABD":
            country_tr = "ABD"
            state = candidate.get("eyalet")
        else:
            country_tr = candidate.get("ulke") or task["bolge"]
            state = None

        try:
            lat, lng, approx, method = geocode_city(city, country_tr, state)
        except ValueError as e:
            failed.append((name, str(e)))
            continue

        if task["bolge"] == "ABD":
            konum = f"{city}, {state or country_tr}"
        elif country_tr and _norm_name(country_tr) in _norm_name(city):
            # source city text already contains the country (common in the
            # France source files), don't double it up
            konum = city
        else:
            konum = f"{city}, {country_tr}"

        new_customer = {
            "ad": name,
            "konum": konum,
            "bolge": task["bolge"],
            "sektor": [task["sektor"]],
            "tip": "Tesis",
            "lat": round(lat, 3),
            "lng": round(lng, 3),
            "not": build_note(candidate, approx),
            "iletisim": build_iletisim(candidate),
        }
        customers.append(new_customer)
        existing_names.add(_norm_name(name))
        added.append({**new_customer, "_geocode_method": method})
        remaining -= 1

    print(f"Added: {len(added)}")
    for a in added:
        print(f"  + {a['ad']} ({a['konum']}) -> {a['lat']},{a['lng']} [{a['_geocode_method']}]")
    if skipped_dup:
        print(f"Skipped (already on site): {len(skipped_dup)} -> {', '.join(skipped_dup)}")
    if failed:
        print(f"Failed to geocode ({len(failed)}):")
        for name, err in failed:
            print(f"  ! {name}: {err}")

    remaining_total = sum(
        len(load_json(os.path.join(SOURCES_DIR, t["file"]))) for t in tasks
    ) - sum(
        (cursor["item_i"] if i == cursor["task_i"] else (len(load_json(os.path.join(SOURCES_DIR, t["file"]))) if i < cursor["task_i"] else 0))
        for i, t in enumerate(tasks)
    )
    print(f"Queue remaining: ~{max(remaining_total, 0)} candidates across {len(tasks) - cursor['task_i']} remaining task file(s)")

    if args.dry_run:
        print("[dry-run] not writing customers.json / manifest / index.html")
        return

    if added:
        save_json(CUSTOMERS_PATH, customers)
    save_json(MANIFEST_PATH, manifest)

    if added:
        import subprocess
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_site.py")], check=True)


if __name__ == "__main__":
    main()
