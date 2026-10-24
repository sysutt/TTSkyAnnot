import argparse, json, os, sys, time
sys.path.insert(0, r"E:\SkyAnnot")
from skyannot import vizier

def load_fields(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    out = []
    for r in rows:
        try:
            out.append({"ra": float(r["ra_c"]), "dec": float(r["dec_c"]), "n": int(r["n"]),
                        "fov": float(r["fov"] or 0), "scale": float(r["scale"] or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", default=r"E:\SkyAnnot\.scratch95\fields.json")
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--out", default=r"E:\SkyAnnot\.scratch95\field_index.json")
    ap.add_argument("--cache", default=r"E:\SkyAnnot\.scratch95\fields")
    ap.add_argument("--include", default="61.0,26.5")
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    fields = sorted(load_fields(args.fields), key=lambda f: -f["n"])[:args.top]
    if args.include:
        ira, idec = [float(v) for v in args.include.split(",")]
        extra = [f for f in load_fields(args.fields) if abs(f["ra"] - ira) < 0.4 and abs(f["dec"] - idec) < 0.4]
        for f in extra:
            if f not in fields:
                fields.append(f)
    print("fields to build:", len(fields))
    index = []
    for i, f in enumerate(fields):
        key = "%.2f_%.2f" % (f["ra"], f["dec"])
        fp = os.path.join(args.cache, key + ".json")
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as fh:
                index.append(json.load(fh))
            continue
        radius = min(2.5, max(0.7, (f["fov"] or 2.0) / 2.0 + 0.3))
        t0 = time.time()
        try:
            rows = vizier.cone("gaia", f["ra"], f["dec"], radius, mag_max=14.0, max_rows=50000, log=lambda m: None)
        except Exception as e:
            print("  %3d/%3d %s FAILED %s" % (i + 1, len(fields), key, str(e)[:60]))
            continue
        stars = []
        for r in rows:
            try:
                ra = float(r["RA_ICRS"]); de = float(r["DE_ICRS"]); g = float(r["Gmag"])
            except (KeyError, TypeError, ValueError):
                continue
            stars.append([ra, de, g])
        stars.sort(key=lambda s: s[2])
        entry = dict(f, stars=stars[:60])
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
        index.append(entry)
        print("  %3d/%3d %s n=%d stars=%d %.1fs" % (i + 1, len(fields), key, f["n"], len(entry["stars"]), time.time() - t0), flush=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    print("written", args.out, len(index), "fields")

main()
