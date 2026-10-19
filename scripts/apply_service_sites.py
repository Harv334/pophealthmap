"""Apply the GP practice and pharmacy rules in fetch_all_data.py to the files
already on disk.

run_gp_practices() and run_pharmacies() leave out special allocation schemes,
PCN hubs, federation offices, appliance contractors, online and delivery
pharmacies and airside counters on every data refresh, and label the
practices for people who are homeless (data/curated/service_sites.csv).
run_dentists() leaves out prison dental services and labels referral and
urgent services. This
does the same to the shipped files now, without a download: the London
parquets and map files, and those of any region build present under
data/regions/<id>/. Safe to run twice.

The shipped files carry no role code and no opening date, so two rules wait
for the next refresh: RO94 records are caught here by name only, and of two
pharmacies at one address the first listed is kept rather than the newer.

    python3 scripts/apply_service_sites.py
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import pandas as pd  # noqa: E402
import fetch_all_data as F  # noqa: E402

SITES = F.load_service_sites()


def gp_ok(code, name):
    return F.gp_practice_verdict(code, name, SITES)


def ph_ok(name, addr):
    return F.pharmacy_verdict("", name, addr, "", SITES) == ""


def addr_key(a, pc):
    first = str(a or "").split(",")[0]
    return re.sub(r"[^A-Z0-9]", "", first.upper()) + "|" + F.normalise_postcode(pc)


def read_js(path: Path, var: str):
    s = path.read_text(encoding="utf-8")
    i = s.index("var " + var)
    head = s[:i]
    body = s[i:]
    return head, json.loads(body[body.index("["):body.rindex("]") + 1])


def write_js(path: Path, head: str, var: str, rows: list) -> None:
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    path.write_text(head + f"var {var} = {body};\n", encoding="utf-8")


def dedupe(rows, a, pc):
    seen, out = set(), []
    for r in rows:
        k = addr_key(r.get(a), r.get(pc))
        if k.split("|")[0] and k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def main() -> None:
    dropped_gp, dropped_ph = set(), set()
    roots = [REPO] + sorted(p for p in (REPO / "data" / "regions").glob("*") if p.is_dir())
    for root in roots:
        base = root if root == REPO else root
        # GP practices
        js = base / "data" / "map" / "gp_practices.js" if root == REPO else base / "map" / "gp_practices.js"
        if js.exists():
            head, recs = read_js(js, "GPS")
            out = []
            for d in recs:
                v, label = gp_ok(d.get("code", ""), d.get("n", ""))
                if v == "drop":
                    dropped_gp.add((d.get("n", ""), F.normalise_postcode(d.get("pc", ""))))
                    continue
                if label:
                    d["kind"] = label
                out.append(d)
            write_js(js, head, "GPS", out)
            print(f"{js.relative_to(REPO)}: {len(recs)} -> {len(out)}")
        # Pharmacies
        pj = base / "pharmacies.json"
        if pj.exists():
            recs = json.loads(pj.read_text(encoding="utf-8"))
            out = [d for d in recs if ph_ok(d.get("n", ""), d.get("a", ""))]
            out = dedupe(out, "a", "pc")
            kept = {id(d) for d in out}
            for d in recs:
                if id(d) not in kept:
                    dropped_ph.add((d.get("n", ""), F.normalise_postcode(d.get("pc", "")), d.get("a", "")))
            pj.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
            print(f"{pj.relative_to(REPO)}: {len(recs)} -> {len(out)}")
        # Dental practices: prisons out, referral services labelled. The
        # shipped file has no address, so these go on the name alone.
        dj = base / "dental_practices.json"
        if dj.exists():
            recs = json.loads(dj.read_text(encoding="utf-8"))
            out = []
            for d in recs:
                k = F.dental_verdict(d.get("name", ""))
                if k is None:
                    continue
                if k:
                    d["kind"] = k
                else:
                    d.pop("kind", None)
                out.append(d)
            dj.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
            print(f"{dj.relative_to(REPO)}: {len(recs)} -> {len(out)}")

    # The London parquets, which the next pipeline stages read.
    gpq = REPO / "data" / "healthcare" / "gp_practices.parquet"
    if gpq.exists():
        df = pd.read_parquet(gpq)
        v = [gp_ok(c, n) for c, n in zip(df["code"], df["name"])]
        df = df.assign(kind=[l if a == "keep" else "" for a, l in v])[[a == "keep" for a, _ in v]]
        df.to_parquet(gpq, index=False)
        print(f"{gpq.relative_to(REPO)}: -> {len(df)}")
    phq = REPO / "data" / "healthcare" / "pharmacies.parquet"
    if phq.exists():
        df = pd.read_parquet(phq)
        keep = [ph_ok(n, a) for n, a in zip(df["name"], df["addr"])]
        df = df[keep]
        k = [addr_key(a, p) for a, p in zip(df["addr"], df["postcode"])]
        df = df.assign(_k=k)
        df = df[~(df["_k"].duplicated() & ~df["_k"].str.startswith("|"))].drop(columns="_k")
        df.to_parquet(phq, index=False)
        print(f"{phq.relative_to(REPO)}: -> {len(df)}")

    dq = REPO / "data" / "healthcare" / "dental_practices.parquet"
    if dq.exists():
        df = pd.read_parquet(dq)
        k = [F.dental_verdict(n) for n in df["name"]]
        df = df.assign(kind=[x or "" for x in k])[[x is not None for x in k]]
        df.to_parquet(dq, index=False)
        print(f"{dq.relative_to(REPO)}: -> {len(df)}")

    # The ward lists the ward report reads.
    names_gp = {(n, pc) for n, pc in dropped_gp}
    names_ph = {(n, pc, a) for n, pc, a in dropped_ph}
    wfiles = [REPO / "ward_data.json", REPO / "data" / "ward_core.json"]
    wfiles += sorted((REPO / "data" / "regions").glob("*/ward_data.json"))
    wfiles += sorted((REPO / "data" / "regions").glob("*/ward_core.json"))
    for wf in wfiles:
        if not wf.exists():
            continue
        txt = wf.read_text(encoding="utf-8")
        d = json.loads(txt)
        n = 0
        for w in (d.get("wards") or {}).values():
            if "gp_list" in w:
                before = len(w["gp_list"])
                w["gp_list"] = [g for g in w["gp_list"]
                                if (g.get("name", ""), F.normalise_postcode(g.get("postcode", ""))) not in names_gp]
                n += before - len(w["gp_list"])
            if "pharmacy_list" in w:
                before = len(w["pharmacy_list"])
                w["pharmacy_list"] = [p for p in w["pharmacy_list"]
                                      if (p.get("name", ""), F.normalise_postcode(p.get("postcode", "")), p.get("addr", "")) not in names_ph]
                n += before - len(w["pharmacy_list"])
        # Written as the pipeline writes it, so only the removed entries change.
        wf.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{wf.relative_to(REPO)}: {n} list entries removed")


if __name__ == "__main__":
    main()
