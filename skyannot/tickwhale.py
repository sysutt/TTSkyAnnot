"""Looking up objects in the tickwhale deep-sky database.

A second, optional catalogue source next to the bundled files in catalogs.py. The local catalogues are
OpenNGC plus Sharpless, which hold no Lynds nebulae or the other deep catalogues; the tickwhale database has
them, so it is offered as an extra source for the object-name field.

    POST {base}/weather   {"a": "dso_search", "d": {"catalog_id": "LBN777"}}
    POST {base}/weather   {"a": "dso_search", "d": {"search_text": "LBN 777"}}
      -> {"result": {"list": [ {"id", "type", "ra", "dec", "size_major", "size_minor", "bmag",
                                "constellation_zh", "name_zh", "description_short", ...} ], "total": n}}

No key is needed for this action. Everything here fails soft: the caller gets an empty list and the bundled
catalogues carry on, so the application still works with no network at all.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_BASE = "https://app.tickwhale.com"

# The server's object types, mapped onto the OpenNGC vocabulary the rest of the program already speaks
# (shapes.category() switches on those values). The server also uses variants that are not OpenNGC's, so the
# table is backed by prefix rules in classify(): "DNe", "Nb", "GTrpl" and anything else it grows later still
# land in the right group without an edit here.
TYPE_MAP = {
    "G": "G", "GALAXY": "G", "GPair": "GPair", "GTrpl": "GTrpl", "GGroup": "GGroup",
    "OCL": "OCl", "OC": "OCl", "OPNCL": "OCl", "*ASS": "*Ass", "ASCC": "*Ass",
    "GCL": "GCl", "GC": "GCl", "CL+N": "Cl+N",
    "PN": "PN", "PLNNB": "PN", "HII": "HII", "EMN": "EmN", "NEB": "Neb", "BN": "BN",
    "RFN": "RfN", "REFLN": "RfN", "SNR": "SNR", "DRKN": "DrkN", "DN": "DrkN",
    "***": "*Ass", "STAR": "**", "**": "**", "DBLSTAR": "**",
}
# checked in order; the first prefix that fits decides, so the multi-letter galaxy codes come before "G"
_TYPE_RULES = (("GCL", "GCl"), ("OCL", "OCl"), ("ASS", "*Ass"), ("CL", "Cl+N"),
               ("PN", "PN"), ("PLN", "PN"), ("HII", "HII"), ("EMN", "EmN"), ("RFN", "RfN"),
               ("SNR", "SNR"), ("DRKN", "DrkN"), ("DN", "DrkN"), ("BN", "BN"),
               ("GPAIR", "GPair"), ("GTRPL", "GTrpl"), ("GGROUP", "GGroup"),
               ("NB", "Neb"), ("NEB", "Neb"), ("G", "G"))


def classify_type(raw: str) -> str:
    """The server's type code as an OpenNGC one, by table first and then by prefix."""
    t = (raw or "").strip()
    if t in TYPE_MAP:
        return TYPE_MAP[t]
    up = t.upper()
    for prefix, mapped in _TYPE_RULES:
        if up.startswith(prefix):
            return mapped
    return raw


def _num(value, default=None):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f


def _one(row: dict):
    """One server row as a record this program can use, or None when it carries no usable position."""
    ra, dec = _num(row.get("ra")), _num(row.get("dec"))
    if ra is None or dec is None or not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        return None
    name = str(row.get("id") or "").strip()
    if not name:
        return None
    raw_type = str(row.get("type") or "").strip()
    bmag = _num(row.get("bmag"))
    # the server marks "no magnitude" with a sentinel rather than leaving it out
    if bmag is not None and bmag >= 90.0:
        bmag = None
    return {
        "name": name,
        "ra": ra,
        "dec": dec,
        "type": classify_type(raw_type),
        "raw_type": raw_type,
        "mag": bmag,
        "maj": _num(row.get("size_major")),
        "minr": _num(row.get("size_minor")),
        "common": (str(row.get("name_zh") or "").strip()
                   or str(row.get("description_short") or "").strip()),
        "constellation": str(row.get("constellation_zh") or row.get("constellation") or "").strip(),
        "source": "tickwhale",
    }


def label_for(rec: dict) -> str:
    """How a tickwhale object is shown in the object-name field."""
    extra = rec.get("common") or rec.get("constellation")
    return "%s%s" % (rec["name"], " · %s" % extra if extra else "")


def search(text: str, base: str = "", timeout: float = 6.0, limit: int = 12) -> list:
    """Objects matching `text`, best first. An empty list when the server is unreachable or has nothing.

    A plain string is searched as free text; a catalogue designation (LBN777, M57, Sh2-155) is also tried as
    an exact id first, which is both faster and immune to the free-text ranking.
    """
    text = (text or "").strip()
    if len(text) < 2:
        return []
    url = (base or DEFAULT_BASE).rstrip("/") + "/weather"
    out, seen = [], set()
    for payload in ({"catalog_id": text.replace(" ", "").upper()}, {"search_text": text}):
        for rec in _ask(url, payload, timeout):
            if rec["name"] not in seen:
                seen.add(rec["name"])
                out.append(rec)
        if out:
            break                                    # an exact id hit is the answer; no need for the text pass
    return out[:limit]


def region(base: str = "", ra: float = 0.0, dec: float = 0.0, radius: float = 1.0,
           catalogs=None, limit: int = 500, timeout: float = 8.0) -> list:
    """Every database object inside a cone: centre, radius in degrees, optionally catalogue keys.

    This is the action the annotation uses after a solve, so a field can be filled with the online catalogues
    the way the bundled ones are filled. A server that predates the action answers with an error, `_ask` turns
    that into an empty list and the annotation simply has nothing extra to draw.
    """
    url = (base or DEFAULT_BASE).rstrip("/") + "/weather"
    payload = {"ra": round(float(ra), 6), "dec": round(float(dec), 6),
               "radius": round(float(radius), 6), "limit": int(limit)}
    if catalogs:
        payload["catalogs"] = [str(c) for c in catalogs]
    out, seen = [], set()
    for rec in _ask(url, payload, timeout, action="dso_region"):
        if rec["name"] not in seen:
            seen.add(rec["name"])
            out.append(rec)
    return out


def _ask(url: str, payload: dict, timeout: float, action: str = "dso_search") -> list:
    body = json.dumps({"a": action, "d": payload}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    rows = ((data or {}).get("result") or {}).get("list") or []
    recs = [_one(r) for r in rows if isinstance(r, dict)]
    return [r for r in recs if r]
