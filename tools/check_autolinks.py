"""Prints a line for every automatic link (class "autolink") that points to a page that does not exist."""
import os, re, sys
from urllib.parse import urldefrag
root = sys.argv[1]
n = 0
for dp, _, fs in os.walk(root):
    for f in fs:
        if not f.endswith(".html"):
            continue
        path = os.path.join(dp, f)
        html = open(path, encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r'<a\b[^>]*>', html):
            tag = m.group(0)
            if "autolink" not in tag:
                continue
            h = re.search(r'href="([^"]*)"', tag)
            if not h:
                continue
            n += 1
            target = os.path.normpath(os.path.join(dp, urldefrag(h.group(1))[0]))
            if not os.path.isfile(target):
                print(f"{os.path.relpath(path, root)}: automatic link {h.group(1)} does not lead to a page")
print(f"checked {n} automatic links", file=sys.stderr)
