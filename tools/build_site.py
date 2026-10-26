#!/usr/bin/env python3
"""
Builds the content of The Truth from the writers' Word files.

What it does, every time the site is published:
  1. Reads every Word (or LibreOffice) file in  submissions/  (the writer's details table at the
     top says where the page goes) and turns it into a page.
  2. Also reads any hand-made pages in  articles/  and  chapters/  that carry the
     same details in their header.
  3. Rebuilds every list from those pages: the master page list in _quarto.yml,
     the article lists, the Bible book grid, each book's chapter grid, and the
     What's new list on Home (written from the Posted and Updated dates).

Nothing here needs to be run by hand. The publishing workflow runs it.
A file with a problem is skipped and listed in build-problems.txt, and the
workflow reports it after the rest of the site has been published.
"""
import datetime
import json
import os
import re
import shlex
import shutil
import tempfile
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

NEWS_LIMIT = 5
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PANDOC = shlex.split(os.environ.get("PANDOC", "pandoc"))
DOT = "·"

SECTIONS = {
    "know-the-truth": {"name": "Know the Truth", "prefix": "kt", "page": "know-the-truth.qmd", "kind": "article", "order": 0},
    "seek-jehovah": {"name": "Seek Jehovah", "prefix": "sj", "page": "seek-jehovah.qmd", "kind": "article", "order": 2},
    "prophetic-word": {"name": "The Prophetic Word", "prefix": None, "page": "prophetic-word.qmd", "kind": "chapter", "order": 1},
}

with open(os.path.join(ROOT, "tools", "books.json"), encoding="utf-8") as f:
    BOOKS = json.load(f)
BOOK_BY_NAME = {b["name"].lower(): b for b in BOOKS}
BOOK_BY_SLUG = {b["slug"]: b for b in BOOKS}
BOOK_ORDER = {b["slug"]: i for i, b in enumerate(BOOKS)}

problems = []


def problem(where, message):
    problems.append((where, message))
    print("::error file=%s::%s" % (where, message))


# ---------------------------------------------------------------- helpers

def slugify(text):
    s = text.lower()
    s = re.sub(r"[‘’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:70].strip("-") or "page"


def long_date(d):
    return "%s %d, %d" % (d.strftime("%B"), d.day, d.year)


def parse_date(text, fullDate=None):
    if fullDate:
        try:
            return datetime.date.fromisoformat(fullDate[:10])
        except ValueError:
            pass
    text = (text or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d %B %Y", "%B %d %Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


PLACEHOLDER = re.compile(r"^(choose|click|pick|select|type|tap)\b", re.I)


def clean(v):
    v = re.sub(r"\s+", " ", v or "").strip()
    return "" if PLACEHOLDER.match(v) else v


# ------------------------------------------------------- reading Word files

def read_details(path):
    """Reads the details table at the top of a writer's Word file."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    table = body.find(W + "tbl") if body is not None else None
    if table is None:
        return None
    labels = [
        ("where will this be published", "where"), ("bible book", "book"),
        ("chapter number", "chapter"), ("title", "title"),
        ("date first posted", "posted"), ("date last updated", "updated"),
        ("summary", "summary"), ("status", "status"),
    ]
    out = {}
    for row in table.findall(W + "tr"):
        cells = row.findall(W + "tc")
        if len(cells) < 2:
            continue

        def text_of(cell):
            parts = []
            for p in cell.iter(W + "p"):
                parts.append("".join(t.text or "" for t in p.iter(W + "t")))
            return " ".join(x for x in parts if x.strip())

        label = text_of(cells[0]).lower().strip()
        for key, name in labels:
            if label.startswith(key):
                value = text_of(cells[1])
                node = cells[1].find(".//" + W + "date")
                full = node.get(W + "fullDate") if node is not None else None
                out[name] = (value, full)
                break
    return out


def run_pandoc(path, kind):
    cmd = PANDOC + [
        path, "-f", "docx+styles", "-t", "markdown-smart+fenced_divs-raw_html-native_divs-native_spans",
        "--wrap=none", "--lua-filter", os.path.join(ROOT, "tools", "body.lua"),
        "-M", "kind=" + kind,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "pandoc failed")
    return r.stdout.strip() + "\n"


def tidy_body(md):
    md = md.replace("\r\n", "\n")
    md = re.sub(r"\n{3,}", "\n\n", md)
    # headings: drop auto ids
    md = re.sub(r"^(#{1,6} .*?)\s*\{#[^}]*\}\s*$", r"\1", md, flags=re.M)
    return md


# ----------------------------------------------------------------- entries

def odt_to_docx(path, outdir):
    """LibreOffice files (.odt) are converted to Word format first. Returns the new path or None."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    if not exe:
        problem(rel, "This is a LibreOffice file and the converter is not installed. Save it as Word (.docx) instead.")
        return None
    subprocess.run([exe, "--headless", "--convert-to", "docx", "--outdir", outdir, path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    out = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".docx")
    if not os.path.exists(out):
        problem(rel, "This LibreOffice file could not be opened. Try saving it as Word (.docx) instead.")
        return None
    return out


def entry_from_docx(path, shown=None):
    rel = shown or os.path.relpath(path, ROOT).replace(os.sep, "/")
    try:
        d = read_details(path)
    except Exception as e:  # not a real Word file
        problem(rel, "This does not open as a Word file (%s)." % e)
        return None
    if d is None:
        problem(rel, "The details table at the top of the template is missing. Start from the writer template.")
        return None

    def v(k):
        return clean(d.get(k, ("", None))[0])

    where = v("where").lower()
    if "know the truth" in where:
        section = "know-the-truth"
    elif "seek jehovah" in where:
        section = "seek-jehovah"
    elif "prophetic" in where or "bible" in where:
        section = "prophetic-word"
    else:
        problem(rel, 'Pick a place in the "Where will this be published?" box.')
        return None
    status = v("status").lower()
    if "draft" in status:
        print("Draft, not published: %s" % rel)
        return "draft"

    posted = parse_date(v("posted"), d.get("posted", ("", None))[1] if v("posted") else None)
    if not posted:
        problem(rel, 'Pick a date in the "Date first posted" box.')
        return None
    updated = parse_date(v("updated"), d.get("updated", ("", None))[1] if v("updated") else None) or posted
    if updated < posted:
        problem(rel, "The last-updated date is earlier than the first-posted date.")
        return None

    e = {"src": rel, "section": section, "posted": posted, "updated": updated, "summary": v("summary"),
         "generated": True, "abs": path}
    if SECTIONS[section]["kind"] == "chapter":
        book = BOOK_BY_NAME.get(v("book").lower())
        if not book:
            problem(rel, 'Pick a book in the "Bible book" box.')
            return None
        m = re.match(r"^\d+", v("chapter"))
        if not m or int(m.group(0)) < 1:
            problem(rel, 'Type the chapter number in the "Chapter number" box (for example 3).')
            return None
        n = int(m.group(0))
        e.update(kind="chapter", book=book["slug"], book_name=book["name"], chapter=n,
                 title="%s %d" % (book["name"], n), path="chapters/%s-%d.qmd" % (book["slug"], n))
    else:
        title = v("title")
        if not title:
            problem(rel, 'Type the article title in the "Title" box.')
            return None
        e.update(kind="article", title=title,
                 path="articles/%s-%s.qmd" % (SECTIONS[section]["prefix"], slugify(title)))
    try:
        md = tidy_body(run_pandoc(path, e["kind"]))
    except Exception as ex:
        problem(rel, "The text could not be read (%s)." % ex)
        return None
    if not md.strip():
        problem(rel, "There is no text below the details table.")
        return None
    if re.search(r"^\W*SAMPLE\b", md, flags=re.M):
        problem(rel, 'Some "SAMPLE:" lines from the template are still in the text. Replace or delete them.')
        return None
    e["body"] = md
    return e


FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def entry_from_qmd(path):
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = FM.match(text)
    if not m:
        return None
    fm = {}
    for line in m.group(1).split("\n"):
        mm = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip().strip('"')
    if "source" in fm:  # a page this script made earlier
        return "stale"
    if "site-section" not in fm and "book" in fm:
        fm["site-section"] = "prophetic-word"
    if "site-section" not in fm:
        return None
    section = fm["site-section"]
    if section not in SECTIONS:
        problem(rel, "Unknown section %r in the page header." % section)
        return None
    posted = parse_date(fm.get("posted"))
    updated = parse_date(fm.get("updated")) or posted
    if not posted:
        problem(rel, "The page header needs a posted date such as 2026-11-03.")
        return None
    e = {"src": rel, "section": section, "posted": posted, "updated": updated, "title": fm.get("title", ""),
         "summary": fm.get("summary", ""), "generated": False, "path": rel}
    if SECTIONS[section]["kind"] == "chapter":
        book = BOOK_BY_NAME.get(fm.get("book", "").lower())
        if not book or not fm.get("chapter", "").isdigit():
            problem(rel, "A chapter page header needs book: and chapter:.")
            return None
        e.update(kind="chapter", book=book["slug"], book_name=book["name"], chapter=int(fm["chapter"]))
    else:
        e["kind"] = "article"
    return e


# ------------------------------------------------------------- generation

def write(path, text):
    os.makedirs(os.path.dirname(os.path.join(ROOT, path)) or ".", exist_ok=True)
    with open(os.path.join(ROOT, path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def replace_between(path, start, end, new):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        text = f.read()
    a = text.find(start)
    b = text.find(end)
    if a < 0 or b < 0 or b < a:
        raise SystemExit("Markers %r and %r not found in %s" % (start, end, path))
    a_end = text.index("\n", a) + 1
    b_line = text.rfind("\n", 0, b) + 1
    text = text[:a_end] + new + text[b_line:]
    with open(os.path.join(ROOT, path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def md_marker(name, which):
    return "[auto-%s-%s]: #" % (name, which)


def yaml_marker(name, which):
    return "# AUTO:%s-%s" % (name, which.upper())


def article_list(entries):
    lines = [""]
    for e in sorted(entries, key=lambda x: (x["posted"], x["title"].lower()), reverse=True):
        meta = "Posted %s %s Updated %s" % (long_date(e["posted"]), DOT, long_date(e["updated"]))
        lines.append("- [%s](%s)\\" % (e["title"].replace("]", "\\]"), e["path"]))
        if e["summary"]:
            lines.append("  [%s]{.meta}\\" % meta)
            lines.append("  " + e["summary"])
        else:
            lines.append("  [%s]{.meta}" % meta)
    lines.append("")
    return "\n".join(lines) + "\n"


def join_names(parts):
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return datetime.date(y, m, day)


def short_date(d):
    if d.year == datetime.date.today().year:
        return "%s %d" % (d.strftime("%b"), d.day)
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year)


def news_events(entries, since=None):
    """One event per (date, new/updated, book) for chapters, one per article."""
    groups = {}
    for e in entries:
        groups.setdefault((e["posted"], "new", e["kind"], e.get("book", e["path"])), []).append(e)
        if e["updated"] > e["posted"]:
            groups.setdefault((e["updated"], "upd", e["kind"], e.get("book", e["path"])), []).append(e)
    events = []
    for (date, what, kind, _), items in groups.items():
        if since and date < since:
            continue
        if kind == "chapter":
            items = sorted(items, key=lambda x: x["chapter"])
            first = items[0]
            parts = ["[%s %d](%s)" % (first["book_name"], first["chapter"], first["path"])]
            parts += ["[%d](%s)" % (x["chapter"], x["path"]) for x in items[1:]]
            events.append({"date": date, "what": what, "kind": "chapter", "text": join_names(parts),
                           "count": len(items), "book": first["book_name"],
                           "order": (1, BOOK_ORDER[first["book"]])})
        else:
            for x in sorted(items, key=lambda x: x["title"].lower()):
                events.append({"date": date, "what": what, "kind": "article",
                               "text": "[%s](%s)" % (x["title"], x["path"]), "count": 1, "book": "",
                               "order": (SECTIONS[x["section"]]["order"] + 10, x["title"].lower())})
    events.sort(key=lambda r: (-r["date"].toordinal(), r["order"]))
    return events


def news_line(date, what, body):
    return "- [%s]{.wn-date} [%s]{.wn-tag .is-%s} %s" % (
        short_date(date), "New" if what == "new" else "Updated", what,
        body if body.startswith("<details") else "[%s]{.wn-what}" % body)


def news_home(entries):
    """Home page: the newest few lines. A busy day with many pages becomes one expandable line."""
    events = news_events(entries)
    rolled, seen = [], set()
    for ev in events:
        key = (ev["date"], ev["what"], ev["kind"])
        same = [x for x in events if (x["date"], x["what"], x["kind"]) == key]
        total = sum(x["count"] for x in same)
        if total > 3 and len(same) > 0 and (ev["kind"] == "chapter" or len(same) > 3):
            if key in seen:
                continue
            seen.add(key)
            if ev["kind"] == "chapter":
                books = []
                for x in same:
                    if x["book"] not in books:
                        books.append(x["book"])
                label = "%s (%d chapters)" % (join_names(books) if len(books) <= 3 else "%d books" % len(books), total)
                inner = "<br>".join(x["text"] for x in same)
            else:
                label = "%d articles" % total
                inner = "<br>".join(x["text"] for x in same)
            body = '<details class="wn-more"><summary>%s</summary>%s</details>' % (label, inner)
            rolled.append((ev["date"], ev["what"], body))
        else:
            rolled.append((ev["date"], ev["what"], ev["text"]))
    out = [""]
    for date, what, body in rolled[:NEWS_LIMIT]:
        out.append(news_line(date, what, body))
    out.append("")
    return "\n".join(out) + "\n"


def news_all(entries):
    """What's new page: the last three months, newest first, grouped by month."""
    since = add_months(datetime.date.today(), -3)
    events = news_events(entries, since)
    if not events:
        return "\nNo updates in the last three months.\n"
    out, month = [""], None
    for ev in events:
        m = (ev["date"].year, ev["date"].month)
        if m != month:
            month = m
            out += ["", "## %s %d" % (ev["date"].strftime("%B"), ev["date"].year), ""]
        out.append(news_line(ev["date"], ev["what"], ev["text"]))
    out.append("")
    return "\n".join(out) + "\n"


def book_grid(group, chapters_by_book):
    lines = [""]
    for b in BOOKS:
        if b["group"] != group:
            continue
        if b["slug"] in chapters_by_book:
            lines.append("- [%s](%s.qmd)" % (b["name"], b["slug"]))
        elif b["shown"]:
            lines.append("- [%s]{.off}" % b["name"])
    lines.append("")
    return "\n".join(lines) + "\n"


def main():
    # 0. forget pages this script made on an earlier run
    for folder in ("articles", "chapters"):
        full = os.path.join(ROOT, folder)
        if not os.path.isdir(full):
            continue
        for name in os.listdir(full):
            p = os.path.join(full, name)
            if name.endswith(".qmd") and entry_from_qmd(p) == "stale":
                os.remove(p)

    entries, drafts = [], 0
    # 1. Word files
    sub = os.path.join(ROOT, "submissions")
    if os.path.isdir(sub):
        for name in sorted(os.listdir(sub)):
            low = name.lower()
            if not low.endswith((".docx", ".odt")) or name.startswith(("~$", "_", ".", ".~lock")):
                continue
            full = os.path.join(sub, name)
            if low.endswith(".odt"):
                tmp = tempfile.mkdtemp()
                conv = odt_to_docx(full, tmp)
                e = entry_from_docx(conv, "submissions/" + name) if conv else None
            else:
                e = entry_from_docx(full)
            if e == "draft":
                drafts += 1
            elif e:
                entries.append(e)
    # 2. hand-made pages
    for folder in ("articles", "chapters"):
        full = os.path.join(ROOT, folder)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            if name.endswith(".qmd"):
                e = entry_from_qmd(os.path.join(full, name))
                if e and e != "stale":
                    entries.append(e)

    # 3. one page per place
    seen = {}
    good = []
    for e in sorted(entries, key=lambda x: x["src"]):
        key = e["path"]
        if key in seen:
            what = e["title"]
            problem(e["src"], "%s is already in %s. Keep one file and delete the other." % (what, seen[key]))
            continue
        seen[key] = e["src"]
        good.append(e)

    # 4. write the pages made from Word files
    for e in good:
        if not e["generated"]:
            continue
        fm = ["---", "title: %s" % json.dumps(e["title"], ensure_ascii=False)]
        if e["kind"] == "chapter":
            fm += ["book: %s" % e["book_name"], "chapter: %d" % e["chapter"]]
        else:
            fm += ["site-section: %s" % e["section"]]
            if e["summary"]:
                fm += ["summary: %s" % json.dumps(e["summary"], ensure_ascii=False)]
        fm += ["posted: %s" % e["posted"].isoformat(), "updated: %s" % e["updated"].isoformat(),
               "source: %s" % e["src"], "---", ""]
        dates = "::: dates\nPosted: %s %s Updated: %s\n:::\n\n" % (long_date(e["posted"]), DOT, long_date(e["updated"]))
        write(e["path"], "\n".join(fm) + "\n" + dates + e["body"])

    # 5. lists
    articles = {s: [e for e in good if e["kind"] == "article" and e["section"] == s]
                for s in ("know-the-truth", "seek-jehovah")}
    chapters = [e for e in good if e["kind"] == "chapter"]
    by_book = {}
    for e in chapters:
        by_book.setdefault(e["book"], []).append(e)
    for lst in by_book.values():
        lst.sort(key=lambda x: x["chapter"])

    for s, name in (("know-the-truth", "KT"), ("seek-jehovah", "SJ")):
        ordered = sorted(articles[s], key=lambda x: (x["posted"], x["title"].lower()), reverse=True)
        lines = "".join("        - %s\n" % e["path"] for e in ordered) or "        []\n"
        replace_between("_quarto.yml", yaml_marker(name, "start"), yaml_marker(name, "end"), lines)
        replace_between(SECTIONS[s]["page"], md_marker("list", "start"), md_marker("list", "end"), article_list(articles[s]))
    pw = ""
    for b in BOOKS:
        if b["slug"] in by_book:
            pw += "        - %s.qmd\n" % b["slug"]
            for e in by_book[b["slug"]]:
                pw += "        - %s\n" % e["path"]
    replace_between("_quarto.yml", yaml_marker("PW", "start"), yaml_marker("PW", "end"), pw or "        []\n")
    replace_between("prophetic-word.qmd", md_marker("hebrew", "start"), md_marker("hebrew", "end"), book_grid("hebrew", by_book))
    replace_between("prophetic-word.qmd", md_marker("greek", "start"), md_marker("greek", "end"), book_grid("greek", by_book))

    # book pages
    for slug, lst in by_book.items():
        b = BOOK_BY_SLUG[slug]
        grid = "".join("- [%d](%s)\n" % (e["chapter"], e["path"]) for e in lst)
        write(slug + ".qmd", '---\ntitle: %s\n---\n\nInformation is available on the chapters listed below.\n\n::: chapter-grid\n%s:::\n'
              % (json.dumps(b["name"]), grid))
    # home
    replace_between("index.qmd", md_marker("news", "start"), md_marker("news", "end"), news_home(good))
    replace_between("whats-new.qmd", md_marker("all", "start"), md_marker("all", "end"), news_all(good))

    print("Pages: %d articles, %d chapters, %d drafts held back, %d problems."
          % (sum(len(v) for v in articles.values()), len(chapters), drafts, len(problems)))

    report = os.path.join(ROOT, "build-problems.txt")
    if problems:
        with open(report, "w", encoding="utf-8") as f:
            for where, msg in problems:
                f.write("%s: %s\n" % (where, msg))
    elif os.path.exists(report):
        os.remove(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            if problems:
                f.write("### Files that were skipped\n\n")
                for where, msg in problems:
                    f.write("- **%s**: %s\n" % (where, msg))
            else:
                f.write("All files were published.\n")


if __name__ == "__main__":
    main()
