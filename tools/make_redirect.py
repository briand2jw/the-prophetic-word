#!/usr/bin/env python3
"""Makes a tiny site for the old GitHub address that sends every visitor to the real domain."""
import os, sys
out = sys.argv[1]
NEW = "https://thepurelanguage.org/"
OLD_PATH = "/the-prophetic-word/"
page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>The Pure Language</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<link rel="canonical" href="%(new)s">
<meta http-equiv="refresh" content="0; url=%(new)s">
<script>
(function(){
  var p = location.pathname, base = %(old)r;
  var rest = p.indexOf(base) === 0 ? p.slice(base.length) : "";
  location.replace(%(new)r + rest + location.search + location.hash);
})();
</script>
</head><body style="font-family:Georgia,serif;background:#1b1c18;color:#f0ede3;padding:2rem">
<p>The Pure Language has moved to <a href="%(new)s" style="color:#e58a73">thepurelanguage.org</a>.</p>
</body></html>
""" % {"new": NEW, "old": OLD_PATH}
os.makedirs(out, exist_ok=True)
for name in ("index.html", "404.html"):
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        f.write(page)
print("redirect site written to", out)
