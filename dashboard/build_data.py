#!/usr/bin/env python3
"""Gera data.json do dashboard ANJOS (somente Meta Ads + leads do Meta no Pipedrive).

Uso:
    python3 build_data.py --deals deals_1.json [deals_2.json ...] [--end YYYY-MM-DD] [--out data.json]

- Meta: chamadas a graph.facebook.com (a credencial do ambiente injeta o token; nada de token aqui).
- Pipedrive: arquivos JSON salvos a partir da ferramenta getDeals (pipeline NACIONAL).
"""
import argparse, json, re, subprocess, sys, time, unicodedata
from datetime import date, datetime, timedelta, timezone

ACCOUNT = "act_821566846073840"
API = "https://graph.facebook.com/v21.0"
PRESETS = [1, 7, 14, 30]
DATA_START = date(2026, 7, 1)  # o dashboard só considera dados de julho/2026 em diante

# campos personalizados de negócio no Pipedrive
F_SOURCE = "d0c29b1b081a2613f86dda37adff5be306753787"
F_MEDIUM = "6f444efc2ee75fbbe4efb9b6ba0eff348a1bc4df"
F_CAMPAIGN = "08b0d86f0657a3bc2f04de8091939ac23d12d55e"
F_CONTENT = "91bfef91619b3d5f91ded82c0aa17c4831300b33"
F_TERM = "23009c4080a025a6ebcb2dbabe08871f871c1a1b"

# ordem das etapas do funil NACIONAL (id Pipedrive -> posição)
STAGE_POS = {1: 1, 2: 2, 124: 3, 130: 3, 67: 4, 68: 5, 3: 6, 110: 7, 109: 8}
STAGE_NAMES = {
    1: "Leads novos", 2: "Tentativa de contato", 3: "Conectados", 4: "MQL em atendimento",
    5: "SQL · 1ª reunião agendada", 6: "1ª reunião realizada", 7: "COF", 8: "Fechamento",
}


def curl(url, params=None):
    cmd = ["curl", "-sS", "-m", "60", "-G", url]
    for k, v in (params or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    for attempt in range(12):
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
        err = data.get("error")
        if not err:
            return data
        # limite de chamadas do Meta (códigos 4, 17, 32, 613) ou "reduza os dados" (1): espera e tenta de novo
        if err.get("code") in (1, 4, 17, 32, 613) and attempt < 11:
            time.sleep(60)
            continue
        raise RuntimeError(f"Meta API: {err.get('message')}")


def paged(path, params):
    rows, url, p = [], f"{API}/{path}", dict(params)
    while True:
        d = curl(url, p)
        rows += d.get("data", [])
        nxt = d.get("paging", {}).get("next")
        if not nxt:
            return rows
        url, p = nxt, {}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def action(row, key, name):
    for a in row.get(key) or []:
        if a.get("action_type") == name:
            return num(a.get("value"))
    return 0.0


def metrics(r):
    impr, reach, spend = num(r.get("impressions")), num(r.get("reach")), num(r.get("spend"))
    clicks = num(r.get("inline_link_clicks"))
    leads = action(r, "actions", "lead")
    p25 = sum(num(a.get("value")) for a in (r.get("video_p25_watched_actions") or []))
    return {
        "spend": round(spend, 2), "impressions": int(impr), "reach": int(reach),
        "link_clicks": int(clicks), "leads": int(leads), "video_p25": int(p25),
    }


INSIGHT_FIELDS = ("spend,impressions,reach,inline_link_clicks,actions,video_p25_watched_actions,"
                  "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name")


def insights(level, since, until):
    return paged(f"{ACCOUNT}/insights", {
        "level": level, "time_range": json.dumps({"since": since, "until": until}),
        "fields": INSIGHT_FIELDS, "limit": 50,
    })


def windows(end):
    out = {}
    for p in PRESETS:
        cs, ce = end - timedelta(days=p - 1), end
        pe = cs - timedelta(days=1)
        ps = pe - timedelta(days=p - 1)
        out[p] = {"cur": (cs, ce), "prev": (ps, pe)}
    return out


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def to_local_date(ts):
    # add_time vem em UTC; o fuso da operação é America/Sao_Paulo (UTC-3)
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(hours=3)
    return dt.date()


def is_meta(deal):
    cf = deal.get("custom_fields") or {}
    src = norm(str(cf.get(F_SOURCE) or ""))
    return src.startswith(("ig", "fb", "facebook", "instagram", "meta"))


def build_crm(deals, win, lk):
    """lk: índices de adsets/anúncios para atribuir cada lead (utm_content = conjunto, utm_term = anúncio)."""
    meta_deals = []
    for d in deals:
        if d.get("pipeline_id") not in (None, 1) or not is_meta(d):
            continue
        cf = d.get("custom_fields") or {}
        pos = STAGE_POS.get(d.get("stage_id"), 1)
        if d.get("status") == "won":
            pos = 8
        content = str(cf.get(F_CONTENT) or "").strip()
        term = str(cf.get(F_TERM) or "").strip()
        adset = lk["adset_id"].get(content) or lk["adset_name"].get(norm(content))
        ad = None
        if term:
            cands = lk["ad_by_name"].get(norm(term), [])
            if adset:
                cands = [x for x in cands if lk["ad_adset"].get(x) == adset]
            if len(cands) == 1:
                ad = cands[0]
        if not ad and term in lk["ad_ids"]:
            ad = term
        if not ad and content in lk["ad_ids"]:
            ad = content
        if not ad and content and not adset:
            cands = lk["ad_by_name"].get(norm(content), [])
            if len(cands) == 1:
                ad = cands[0]
        if ad and not adset:
            adset = lk["ad_adset"].get(ad)
        meta_deals.append({
            "day": to_local_date(d["add_time"]), "pos": pos, "status": d.get("status"),
            "reason": d.get("lost_reason"), "value": d.get("value") or 0, "ad": ad, "adset": adset,
        })

    def cohort(start, end):
        rows = [x for x in meta_deals if start <= x["day"] <= end]
        reach = {k: sum(1 for x in rows if x["pos"] >= k) for k in range(1, 9)}
        lost = [x for x in rows if x["status"] == "lost"]
        reasons = {}
        for x in lost:
            reasons[x["reason"] or "SEM MOTIVO"] = reasons.get(x["reason"] or "SEM MOTIVO", 0) + 1
        lost_stage = {}
        for x in lost:
            lost_stage[x["pos"]] = lost_stage.get(x["pos"], 0) + 1
        won = [x for x in rows if x["status"] == "won"]
        ads, adsets = {}, {}
        for store, key in ((ads, "ad"), (adsets, "adset")):
            for x in rows:
                k = x[key] or "sem_rastreio"
                a = store.setdefault(k, {"total": 0, "r3": 0, "r4": 0, "r5": 0, "r6": 0, "r8": 0})
                a["total"] += 1
                for n in (3, 4, 5, 6, 8):
                    if x["pos"] >= n:
                        a[f"r{n}"] += 1
        return {
            "total": len(rows), "reach": reach, "lost": len(lost), "reasons": reasons,
            "lost_stage": lost_stage, "won": len(won), "won_value": sum(x["value"] for x in won),
            "spam": reasons.get("SPAM", 0), "ads": ads, "adsets": adsets,
            "tracked": sum(1 for x in rows if x["adset"] or x["ad"]),
        }

    return {str(p): {"cur": cohort(*w["cur"]), "prev": cohort(*w["prev"])} for p, w in win.items()}


def make_lookup(ent):
    lk = {"adset_id": {x["id"]: x["id"] for x in ent["adsets"]}, "adset_name": {}, "ad_by_name": {},
          "ad_adset": {x["id"]: x["adset_id"] for x in ent["ads"]}, "ad_ids": {x["id"] for x in ent["ads"]}}
    for x in ent["adsets"]:
        lk["adset_name"].setdefault(norm(x["name"]), x["id"])
    for x in ent["ads"]:
        lk["ad_by_name"].setdefault(norm(x["name"]), []).append(x["id"])
    return lk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deals", nargs="+", required=True)
    ap.add_argument("--end")
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--reuse", help="reaproveita as partes do Meta de um data.json existente e só refaz o CRM")
    a = ap.parse_args()

    today = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    end = date.fromisoformat(a.end) if a.end else today - timedelta(days=1)
    win = windows(end)
    if min(w["prev"][0] for w in win.values()) < DATA_START:
        sys.exit("a janela anterior começa antes de 01/07/2026; reduza os presets")
    first = min(w["prev"][0] for w in win.values())

    if a.reuse:
        out = json.load(open(a.reuse))
        deals = []
        for f in a.deals:
            raw = json.load(open(f))
            deals += raw.get("data", raw) if isinstance(raw, dict) else raw
        deals = [d for d in deals if to_local_date(d["add_time"]) >= DATA_START]
        out["crm"] = build_crm(deals, win, make_lookup(out["entities"]))
        out["deals_seen"] = len(deals)
        json.dump(out, open(a.out, "w"), ensure_ascii=False)
        print(f"ok (CRM refeito): {a.out}", file=sys.stderr)
        return

    # --- entidades (status, orçamento) ---
    ent = {"campaigns": [], "adsets": [], "ads": []}
    since_ts = int(datetime(DATA_START.year, DATA_START.month, DATA_START.day, tzinfo=timezone.utc).timestamp())
    flt = json.dumps([{"field": "updated_time", "operator": "GREATER_THAN", "value": since_ts}])
    for c in paged(f"{ACCOUNT}/campaigns", {"fields": "id,name,status,effective_status,daily_budget,lifetime_budget,objective", "filtering": flt, "limit": 50}):
        ent["campaigns"].append({"id": c["id"], "name": c["name"], "status": c.get("effective_status"),
                                 "on": c.get("status") == "ACTIVE",
                                 "budget": (num(c.get("daily_budget")) / 100 if c.get("daily_budget") else None),
                                 "budget_type": "Diário" if c.get("daily_budget") else ("Vitalício" if c.get("lifetime_budget") else None)})
    for s in paged(f"{ACCOUNT}/adsets", {"fields": "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget", "filtering": flt, "limit": 50}):
        ent["adsets"].append({"id": s["id"], "name": s["name"], "campaign_id": s["campaign_id"], "status": s.get("effective_status"),
                              "on": s.get("status") == "ACTIVE",
                              "budget": (num(s.get("daily_budget")) / 100 if s.get("daily_budget") else None),
                              "budget_type": "Diário" if s.get("daily_budget") else ("Vitalício" if s.get("lifetime_budget") else None)})
    for x in paged(f"{ACCOUNT}/ads", {"fields": "id,name,adset_id,campaign_id,status,effective_status,creative{thumbnail_url,object_type}", "filtering": flt, "limit": 50}):
        cr = x.get("creative") or {}
        ent["ads"].append({"id": x["id"], "name": x["name"], "adset_id": x["adset_id"], "campaign_id": x["campaign_id"],
                           "status": x.get("effective_status"), "on": x.get("status") == "ACTIVE",
                           "format": cr.get("object_type"), "thumb_url": cr.get("thumbnail_url")})

    # --- insights por nível/janela ---
    rows = {lv: {} for lv in ("campaign", "adset", "ad")}
    account = {}
    for p, w in win.items():
        for which in ("cur", "prev"):
            s, e = (d.isoformat() for d in w[which])
            for lv in rows:
                res = rows[lv].setdefault(str(p), {}).setdefault(which, {})
                for r in insights(lv, s, e):
                    res[r[f"{lv}_id"]] = metrics(r)
            acc = paged(f"{ACCOUNT}/insights", {"level": "account", "time_range": json.dumps({"since": s, "until": e}),
                                                 "fields": INSIGHT_FIELDS, "limit": 5})
            account.setdefault(str(p), {})[which] = metrics(acc[0]) if acc else metrics({})

    daily = []
    for r in paged(f"{ACCOUNT}/insights", {"level": "account", "time_increment": 1,
                                           "time_range": json.dumps({"since": max(first, DATA_START).isoformat() if False else DATA_START.isoformat(), "until": end.isoformat()}),
                                           "fields": INSIGHT_FIELDS, "limit": 50}):
        m = metrics(r)
        m["date"] = r["date_start"]
        daily.append(m)
    daily.sort(key=lambda x: x["date"])

    # --- CRM ---
    deals = []
    for f in a.deals:
        raw = json.load(open(f))
        deals += raw.get("data", raw) if isinstance(raw, dict) else raw
    deals = [d for d in deals if to_local_date(d["add_time"]) >= DATA_START]
    crm = build_crm(deals, win, make_lookup(ent))

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.isoformat(), "account": ACCOUNT, "account_name": "Anjos Franchising · Nacional",
        "stage_names": {str(k): v for k, v in STAGE_NAMES.items()},
        "windows": {str(p): {k: [d.isoformat() for d in v] for k, v in w.items()} for p, w in win.items()},
        "entities": ent, "rows": rows, "account_totals": account, "daily": daily, "crm": crm,
        "deals_seen": len(deals),
    }
    json.dump(out, open(a.out, "w"), ensure_ascii=False)
    print(f"ok: {a.out} · fim {end} · {len(ent['ads'])} anúncios · {len(deals)} negócios lidos", file=sys.stderr)


if __name__ == "__main__":
    main()
