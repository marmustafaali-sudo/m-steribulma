"""
Offline city geocoding for the daily automation.

Why offline: this sandbox's outbound network is restricted to an allowlist
(PyPI, npm, GitHub, a handful of others) — live geocoding APIs like Nominatim
are blocked at the network layer, and Nominatim's own robots.txt blocks the
WebFetch tool too. So instead of guessing coordinates by hand (which is
exactly the kind of fabrication this whole project has avoided everywhere
else), we ship a real, static city database (geonamescache, ~34k cities)
and geocode against that.

Small towns/villages below the database's population cutoff won't resolve
directly. In that case we fall back to the country's centroid and say so —
callers should surface that as an approximation, never as a precise pin.
"""
import unicodedata

import geonamescache

_gc = geonamescache.GeonamesCache()
_cities = list(_gc.get_cities().values())
_countries = _gc.get_countries()

# geonamescache country codes we actually need, keyed by the Turkish country
# names used throughout this project's research files.
COUNTRY_NAME_TO_ISO2 = {
    "fransa": "FR", "france": "FR",
    "romanya": "RO", "bulgaristan": "BG", "sırbistan": "RS", "sirbistan": "RS",
    "bosna-hersek": "BA", "bosna": "BA", "hırvatistan": "HR", "hirvatistan": "HR",
    "slovenya": "SI", "yunanistan": "GR", "kosova": "XK", "arnavutluk": "AL",
    "kuzey makedonya": "MK",
    "cezayir": "DZ", "fas": "MA", "mısır": "EG", "misir": "EG",
    "ürdün": "JO", "urdun": "JO", "ırak": "IQ", "irak": "IQ",
    "suudi arabistan": "SA", "s. arabistan": "SA", "bae": "AE",
    "gürcistan": "GE", "gurcistan": "GE", "azerbaycan": "AZ",
    "abd": "US", "amerika": "US", "türkiye": "TR", "turkiye": "TR",
    "italya": "IT", "almanya": "DE",
}

US_STATE_NAME_TO_ABBR = {
    "michigan": "MI", "pennsylvania": "PA", "alabama": "AL", "ohio": "OH",
    "missouri": "MO", "arkansas": "AR", "indiana": "IN", "florida": "FL",
    "iowa": "IA", "texas": "TX", "colorado": "CO", "california": "CA",
    "utah": "UT", "kentucky": "KY", "illinois": "IL", "south carolina": "SC",
    "new york": "NY", "virginia": "VA", "maryland": "MD", "mississippi": "MS",
    "arizona": "AZ",
}


def _strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _norm(s):
    return _strip_accents(s or "").lower().strip()


def resolve_country_iso2(country_name_tr):
    return COUNTRY_NAME_TO_ISO2.get(_norm(country_name_tr))


def geocode_city(city_name, country_name_tr=None, us_state_name=None):
    """
    Returns (lat, lng, approximate: bool, method: str).

    Tries, in order:
      1. Exact city-name match within the resolved country (+ US state if given)
      2. Exact city-name match anywhere (only if unambiguous - single match)
      3. Country centroid fallback (approximate=True)

    Raises ValueError if even the country can't be resolved - that's a real
    "can't place this on the map" case that should stop the pipeline for that
    record rather than silently drop a pin at (0,0).
    """
    iso2 = resolve_country_iso2(country_name_tr) if country_name_tr else None
    target_city = _norm(city_name)

    # Some of our source data writes city like "Bethioua, Oran" or
    # "Batna (Ain Touta)" - split on commas/parens into candidate tokens
    # (settlement name, then the larger nearby city/province it's filed
    # under) and try each in turn, since the smaller settlement is often
    # below the database's population cutoff but the second token resolves.
    normalized = target_city.replace("(", ",").replace(")", ",").replace("/", ",")
    tokens = [t.strip() for t in normalized.split(",")]
    tokens = [t for t in tokens if t]

    for tok in tokens:
        candidates = [
            c for c in _cities
            if _norm(c["name"]) == tok and (not iso2 or c["countrycode"] == iso2)
        ]

        if us_state_name and iso2 == "US":
            abbr = US_STATE_NAME_TO_ABBR.get(_norm(us_state_name))
            state_matches = [c for c in candidates if abbr and c.get("admin1code") == abbr]
            if state_matches:
                candidates = state_matches

        if candidates:
            best = max(candidates, key=lambda c: c.get("population", 0))
            return best["latitude"], best["longitude"], False, "exact"

        # Unscoped fallback: unambiguous global match on this token
        global_matches = [c for c in _cities if _norm(c["name"]) == tok]
        if len(global_matches) == 1:
            best = global_matches[0]
            return best["latitude"], best["longitude"], False, "exact-global"

    # State-level fallback (US only, when we at least know the state):
    # the largest city in that state is a much better approximation than
    # jumping all the way to the national largest city.
    if us_state_name and iso2 == "US":
        abbr = US_STATE_NAME_TO_ABBR.get(_norm(us_state_name))
        state_cities = [c for c in _cities if c["countrycode"] == "US" and c.get("admin1code") == abbr]
        if state_cities:
            best = max(state_cities, key=lambda c: c.get("population", 0))
            return best["latitude"], best["longitude"], True, "state-fallback"

    # Country centroid fallback - geonamescache doesn't ship centroids, so
    # approximate using its largest/capital city instead.
    if iso2:
        country_cities = [c for c in _cities if c["countrycode"] == iso2]
        if country_cities:
            best = max(country_cities, key=lambda c: c.get("population", 0))
            return best["latitude"], best["longitude"], True, "country-fallback"

    raise ValueError(
        f"Could not geocode '{city_name}' / country='{country_name_tr}' - "
        f"resolve manually before adding to customers.json"
    )
