"""Which deep-sky catalogue an object is listed under, and the order that decides it.

Annotation groups are the *catalogues* a picture's objects come from — Messier, NGC, IC, Sharpless and so on —
rather than the kind of object they are. That is how an observer reads such a list: "show me the Messier
objects in this field".

An object can belong to more than one catalogue: M31 is also NGC 224, and NGC 7000 is also Sharpless 2-117.
Listing it twice would double-label the same nebula on the picture and, worse, leave the count ambiguous. So
each object is assigned to exactly one catalogue: the first enabled one in precedence order that lists it.
That order is the familiar one — Messier before NGC before IC, with the smaller, more specialised catalogues
after them — and it is the order the panel shows the toggles in, so the lowest enabled line always wins.

Colours are not part of this: they stay keyed on the object's *type* (nebula, galaxy, cluster, ...), which is
what makes the overlay readable. Only the grouping and the counts follow the catalogue.
"""
from __future__ import annotations

import functools
import re

# (key, label, note) in precedence order: the first enabled catalogue that lists an object claims it.
CATALOGUES = (
    ("m", "Messier", "Messier 星表（OpenNGC）"),
    ("ngc", "NGC", "NGC 星表（OpenNGC）"),
    ("ic", "IC", "IC 星表（OpenNGC）"),
    ("sh2", "Sharpless", "Sharpless HII 区星表（Sh2-）"),
    ("c", "Caldwell", "Caldwell 目录（C-）"),
    ("lbn", "LBN", "Lynds 亮星云（在线获取）"),
    ("ldn", "LDN", "Lynds 暗星云（在线获取）"),
    ("vdb", "VdB", "van den Bergh 反射星云（在线获取）"),
    ("ced", "Ced", "Cederblad 发射星云（在线获取）"),
    ("rcw", "RCW", "RCW 发射星云（在线获取）"),
)

LOCAL = ("m", "ngc", "ic", "sh2")          # the catalogues whose data is bundled
ONLINE = ("lbn", "ldn", "vdb", "ced", "rcw")   # the ones the tickwhale database supplies

KEYS = tuple(k for k, _l, _n in CATALOGUES)
LABELS = {k: l for k, l, _n in CATALOGUES}
NOTES = {k: n for k, _l, n in CATALOGUES}
# One colour per catalogue, used by the panel's cards: it is not the overlay's palette (that follows the type
# of object), just a quick way to tell the catalogues apart.
COLORS = {
    "m": "#FFCF4A", "ngc": "#45D9E6", "ic": "#7FB5FF", "sh2": "#FF7BAE",
    "c": "#9BE56B",
    "lbn": "#B7A2FF", "ldn": "#8A78E8", "vdb": "#6FD3A8", "ced": "#F2A65A", "rcw": "#E8735A",
}


@functools.lru_cache(maxsize=1)
def counts() -> dict:
    """How many records each bundled catalogue holds; the online ones are not counted locally.

    These are the catalogue sizes, not what the precedence rule would assign: NGC 224 is an NGC record even
    though the picture labels it M31. The Messier catalogue is the familiar 110; OpenNGC leaves M102 (the
    disputed NGC 5866) without a Messier number, so it is added back here.
    """
    from . import catalogs
    n = {"ngc": 0, "ic": 0}
    for d in catalogs.openngc():
        up = str(d.name).upper().replace(" ", "")
        if up.startswith("NGC"):
            n["ngc"] += 1
        elif up.startswith("IC"):
            n["ic"] += 1
    numbers = set()
    for d in catalogs.messier():
        num = re.sub(r"\D", "", d.messier or "")
        if num:
            numbers.add(int(num))
    n["m"] = len(numbers) + (0 if 102 in numbers else 1)
    n["sh2"] = len(catalogs.sharpless())
    # Caldwell 没有单独的数据文件：它是 OpenNGC 记录上的 C 编号（addendum.csv 里的 C009 等）
    seen_c = set()
    for d in catalogs.openngc():
        for text in aliases_of(d):
            up = str(text).upper().replace(" ", "")
            if up.startswith("C") and up[1:].isdigit():
                seen_c.add(up)
    n["c"] = len(seen_c)
    return n
PRECEDENCE = {k: i for i, k in enumerate(KEYS)}

_NUM = re.compile(r"(\d+)")


def _field(entry, name: str) -> str:
    """Read a field from either an OpenNGC Dso or a plain dict (the online records are dicts)."""
    if isinstance(entry, dict):
        return entry.get(name) or ""
    return getattr(entry, name, "") or ""


def fix_ra_hours(ra, key):
    """vdB/Ced/RCW/PK 这类目录的赤经是「小时」，乘 15 换成度；LBN/LDN 不同，别动。"""
    try:
        if key in ("vdb", "ced", "rcw") and float(ra) <= 24.0:
            return float(ra) * 15.0
    except (TypeError, ValueError):
        pass
    return ra


def precess_b1950(ra, dec):
    """LBN/LDN 的在线记录是 B1950 坐标，直接当 J2000 会偏 0.5-0.8 度，标注就会离开星云。"""
    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        c = SkyCoord(ra=float(ra) * u.deg, dec=float(dec) * u.deg, frame="fk4", equinox="B1950.0")
        j = c.transform_to("icrs")
        return float(j.ra.deg), float(j.dec.deg)
    except Exception:
        return ra, dec


def aliases_of(entry) -> list:
    """Every designation the record carries: name, NGC/IC number, Messier number and the Identifiers list.

    One object can hold several numbers at once (C009 is also Sh2-155 and NGC 7635-adjacent entries carry both
    an NGC and a Sh2 alias), so the catalogue of an object must be decided from all of them, not from `name`.
    """
    out = []

    def add(v):
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)

    add(_field(entry, "name"))
    add(_field(entry, "ngcic"))
    add(_field(entry, "messier"))
    raw = _field(entry, "Identifiers") or _field(entry, "identifiers") or ""
    if raw:
        for part in str(raw).replace(";", ",").split(","):
            add(part)
    return out


def key_for_designation(text: str) -> str:
    """The catalogue a single designation belongs to ("" when it is not one we know)."""
    up = str(text or "").upper().replace(" ", "")
    if not up:
        return ""
    if up.startswith("M") and up[1:].isdigit():
        return "m"
    if up.startswith("NGC"):
        return "ngc"
    if up.startswith("IC"):
        return "ic"
    if up.startswith(("SH2-", "SH2", "SH-", "SHARPLESS")):
        return "sh2"
    if up.startswith("C") and up[1:].isdigit():
        return "c"
    for prefix, key in (("LBN", "lbn"), ("LDN", "ldn"), ("VDB", "vdb"),
                        ("CED", "ced"), ("RCW", "rcw")):
        if up.startswith(prefix):
            return key
    return ""


def catalogue_of(entry) -> str:
    """Which catalogue an object belongs to, by the priority Messier > NGC > IC > Sh2 > C > LBN > LDN > VdB > Ced > RCW.

    The decision is taken over *all* designations the record carries, not just `name`: an object listed as
    C009 that also carries the alias Sh2-155 belongs to Sharpless, because Sh2 outranks Caldwell.
    """
    if _field(entry, "messier"):
        return "m"
    best = ""
    best_rank = len(KEYS) + 1
    for text in aliases_of(entry):
        key = key_for_designation(text)
        if key and key in KEYS:
            rank = KEYS.index(key)
            if rank < best_rank:
                best, best_rank = key, rank
    return best

def key_of(entry) -> str:
    """A stable identity for an object, so the same object coming from two lists is one object.

    OpenNGC exposes overlapping lists: `messier()` and `ngcic()` both contain M31 (107 objects appear in both),
    as separate records. Keying on object identity would count those twice — and would leave whichever copy
    lost the race out of the annotation entirely — so identity comes from the designation instead.
    """
    messier = _field(entry, "messier")
    if messier:
        return "m:" + messier.upper().replace(" ", "")
    return str(_field(entry, "name")).upper().replace(" ", "")


def unique(entries) -> list:
    """One entry per object, preferring the record from the highest-precedence catalogue it appears in."""
    best = {}
    for e in entries:
        k = key_of(e)
        if not k:
            continue
        rank = PRECEDENCE.get(catalogue_of(e), 99)
        if k not in best or rank < best[k][0]:
            best[k] = (rank, e)
    return [e for _r, e in sorted(best.values(), key=lambda t: t[0])]


def memberships(entries) -> dict:
    """Every catalogue each object appears in, keyed by object key (see key_of).

    An object can hold more than one designation. OpenNGC keeps one record for it: M31 is a single row with
    messier="M31" and ngcic="NGC224", and it is listed once, in the Messier group. So membership is read from
    every designation the record carries, which is also what lets an object fall through to the next catalogue
    when a higher one is switched off.
    """
    out = {}
    for e in entries:
        keys = []
        if _field(e, "messier"):
            keys.append("m")
        ngcic = str(_field(e, "ngcic")).upper().replace(" ", "")
        if ngcic.startswith("NGC"):
            keys.append("ngc")
        elif ngcic.startswith("IC"):
            keys.append("ic")
        # 每一个编号都算成员（C009 同时属于 Sh2 与 Caldwell），顺序按优先级排列，
        # 这样关掉高优先级目录时会自然落到下一个
        for text in aliases_of(e):
            k2 = key_for_designation(text)
            if k2 and k2 not in keys:
                keys.append(k2)
        keys.sort(key=lambda k: KEYS.index(k) if k in KEYS else 99)
        if keys:
            out.setdefault(key_of(e), [])
            for k in keys:
                if k not in out[key_of(e)]:
                    out[key_of(e)].append(k)
    return out


def assign(entries, enabled) -> dict:
    """Map each object key to the catalogue it is listed under, following the precedence rule.

    `enabled` is the set of catalogue keys the user switched on. An object is listed under the first *enabled*
    catalogue in precedence order that holds it, so switching Messier off hands M31 to NGC rather than making
    it vanish; an object no enabled catalogue holds is left out of the map entirely and the caller skips it.
    """
    on = set(enabled)
    out = {}
    for e, keys in memberships(entries).items():
        for k in keys:
            if k in on:
                out[e] = k
                break
    return out


def sort_key(entry, catalogue: str):
    """Catalogue order, then the catalogue number, so a list reads M1, M2, ... and NGC 1, NGC 2, ..."""
    name = _field(entry, "name")
    m = _NUM.search(name)
    return (PRECEDENCE.get(catalogue, 99), int(m.group(1)) if m else 0, name)


def enabled_from(saved, default_all=True) -> list:
    """The enabled catalogues from what a config file holds, tolerating configs written before this scheme.

    An older config lists group keys such as "galaxy"/"nebula"; none of them match a catalogue, so taking it
    literally would start the program with every group switched off and nothing drawn. Anything unrecognised
    therefore falls back to the default rather than to silence.
    """
    if not saved:
        return list(KEYS) if default_all else []
    kept = [k for k in KEYS if k in saved]
    if not kept and default_all:
        return list(KEYS)
    return kept
