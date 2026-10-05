#!/usr/bin/env python3
"""Gera data.json do dashboard ANJOS (somente Meta Ads + leads do Meta no Pipedrive).

Uso:
    python3 build_data.py --pipedrive [--end YYYY-MM-DD] [--out data.json]
    python3 build_data.py --deals deals_1.json [deals_2.json ...]   # modo manual, com arquivos do getDeals

- Meta: chamadas a graph.facebook.com (a credencial do ambiente injeta o token; nada de token aqui).
- Pipedrive: API v2 (credencial do ambiente) ou arquivos JSON salvos pela ferramenta getDeals.
- A página calcula qualquer período a partir dos dados diários por anúncio (`daily_ads`) e dos negócios
  (`deals`). Alcance e frequência não são somáveis entre dias, por isso vêm prontos só para os períodos
  fixos (1, 7, 14 e 30 dias) em `reach`.
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


# ---------------------------------------------------------------- Meta
def curl(url, params=None):
    cmd = ["curl", "-sS", "-m", "90", "-G", url]
    for k, v in (params or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    for attempt in range(12):
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
        err = data.get("error") if isinstance(data, dict) else None
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


def daily_ads(since, until):
    """Uma linha por anúncio e dia: [data, ad, conjunto, campanha, gasto, impressões, cliques no link, leads, vídeo 25%]."""
    rows = []
    for r in paged(f"{ACCOUNT}/insights", {
        "level": "ad", "time_increment": 1,
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        "fields": "ad_id,adset_id,campaign_id,spend,impressions,inline_link_clicks,actions,video_p25_watched_actions",
        "limit": 100,
    }):
        rows.append([
            r["date_start"], r["ad_id"], r["adset_id"], r["campaign_id"],
            round(num(r.get("spend")), 2), int(num(r.get("impressions"))), int(num(r.get("inline_link_clicks"))),
            int(action(r, "actions", "lead")),
            int(sum(num(a.get("value")) for a in (r.get("video_p25_watched_actions") or []))),
        ])
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows


def reach_rows(level, since, until):
    out = {}
    for r in paged(f"{ACCOUNT}/insights", {
        "level": level, "time_range": json.dumps({"since": since, "until": until}),
        "fields": f"{level}_id,reach", "limit": 200,
    }):
        out[r[f"{level}_id"]] = int(num(r.get("reach")))
    return out


def fetch_entities(ids_by_kind, since_ts):
    flt = json.dumps([{"field": "updated_time", "operator": "GREATER_THAN", "value": since_ts}])
    spec = {
        "campaigns": ("campaigns", "id,name,objective,status,effective_status,daily_budget,lifetime_budget"),
        "adsets": ("adsets", "id,name,campaign_id,optimization_goal,destination_type,status,effective_status,daily_budget,lifetime_budget"),
        "ads": ("ads", "id,name,adset_id,campaign_id,status,effective_status,creative{thumbnail_url,object_type}"),
    }
    out = {}
    for kind, (edge, fields) in spec.items():
        found = {x["id"]: x for x in paged(f"{ACCOUNT}/{edge}", {"fields": fields, "filtering": flt, "limit": 50})}
        missing = [i for i in ids_by_kind[kind] if i not in found]
        for i in range(0, len(missing), 40):
            by_id = json.dumps([{"field": "id", "operator": "IN", "value": missing[i:i + 40]}])
            for x in paged(f"{ACCOUNT}/{edge}", {"fields": fields, "filtering": by_id, "limit": 50}):
                found[x["id"]] = x
        out[kind] = list(found.values())
    return out


def shape_entities(raw):
    def budget(x):
        d, l = x.get("daily_budget"), x.get("lifetime_budget")
        return (num(d) / 100 if d else None), ("Diário" if d else ("Vitalício" if l else None))
    ent = {"campaigns": [], "adsets": [], "ads": []}
    goals = {}
    for s in raw["adsets"]:
        goals.setdefault(s["campaign_id"], []).append((s.get("optimization_goal"), s.get("destination_type")))

    def kind(c):
        # form = formulário instantâneo; site = conversão no site/landing page; other = demais objetivos
        if c.get("objective") not in ("OUTCOME_LEADS", "LEAD_GENERATION"):
            return "other"
        gs = goals.get(c["id"], [])
        if any(g in ("LEAD_GENERATION", "QUALITY_LEAD") or d == "ON_AD" for g, d in gs):
            return "form"
        return "site"

    for c in raw["campaigns"]:
        b, t = budget(c)
        ent["campaigns"].append({"id": c["id"], "name": c["name"], "status": c.get("effective_status"),
                                 "on": c.get("status") == "ACTIVE", "budget": b, "budget_type": t,
                                 "objective": c.get("objective"), "kind": kind(c)})
    for s in raw["adsets"]:
        b, t = budget(s)
        ent["adsets"].append({"id": s["id"], "name": s["name"], "campaign_id": s["campaign_id"],
                              "status": s.get("effective_status"), "on": s.get("status") == "ACTIVE",
                              "budget": b, "budget_type": t})
    for x in raw["ads"]:
        cr = x.get("creative") or {}
        ent["ads"].append({"id": x["id"], "name": x["name"], "adset_id": x["adset_id"], "campaign_id": x["campaign_id"],
                           "status": x.get("effective_status"), "on": x.get("status") == "ACTIVE",
                           "format": cr.get("object_type"), "thumb_url": cr.get("thumbnail_url")})
    return ent


# ---------------------------------------------------------------- Pipedrive
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def to_local_date(ts):
    # add_time vem em UTC; o fuso da operação é America/Sao_Paulo (UTC-3)
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(hours=3)
    return dt.date()


def fetch_pipedrive(since):
    """Lê os negócios do funil NACIONAL pela API do Pipedrive (a credencial do ambiente injeta o token)."""
    keys = ",".join([F_SOURCE, F_MEDIUM, F_CAMPAIGN, F_CONTENT, F_TERM])
    deals, cursor = [], None
    while True:
        cmd = ["curl", "-sS", "-m", "90", "-G", "https://api.pipedrive.com/api/v2/deals",
               "--data-urlencode", "pipeline_id=1", "--data-urlencode", "sort_by=add_time",
               "--data-urlencode", "sort_direction=desc", "--data-urlencode", "limit=500",
               "--data-urlencode", f"custom_fields={keys}"]
        if cursor:
            cmd += ["--data-urlencode", f"cursor={cursor}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("Pipedrive: não foi possível acessar api.pipedrive.com. Adicione a credencial do Pipedrive "
                     "(site permitido api.pipedrive.com, cabeçalho x-api-token) nas configurações do ambiente.")
        try:
            d = json.loads(r.stdout)
        except ValueError:
            sys.exit(f"Pipedrive: resposta inesperada: {r.stdout[:200]}")
        if not d.get("success"):
            sys.exit(f"Pipedrive: {d.get('error') or d}. Confira se a credencial do Pipedrive está no ambiente.")
        batch = d.get("data") or []
        deals += batch
        cursor = (d.get("additional_data") or {}).get("next_cursor")
        if not batch or not cursor or to_local_date(batch[-1]["add_time"]) < since:
            return deals


def load_deals(a, since):
    if a.pipedrive:
        return fetch_pipedrive(since)
    deals = []
    for f in a.deals or []:
        raw = json.load(open(f))
        deals += raw.get("data", raw) if isinstance(raw, dict) else raw
    if not deals:
        sys.exit("informe --pipedrive ou --deals arquivo.json")
    return deals


def is_meta(deal):
    src = norm(str((deal.get("custom_fields") or {}).get(F_SOURCE) or ""))
    return src.startswith(("ig", "fb", "facebook", "instagram", "meta"))


def make_lookup(ent):
    lk = {"adset_id": {x["id"]: x["id"] for x in ent["adsets"]}, "adset_name": {}, "ad_by_name": {},
          "ad_adset": {x["id"]: x["adset_id"] for x in ent["ads"]}, "ad_ids": {x["id"] for x in ent["ads"]},
          "ad_campaign": {x["id"]: x["campaign_id"] for x in ent["ads"]},
          "adset_campaign": {x["id"]: x["campaign_id"] for x in ent["adsets"]},
          "campaign_id": {x["id"]: x["id"] for x in ent["campaigns"]}, "campaign_name": {}}
    for x in ent["campaigns"]:
        k = norm(x["name"])
        lk["campaign_name"][k] = None if k in lk["campaign_name"] else x["id"]  # nome repetido não identifica a campanha
    for x in ent["adsets"]:
        lk["adset_name"].setdefault(norm(x["name"]), x["id"])
    for x in ent["ads"]:
        lk["ad_by_name"].setdefault(norm(x["name"]), []).append(x["id"])
    return lk


def compact_deals(deals, lk):
    """Negócios do Meta, um por linha: [data, etapa, status o/w/l, motivo de perda, anúncio, conjunto].
    Atribuição: utm_content = conjunto (nome ou ID); utm_term = anúncio (nome ou ID)."""
    out = []
    for d in deals:
        if d.get("pipeline_id") not in (None, 1) or not is_meta(d):
            continue
        day = to_local_date(d["add_time"])
        if day < DATA_START:
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
        ucamp = str(cf.get(F_CAMPAIGN) or "").strip()
        camp = lk["campaign_id"].get(ucamp) or lk["campaign_name"].get(norm(ucamp)) \
            or lk["adset_campaign"].get(adset) or lk["ad_campaign"].get(ad) or ""
        st = {"won": "w", "lost": "l"}.get(d.get("status"), "o")
        out.append([day.isoformat(), pos, st, d.get("lost_reason") or "", ad or "", adset or "",
                    d.get("value") or 0 if st == "w" else 0, camp])
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------- principal
def windows(end):
    out = {}
    for p in PRESETS:
        cs, ce = end - timedelta(days=p - 1), end
        pe = cs - timedelta(days=1)
        ps = pe - timedelta(days=p - 1)
        out[p] = {"cur": (cs, ce), "prev": (ps, pe)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deals", nargs="+", help="arquivos JSON de getDeals (alternativa ao --pipedrive)")
    ap.add_argument("--pipedrive", action="store_true", help="busca os negócios direto na API do Pipedrive")
    ap.add_argument("--end", help="último dia dos dados (padrão: ontem, horário de Brasília)")
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--reuse", help="reaproveita daily_ads e reach de um data.json e refaz só entidades e CRM")
    a = ap.parse_args()

    today = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    end = date.fromisoformat(a.end) if a.end else today - timedelta(days=1)  # o último dia é sempre D-1
    win = windows(end)

    # 1) dados diários por anúncio, de julho até D-1 (base de qualquer período escolhido na página)
    old = json.load(open(a.reuse)) if a.reuse else None
    rows = old["daily_ads"] if old else daily_ads(DATA_START, end)
    ids = {"ads": {r[1] for r in rows}, "adsets": {r[2] for r in rows}, "campaigns": {r[3] for r in rows}}

    # 2) entidades (nome, status, orçamento) das que tiveram gasto ou foram alteradas desde julho
    since_ts = int(datetime(DATA_START.year, DATA_START.month, DATA_START.day, tzinfo=timezone.utc).timestamp())
    ent = shape_entities(fetch_entities({k: sorted(v) for k, v in ids.items()}, since_ts))

    # 3) alcance exato para os períodos fixos (alcance não se soma entre dias)
    reach = old["reach"] if old else {lv: {} for lv in ("campaign", "adset", "ad", "account")}
    for p, w in ([] if old else win.items()):
        for which in ("cur", "prev"):
            if w[which][0] < DATA_START:
                continue
            s, e = (d.isoformat() for d in w[which])
            for lv in reach:
                if lv == "account":
                    acc = paged(f"{ACCOUNT}/insights", {"level": "account", "time_range": json.dumps({"since": s, "until": e}),
                                                         "fields": "reach", "limit": 5})
                    reach[lv].setdefault(f"{s}|{e}", {"_": int(num(acc[0].get("reach"))) if acc else 0})
                else:
                    reach[lv][f"{s}|{e}"] = reach_rows(lv, s, e)

    # 4) negócios do CRM (somente leads do Meta)
    deals = compact_deals(load_deals(a, DATA_START), make_lookup(ent))

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start": DATA_START.isoformat(), "end": end.isoformat(),
        "account": ACCOUNT, "account_name": "Anjos Franchising · Nacional",
        "stage_names": {str(k): v for k, v in STAGE_NAMES.items()},
        "presets": PRESETS, "entities": ent, "daily_ads": rows, "reach": reach, "deals": deals,
    }
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"ok: {a.out} · {DATA_START} a {end} · {len(rows)} linhas diárias · {len(ent['ads'])} anúncios · {len(deals)} leads do Meta no CRM",
          file=sys.stderr)


if __name__ == "__main__":
    main()
