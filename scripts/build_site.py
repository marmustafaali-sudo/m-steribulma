#!/usr/bin/env python3
"""
Regenerate index.html from templates/index.template.html + data/customers.json.

This exists so the daily automation only ever edits data/customers.json (plain
structured JSON) and never touches the HTML/JS by hand. The template has two
placeholder comments — /*__MUS_BLOCK__*/ and /*__ILETISIM_BLOCK__*/ — that get
replaced with freshly-generated JS literals built straight from the JSON, in
the exact format the site's existing JS code already expects (same variable
names, same per-row shape). Nothing else in the file is touched.

Run after every change to data/customers.json:
    python3 scripts/build_site.py
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, "templates", "index.template.html")
DATA_PATH = os.path.join(ROOT, "data", "customers.json")
OUTPUT_PATH = os.path.join(ROOT, "index.html")


def js_string(s):
    """Escape a Python string for embedding in a single-quoted JS string literal."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def js_str_array(items):
    return "[" + ",".join(js_string(x) for x in items) + "]"


def build_mus_block(customers):
    rows = []
    for c in customers:
        row = "  [{ad},{konum},{bolge},{sektor},{tip},{lat},{lng},{not_}]".format(
            ad=js_string(c["ad"]),
            konum=js_string(c["konum"]),
            bolge=js_string(c["bolge"]),
            sektor=js_str_array(c["sektor"]),
            tip=js_string(c["tip"]),
            lat=repr(float(c["lat"])),
            lng=repr(float(c["lng"])),
            not_=js_string(c.get("not", "")),
        )
        rows.append(row)
    array_literal = "[\n" + ",\n".join(rows) + "\n]"
    return (
        "let MUS = " + array_literal +
        ".map((r,i)=>({id:'M'+i, ad:r[0], konum:r[1], bolge:r[2], sektor:r[3], tip:r[4],\n"
        "                lat:r[5], lng:r[6], not:r[7], dur:0, ilt:''}));"
    )


def build_iletisim_block(customers):
    entries = []
    for c in customers:
        contact = c.get("iletisim", "")
        if contact:
            entries.append("  {}:{}".format(js_string(c["ad"]), js_string(contact)))
    obj_literal = "{\n" + ",\n".join(entries) + "\n}" if entries else "{}"
    return (
        "const ILETISIM = " + obj_literal + ";\n"
        "MUS.forEach(m => { if(ILETISIM[m.ad]) m.ilt = ILETISIM[m.ad]; });"
    )


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        customers = json.load(f)

    seen = set()
    for c in customers:
        if c["ad"] in seen:
            raise ValueError(f"Duplicate company name in customers.json: {c['ad']}")
        seen.add(c["ad"])
        for required in ("ad", "konum", "bolge", "sektor", "tip", "lat", "lng"):
            if required not in c:
                raise ValueError(f"Customer {c.get('ad', '?')} missing field: {required}")

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    mus_block = build_mus_block(customers)
    iletisim_block = build_iletisim_block(customers)

    if "/*__MUS_BLOCK__*/" not in template:
        raise ValueError("Template missing __MUS_BLOCK__ placeholder")
    if "/*__ILETISIM_BLOCK__*/" not in template:
        raise ValueError("Template missing __ILETISIM_BLOCK__ placeholder")

    output = template.replace("/*__MUS_BLOCK__*/", mus_block)
    output = output.replace("/*__ILETISIM_BLOCK__*/", iletisim_block)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"Built index.html from {len(customers)} customers.")


if __name__ == "__main__":
    main()
