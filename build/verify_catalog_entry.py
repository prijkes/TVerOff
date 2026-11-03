#!/usr/bin/env python3
"""Validate a webosbrew apps-repo package entry against the fields repogen's PackageRegistry allows.

  python build/verify_catalog_entry.py dist/com.tveroff.patcher.yml
"""
import sys
import urllib.request

import yaml

ALLOWED = {"title", "iconUri", "manifestUrl", "manifestUrlBeta", "category", "description", "shortDescription",
           "pool", "requirements", "detailIconUri", "funding", "screenshots"}
CATEGORIES = {"multimedia", "game", "system", "utility", "screensaver", "launcher", "amblight", "internet"}

p = sys.argv[1]
d = yaml.safe_load(open(p, encoding="utf-8"))
fail = []

extra = set(d) - ALLOWED
if extra:
    fail.append("fields outside the allowed list: %s" % sorted(extra))
for req in ("title", "iconUri", "manifestUrl", "category", "pool", "description"):
    if req not in d:
        fail.append("missing required field: %s" % req)
if len(d.get("title", "")) > 30:
    fail.append("title longer than 30 chars (%d)" % len(d["title"]))
if "shortDescription" in d and len(d["shortDescription"]) > 80:
    fail.append("shortDescription longer than 80 chars (%d)" % len(d["shortDescription"]))
if d.get("category") not in CATEGORIES:
    fail.append("category %r is not one of %s" % (d.get("category"), sorted(CATEGORIES)))
if d.get("pool") not in ("main", "non-free"):
    fail.append("pool must be main or non-free")
if set(d.get("requirements", {})) - {"webosRelease", "deviceSoC"}:
    fail.append("unknown requirements keys: %s" % sorted(set(d["requirements"]) - {"webosRelease", "deviceSoC"}))
for i, s in enumerate(d.get("screenshots", [])):
    if isinstance(s, dict):
        if "url" not in s or set(s) - {"url", "caption"}:
            fail.append("screenshots[%d] must have url and at most caption" % i)
    elif not isinstance(s, str):
        fail.append("screenshots[%d] must be a URL or {url, caption}" % i)

# URLs referenced must exist
urls = [d.get("iconUri"), d.get("manifestUrl")] + [s["url"] if isinstance(s, dict) else s for s in d.get("screenshots", [])]
for u in [u for u in urls if u]:
    try:
        req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "tveroff-submission-check"})
        with urllib.request.urlopen(req, timeout=30) as r:
            print("  ok    %s -> %s" % (r.status, u[:110]))
            if r.status != 200:
                fail.append("HEAD %s -> %s" % (u, r.status))
    except Exception as e:
        fail.append("HEAD %s failed: %s" % (u, e))

print("entry: %s" % p)
for f in fail:
    print("  FAIL  %s" % f)
print("RESULT: %s" % ("OK" if not fail else "FAILED"))
sys.exit(1 if fail else 0)
