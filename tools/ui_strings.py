#!/usr/bin/env python3
"""
Finds the words that readers see in nav-helpers.html (buttons, windows, messages) so they can be
translated, and puts translations back into a copy of the file for each language.

The English file is never changed. Each piece of visible text is a quoted string in the code; we
decide which strings are visible text, translate them (see tools/translate.py), and swap them in.
"""
import re

SKIP_WORDS = {"Escape", "Enter", "Tab", "HEAD", "GET", "POST", "English", "Space"}
BRAND = "The Pure Language"
ATTRS = r"(?:aria-label|placeholder|title|alt)"


def literals(src):
    """All quoted strings in the code: (start, end, quote, raw_text)."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                if j < n and src[j] == "\n":
                    break
                j += 1
            out.append((i, j + 1, c, src[i + 1:j]))
            i = j + 1
        elif src.startswith("//", i) and (i == 0 or src[i - 1] != ":"):
            k = src.find("\n", i)
            i = n if k < 0 else k
        elif src.startswith("/*", i):
            k = src.find("*/", i)
            i = n if k < 0 else k + 2
        else:
            i += 1
    return out


def decode(raw):
    def u(m):
        return chr(int(m.group(1), 16))
    t = re.sub(r"\\u([0-9a-fA-F]{4})", u, raw)
    t = re.sub(r"\\x([0-9a-fA-F]{2})", u, t)
    t = t.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
    t = re.sub(r"\\(.)", r"\1", t)
    return t


def encode(text, quote):
    t = text.replace("\\", "\\\\").replace(quote, "\\" + quote)
    return t.replace("\r", "\\r").replace("\n", "\\n")


def tag_text(t):
    """The words between tags (also from a string that starts or ends in the middle of a tag)."""
    g, l = t.find(">"), t.find("<")
    if g >= 0 and (l < 0 or g < l):
        t = t[g + 1:]                      # starts in the middle of a tag
    t = re.sub(r"<[^>]*>", " ", t)
    k = t.find("<")
    if k >= 0:
        t = t[:k]                          # ends in the middle of a tag
    return t


def visible(t):
    """Text a reader would see, including words inside aria-label, placeholder, title and alt."""
    attrs = " ".join(re.findall(ATTRS + r'="([^"]*)"', t))
    return tag_text(t) + " " + attrs


def is_ui(t):
    s = t.strip()
    if not s or s in SKIP_WORDS or s == BRAND:
        return False
    if "<" in t or re.match(r"^[\"']?>", t):
        v = visible(t)
        if re.sub(r"\s+", " ", tag_text(t)).strip() == BRAND and not re.search(ATTRS + '="', t):
            return False
        return bool(re.search(r"[A-Za-z\u00C0-\u024F]{2,}", v.replace(BRAND, "")))
    if re.search(r"[\[\]=>#]|site_libs|\.(html|json|js|png|epub|webmanifest|css)\b|^[.:(]", s):
        return False
    if re.match(r"^[A-Z][a-z]{2,}$", s):
        return True
    if not re.search(r"[A-Za-z]{3,}", s):
        return False
    first = s[0]
    startsok = first.isupper() or first in "{\u2191\u2190\u2192\u201c\"'"
    return startsok and (" " in s or bool(re.search(r"[,?!:\u2026]|\w\.", s)))


def collect(src):
    """Unique English visible strings, in order of first appearance."""
    seen, out = set(), []
    for a, b, q, raw in literals(src):
        t = decode(raw)
        if is_ui(t) and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def skeleton(t):
    """What must not change when a string is translated: the tags and the placeholders."""
    tags = re.findall(r"<[^>]*>", re.sub(r'(%s)="[^"]*"' % ATTRS, r'\1=""', t))
    return tags, sorted(re.findall(r"\{\d\}", t))


def apply(src, catalog):
    """A copy of the code with translated strings swapped in."""
    parts = []
    for a, b, q, raw in reversed(literals(src)):
        t = decode(raw)
        tr = catalog.get(t)
        if tr and tr != t:
            parts.append((a, b, q + encode(tr, q) + q))
    for a, b, new in parts:       # already in reverse order, so earlier positions stay valid
        src = src[:a] + new + src[b:]
    return src


if __name__ == "__main__":
    import sys
    s = open(sys.argv[1] if len(sys.argv) > 1 else "nav-helpers.html", encoding="utf-8").read()
    items = collect(s)
    print(len(items), "strings,", sum(len(x) for x in items), "characters")
    for x in items:
        print(repr(x[:100]))
