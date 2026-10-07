#!/usr/bin/env python3
"""Injeta data.json no template.html.

  python3 build_html.py                      -> dist/index.html (fragmento para artefato do claude.ai)
  python3 build_html.py --standalone         -> site/index.html (página completa, para hospedar na Vercel)
"""
import argparse, json, os

here = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("data", nargs="?", default="data.json")
ap.add_argument("--standalone", action="store_true", help="gera uma página HTML completa (doctype, head e body)")
ap.add_argument("--out", help="arquivo de saída")
a = ap.parse_args()

data = json.load(open(os.path.join(here, a.data)))
tpl = open(os.path.join(here, "template.html")).read()
assert "/*__DATA__*/null" in tpl
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
page = tpl.replace("/*__DATA__*/null", blob)

if a.standalone:
    cut = page.index('<div class="app">')  # <title>, fontes e estilos vão para o <head>
    page = ("<!doctype html>\n<html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex,nofollow\"><meta name=\"color-scheme\" content=\"light dark\">"
            "<style>[hidden]{display:none!important}</style>\n" + page[:cut] + "</head><body>\n" + page[cut:] + "\n</body></html>\n")
    out = a.out or os.path.join(here, "site", "index.html")
else:
    out = a.out or os.path.join(here, "dist", "index.html")

os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w").write(page)
print(f"ok: {out} ({os.path.getsize(out) // 1024} KB)")
