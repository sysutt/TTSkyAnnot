"""Merge OpenNGC object sizes into the MySQL ``dso`` table behind app.tickwhale.com.

The desktop app reads sizes from the bundled OpenNGC data, while the online catalogue (jiaowa on tickwhale)
keeps them in a MySQL table. The two disagreed on the objects that matter most -- NGC1499 was 120' online
and 160' in OpenNGC, so overlays drawn from the online catalogue came out a quarter too small.

Rule: **the larger size wins**. A catalogue major axis is often only the bright core (OpenNGC lists M76 at
1.12' while it spans about 2.7'), so a value is only ever raised, never lowered.

Safety:
  * ``--dry-run`` (default) prints what would change and writes nothing;
  * the affected rows' ``ax``/``ay`` are copied into ``dso_ax_backup`` before anything is updated, so
    ``UPDATE dso d JOIN dso_ax_backup b USING (auto_id) SET d.ax=b.ax, d.ay=b.ay`` rolls it back;
  * credentials come from the Laravel ``.env`` on the server, never from arguments.

Usage (on the tickwhale server):

    python3 tools/merge_openngc_sizes_mysql.py --env /var/www/jiaowa/.env --dry-run
    python3 tools/merge_openngc_sizes_mysql.py --env /var/www/jiaowa/.env --apply \\
        --ngc /root/ngc.csv --addendum /root/addendum.csv

GitHub is not reachable from that host: download the two CSVs elsewhere and copy them over.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys

DEFAULT_ENV = "/var/www/jiaowa/.env"
GROWTH = 1.02
BACKUP_TABLE = "dso_ax_backup"


def norm(name: str):
    """Catalogue name → the key both sources agree on (NGC0650 / IC2005 style)."""
    k = (name or "").replace(" ", "").upper()
    if k.startswith("NGC") and k[3:].isdigit():
        return "NGC%04d" % int(k[3:])
    if k.startswith("IC") and k[2:].isdigit():
        return "IC%04d" % int(k[2:])
    return None


def read_env(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    return dict(re.findall(r"^(\w+)=(.*)$", text, re.M))


def mysql_args(env: dict) -> list:
    return ["mysql", "-h" + env.get("DB_HOST", "127.0.0.1"),
            "-P" + env.get("DB_PORT", "3306"),
            "-u" + env["DB_USERNAME"],
            "-p" + env["DB_PASSWORD"].strip().strip('"'),
            env["DB_DATABASE"]]


def sql(args, query: str) -> str:
    r = subprocess.run(args + ["-N", "-B", "-e", query], capture_output=True, text=True)
    if r.returncode:
        print("SQL error: %s" % (r.stderr or "")[:300], file=sys.stderr)
        return ""
    return r.stdout


def read_sizes(paths) -> dict:
    """{key: (maj_arcmin, min_arcmin or None)} from OpenNGC CSV files."""
    out = {}
    for p in paths:
        if not os.path.exists(p):
            print("missing CSV: %s" % p, file=sys.stderr)
            continue
        with open(p, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                key = norm(row.get("Name"))
                if not key:
                    continue
                try:
                    maj = float(row["MajAx"]) if row.get("MajAx") else None
                    mn = float(row["MinAx"]) if row.get("MinAx") else None
                except (TypeError, ValueError):
                    continue
                if maj:
                    out[key] = (maj, mn)
    return out


def plan(conn, sizes, growth) -> list:
    """[(auto_id, id, new_maj, new_min)] for every row that should grow."""
    rows = sql(conn, "SELECT auto_id, id, IFNULL(other_name,''), IFNULL(ax,0), IFNULL(ay,0) FROM dso")
    updates = []
    for line in rows.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        aid, rid, other, ax, ay = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            axf = float(ax)
        except ValueError:
            axf = 0.0
        for nm in (rid, other):
            key = norm(nm)
            if key and key in sizes:
                maj, mn = sizes[key]
                if maj > axf * growth:
                    ayf = float(ay) if ay not in ("", "NULL") else 0.0
                    updates.append((int(aid), rid, maj, max(ayf, mn or 0.0) or None))
                break
    return updates


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=DEFAULT_ENV, help="Laravel .env with the DB credentials")
    ap.add_argument("--ngc", default="/root/ngc.csv", help="OpenNGC NGC.csv")
    ap.add_argument("--addendum", default="/root/addendum.csv", help="OpenNGC addendum.csv")
    ap.add_argument("--growth", type=float, default=GROWTH,
                    help="only grow a value when the new one is this many times larger (default 1.02)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    ap.add_argument("--sql-file", default="/root/dso_updates.sql", help="where to write the UPDATE statements")
    a = ap.parse_args(argv)

    if not os.path.exists(a.env):
        print("no .env at %s" % a.env, file=sys.stderr)
        return 1
    env = read_env(a.env)
    conn = mysql_args(env)

    sizes = read_sizes([a.ngc, a.addendum])
    if not sizes:
        print("no sizes read; nothing to do", file=sys.stderr)
        return 1
    print("OpenNGC sizes: %d objects" % len(sizes))

    updates = plan(conn, sizes, a.growth)
    print("rows to update: %d" % len(updates))
    for aid, rid, maj, mn in updates[:5]:
        print("  %-10s ax=%.2f ay=%s" % (rid, maj, mn))

    if not a.apply:
        print("dry run: nothing written")
        return 0
    if not updates:
        print("nothing to update")
        return 0

    sql(conn, "CREATE TABLE IF NOT EXISTS %s (auto_id INT PRIMARY KEY, ax DECIMAL(10,2), ay DECIMAL(10,2),"
              " saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)" % BACKUP_TABLE)
    ids = ",".join(str(u[0]) for u in updates)
    sql(conn, "REPLACE INTO %s (auto_id, ax, ay) SELECT auto_id, ax, ay FROM dso WHERE auto_id IN (%s)"
        % (BACKUP_TABLE, ids))
    with open(a.sql_file, "w", encoding="utf-8") as fh:
        for aid, rid, maj, mn in updates:
            fh.write("UPDATE dso SET ax=%.2f, ay=%s WHERE auto_id=%d;\n"
                     % (maj, ("%.2f" % mn) if mn else "NULL", aid))
    with open(a.sql_file, encoding="utf-8") as fh:
        r = subprocess.run(conn, stdin=fh, capture_output=True, text=True)
    print("applied (%d statements), rc=%d %s" % (len(updates), r.returncode, (r.stderr or "")[:200]))
    print("backup kept in %s; rollback with:"
          " UPDATE dso d JOIN %s b USING (auto_id) SET d.ax=b.ax, d.ay=b.ay;" % (BACKUP_TABLE, BACKUP_TABLE))
    print("now:", sql(conn, "SELECT id, ax, ay FROM dso WHERE id IN ('NGC1499','M76','NGC7000')"))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
