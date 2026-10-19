#!/usr/bin/env python3
"""Manage TTSkyAnnot API keys and invite codes (run on the server as ubuntu).

  python3 key_admin.py invite 5 --note "beta 2026-10"     # five invite codes
  python3 key_admin.py invite 1 --note "for <name>"
  python3 key_admin.py key free --owner friend --label friend
  python3 key_admin.py key service --owner jiaowa --label jiaowa   # the mini-program forwarder
  python3 key_admin.py list [--all]
  python3 key_admin.py revoke 7           # by id
  python3 key_admin.py revoke ttsky_ab12  # by key prefix shown in list?

The key itself is never stored: only sha256("ttsky|" + key). A lost key is replaced, not recovered.
"""
import argparse
import hashlib
import os
import sqlite3
import sys
import time

DB = "/home/ubuntu/interactions.db"
PREFIX = "ttsky_"
QUOTAS = {"free": (150, 600), "service": (1200, 20000)}
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"          # no 0/O/1/I


def db():
    c = sqlite3.connect(DB, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS api_keys(id INTEGER PRIMARY KEY AUTOINCREMENT,
                 key_hash TEXT UNIQUE, label TEXT, provider TEXT, owner TEXT, created INTEGER,
                 last_used INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0,
                 quota_hour INTEGER, quota_day INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY, created INTEGER,
                 note TEXT, used_by TEXT, used_ts INTEGER)""")
    return c


def key_hash(k):
    return hashlib.sha256(("ttsky|" + k.strip()).encode()).hexdigest()


def issue(provider, owner, quota, label):
    key = PREFIX + os.urandom(16).hex()
    hour, day = QUOTAS.get(quota, QUOTAS["free"])
    c = db()
    c.execute("INSERT INTO api_keys(key_hash,label,provider,owner,created,quota_hour,quota_day) "
              "VALUES(?,?,?,?,?,?,?)",
              (key_hash(key), label or key[-6:], provider, owner, int(time.time()), hour, day))
    c.commit()
    c.close()
    return key, hour, day


def invite(n, note):
    c = db()
    codes = []
    for _ in range(n):
        body = "".join(ALPHABET[os.urandom(1)[0] % len(ALPHABET)] for _ in range(8))
        code = "TT-" + body[:4] + "-" + body[4:]
        c.execute("INSERT INTO invites(code, created, note) VALUES(?,?,?)", (code, int(time.time()), note))
        codes.append(code)
    c.commit()
    c.close()
    return codes


def listing(all_):
    c = db()
    rows = c.execute("SELECT * FROM api_keys" + ("" if all_ else " WHERE revoked=0") +
                     " ORDER BY id").fetchall()
    out = []
    for r in rows:
        used = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_used"])) if r["last_used"] else "-"
        out.append("%3d  %-16s %-8s %-18s %s/%s  last %s  %s" % (
            r["id"], (r["label"] or "")[:16], r["provider"], (r["owner"] or "")[:18],
            r["quota_hour"], r["quota_day"], used, "REVOKED" if r["revoked"] else ""))
    inv = c.execute("SELECT code, note, used_ts FROM invites ORDER BY created DESC LIMIT 20").fetchall()
    c.close()
    return out, inv


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("invite")
    p.add_argument("n", type=int, nargs="?", default=1)
    p.add_argument("--note", default="")
    p = sub.add_parser("key")
    p.add_argument("quota", choices=sorted(QUOTAS))
    p.add_argument("--owner", default="manual")
    p.add_argument("--label", default="")
    p = sub.add_parser("list")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("revoke")
    p.add_argument("target")
    a = ap.parse_args()

    if a.cmd == "invite":
        for code in invite(a.n, a.note):
            print(code)
    elif a.cmd == "key":
        key, hour, day = issue("manual", a.owner, a.quota, a.label)
        print(key)
        print("quota: %d/hour, %d/day" % (hour, day), file=sys.stderr)
    elif a.cmd == "list":
        rows, inv = listing(a.all)
        print("\n".join(rows) or "(no keys)")
        if inv:
            print("\nrecent invites:")
            for r in inv:
                print("  %-12s %-24s %s" % (r["code"], (r["note"] or "")[:24],
                                            time.strftime("%Y-%m-%d", time.localtime(r["used_ts"])) if r["used_ts"] else "unused"))
    elif a.cmd == "revoke":
        c = db()
        if a.target.isdigit():
            cur = c.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (int(a.target),))
        else:
            cur = c.execute("UPDATE api_keys SET revoked=1 WHERE key_hash=?", (key_hash(a.target),))
        c.commit()
        c.close()
        print("revoked %d key(s)" % cur.rowcount)


if __name__ == "__main__":
    main()
