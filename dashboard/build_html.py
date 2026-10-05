#!/usr/bin/env python3
"""Injeta data.json no template.html e grava dist/index.html (página pronta para publicar)."""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(here, sys.argv[1] if len(sys.argv) > 1 else "data.json")))
tpl = open(os.path.join(here, "template.html")).read()
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
assert "/*__DATA__*/null" in tpl
os.makedirs(os.path.join(here, "dist"), exist_ok=True)
out = os.path.join(here, "dist", "index.html")
open(out, "w").write(tpl.replace("/*__DATA__*/null", blob))
print(f"ok: {out} ({os.path.getsize(out) // 1024} KB)")
