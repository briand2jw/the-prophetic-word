#!/usr/bin/env python3
"""
Translates the site's English pages into the languages listed in _translations/config.json.

How it works
  - Reads the page list from _quarto.yml (run it after tools/build_site.py).
  - For each language and each page, asks Claude for a translation and saves it in
    _translations/<language>/ (same file names as the English pages).
  - Remembers which English text each translation came from, so only pages that
    changed are translated again. Unchanged pages cost nothing.
  - A translation that a person has edited by hand is never overwritten. If its English
    page changes later, the new machine translation is saved next to it as
    <name>.new.qmd and listed in translation-problems.txt.
  - Checks every translation keeps the page's structure (headings, boxes, links, front matter).
    A translation that fails the check is not saved, and is listed in translation-problems.txt.

Usage
  python3 tools/translate.py                 translate whatever changed (needs ANTHROPIC_API_KEY)
  python3 tools/translate.py --lang es       one language only
  python3 tools/translate.py --force         translate everything again
  python3 tools/translate.py --mock          fake translations for testing (no key, no cost)
"""
import argparse, hashlib, json, os, re, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_strings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = os.path.join(ROOT, "_translations")
PROMPT_VERSION = "1"
API = "https://api.anthropic.com/v1/messages"
TEXT_KEYS = {"title", "description", "subtitle"}   # front-matter values that get translated


def load_config():
    with open(os.path.join(TR, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def page_list():
    """Every page the book lists, in order."""
    with open(os.path.join(ROOT, "_quarto.yml"), encoding="utf-8") as f:
        txt = f.read()
    pages = re.findall(r"^\s*-\s*(?:part:\s*)?(\S+\.qmd)\s*$", txt, re.M)
    pages += re.findall(r"^\s*-\s*part:\s*(\S+\.qmd)\s*$", txt, re.M)
    seen, out = set(), []
    for p in pages:
        if p not in seen and os.path.exists(os.path.join(ROOT, p)):
            seen.add(p)
            out.append(p)
    return out


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------- structure check ----------
def split_fm(t):
    m = re.match(r"^---\n(.*?)\n---\n?", t, re.S)
    return (m.group(1), t[m.end():]) if m else ("", t)


def fm_pairs(fm):
    out = {}
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def signature(t):
    fm, body = split_fm(t)
    pairs = fm_pairs(fm)
    return {
        "fm_keys": list(pairs.keys()),
        "fm_fixed": {k: v for k, v in pairs.items() if k not in TEXT_KEYS},
        "fences": sum(1 for l in body.splitlines() if l.startswith(":::")),
        "heads": [len(m.group(1)) for m in re.finditer(r"^(#{1,6})\s", body, re.M)],
        "attrs": sorted(re.findall(r"\{[^{}\n]*[.#=][^{}\n]*\}", body)),
        "links": sorted(re.findall(r"\]\(([^)\s]+)", body)),
        "code": body.count("```"),
        "html": sorted(re.findall(r"<[a-zA-Z/][^>\n]*>", body)),
    }


def check(src, out):
    a, b = signature(src), signature(out)
    bad = [k for k in a if a[k] != b[k]]
    if not out.startswith("---"):
        bad.append("front matter missing")
    return bad


# ---------- the model ----------
def system_prompt(lang, cfg_lang, glossary):
    bible = cfg_lang.get("bible") or "the most widely used standard Bible in that language"
    return f"""You are a careful translator of a Bible commentary website from English into {cfg_lang['name']} ({lang}).
The readers are ordinary believers. Write natural, clear, reverent {cfg_lang['name']}, not word for word.

Faithfulness
- Never add, remove, soften or reinterpret any teaching or claim. Translate what the author wrote.
- Scripture: translate faithfully and conservatively, in the style of {bible}. Do not paraphrase.
  Keep verse numbers and references exact (for example 7:14). Use the standard {cfg_lang['name']} name of each Bible book.
- Follow this glossary exactly:
{glossary}

File format (very important)
- The input is a Quarto Markdown (.qmd) file. Return the COMPLETE translated file and nothing else:
  no commentary, no code fences around it.
- Keep the YAML front matter keys exactly. Translate only the values of title, description and subtitle.
  Leave every other front-matter value exactly as it is (book, chapter, posted, updated, source, and so on).
- Keep all structure exactly: heading levels, fenced-div lines (:::), list markers, blank lines,
  attribute blocks such as {{.main-sub}} or {{.epub-btn}}, HTML tags, and every link target
  (the part in parentheses). Translate link text only.
- Keep bold and italic markers around the matching translated words.
- Dates such as "Posted Oct 25, 2026" are translated into {cfg_lang['name']} with the same date, in that language's usual form.
- Do not translate file names, class names, URLs, or the site name "The Pure Language".
- Straight line breaks written as a backslash at the end of a line stay as they are."""


def call_model(model, system, user, key):
    body = json.dumps({"model": model, "max_tokens": 16000, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode("utf-8")
    delay = 5
    for attempt in range(6):
        req = urllib.request.Request(API, data=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
            return text, data.get("usage", {})
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 529) and attempt < 5:
                time.sleep(delay); delay *= 2; continue
            raise RuntimeError(f"API error {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 5:
                time.sleep(delay); delay *= 2; continue
            raise RuntimeError(f"network error: {e}")


def strip_fences(t):
    t = t.strip("\n")
    m = re.match(r"^```[a-z]*\n(.*)\n```$", t, re.S)
    return (m.group(1) if m else t).rstrip("\n") + "\n"


# ---------- fake translations for testing ----------
MOCK = {
    "es": "la palabra verdad camino luz vida fe amor paz tiempo reino gloria Señor pueblo tierra cielo".split(),
    "hi": "वचन सत्य मार्ग ज्योति जीवन विश्वास प्रेम शांति समय राज्य महिमा प्रभु लोग पृथ्वी स्वर्ग".split(),
    "ar": "الكلمة الحق الطريق النور الحياة الإيمان المحبة السلام الوقت الملكوت المجد الرب الشعب الأرض السماء".split(),
}


def mock_translate(text, lang):
    words = MOCK.get(lang, MOCK["es"])
    i = [0]

    def sub_words(seg):
        def rep(m):
            w = words[i[0] % len(words)]; i[0] += 1
            return w
        return re.sub(r"[A-Za-z][A-Za-z’']*", rep, seg)

    def tr_line(line):
        if line.startswith(":::") or not line.strip():
            return line
        parts = re.split(r"(\([^)]*\)|\{[^}]*\}|<[^>]*>)", line)
        return "".join(p if re.match(r"^(\(|\{|<)", p) else sub_words(p) for p in parts)

    fm, body = split_fm(text)
    out_fm = []
    for line in fm.splitlines():
        m = re.match(r"^(title|description|subtitle):\s*(.*)$", line)
        out_fm.append(f"{m.group(1)}: " + sub_words(m.group(2)) if m else line)
    return "---\n" + "\n".join(out_fm) + "\n---\n" + "\n".join(tr_line(l) for l in body.split("\n"))



# ---------- buttons, windows and messages (the text inside nav-helpers.html) ----------
def ui_system(lang, lc, glossary):
    return f"""You translate the buttons, windows and messages of a Bible commentary website from English into {lc['name']} ({lang}).
Write short, natural, reverent {lc['name']} that ordinary readers understand. Buttons and labels must stay short.

You receive a JSON object. Each key is an id and each value is one English string. Return ONE JSON object with the same ids and the translated strings, and nothing else.

Rules for every string
- Some strings are pieces of HTML. Keep every tag, every attribute and every attribute value EXACTLY as written
  (class, id, data-*, type, role, href, for, and so on). Translate only the words between tags and the words inside
  aria-label, placeholder, title and alt attributes. Keep &entities; and characters such as ✕ and arrows as they are.
- Keep placeholders such as {{0}} exactly, and put them where the sentence needs them.
- Do not translate the site name "The Pure Language".
- Names of buttons and windows are used in several places and must always be translated the same way:
  My Notes, Bookmarks, Share, Print, Translate, Copy Link, Save a Backup, Everything, Text size, Highlights, Notes.
- Never add, remove or soften meaning. Warnings such as "This cannot be undone" must stay clear.
- Follow this glossary exactly:
{glossary}"""


def ui_check(src, out):
    if not isinstance(out, str) or not out.strip():
        return ["empty"]
    return [] if ui_strings.skeleton(src) == ui_strings.skeleton(out) else ["tags or placeholders changed"]


def mock_ui(text, lang):
    words = MOCK.get(lang, MOCK["es"])
    i = [0]

    def sub_words(seg):
        def rep(m):
            w = words[i[0] % len(words)]; i[0] += 1
            return w
        return re.sub(r"(?<!\{)[A-Za-z][A-Za-z’']*", rep, seg)
    parts = re.split(r'(<[^>]*>|\{\d\}|(?:aria-label|placeholder|title|alt)="[^"]*")', text)
    out = []
    for p in parts:
        m = re.match(r'^((?:aria-label|placeholder|title|alt)=")([^"]*)(")$', p)
        if m:
            out.append(m.group(1) + sub_words(m.group(2)) + m.group(3))
        elif p.startswith("<") or re.match(r"^\{\d\}$", p):
            out.append(p)
        else:
            out.append(sub_words(p))
    # attribute values sit inside tags, so translate them in place
    res = "".join(out)
    return re.sub(r'((?:aria-label|placeholder|title|alt)=")([^"]*)(")', lambda m: m.group(1) + sub_words(m.group(2)) + m.group(3), res) if False else res


def translate_ui(lang, lc, ldir, cfg, key, glossary, mock, force, problems):
    src = open(os.path.join(ROOT, "nav-helpers.html"), encoding="utf-8").read()
    items = ui_strings.collect(src)
    path = os.path.join(ldir, "ui.json")
    cat = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    if force:
        cat = {}
    todo = [t for t in items if t not in cat]
    t_in = t_out = 0
    if not todo:
        return 0, 0, 0
    done = 0
    system = ui_system(lang, lc, glossary)
    for k in range(0, len(todo), 20):
        batch = todo[k:k + 20]
        if mock:
            for t in batch:
                cat[t] = mock_ui(t, lang)
            done += len(batch)
            continue
        payload = {str(n + 1): t for n, t in enumerate(batch)}
        result = {}
        for attempt in range(2):
            try:
                text, usage = call_model(cfg["model"], system, json.dumps(payload, ensure_ascii=False), key)
                t_in += usage.get("input_tokens", 0); t_out += usage.get("output_tokens", 0)
                m = re.search(r"\{.*\}", text, re.S)
                result = json.loads(m.group(0)) if m else {}
                break
            except Exception as e:
                last = str(e)
        for n, t in enumerate(batch):
            out = result.get(str(n + 1))
            bad = ui_check(t, out)
            if bad:
                try:   # one more try, on its own
                    text, usage = call_model(cfg["model"], system, json.dumps({"1": t}, ensure_ascii=False), key)
                    t_in += usage.get("input_tokens", 0); t_out += usage.get("output_tokens", 0)
                    m = re.search(r"\{.*\}", text, re.S)
                    out = json.loads(m.group(0)).get("1") if m else None
                    bad = ui_check(t, out)
                except Exception as e:
                    bad = [str(e)]
            if bad:
                problems.append(f"{lang}/ui.json: kept English for \"{t[:50]}\" ({', '.join(bad)})")
            else:
                cat[t] = out
                done += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1, sort_keys=True)
    return done, t_in, t_out


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--mock", action="store_true"); ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    cfg = load_config()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key and not args.mock:
        print("No ANTHROPIC_API_KEY found, so nothing was translated.")
        return 0
    with open(os.path.join(TR, "glossary.md"), encoding="utf-8") as f:
        glossary = f.read()
    pages = page_list()
    problems, total_in, total_out, done = [], 0, 0, 0
    langs = [args.lang] if args.lang else list(cfg["languages"])
    for lang in langs:
        lc = cfg["languages"][lang]
        ldir = os.path.join(ROOT, "_translations_mock" if args.mock else "_translations", lang)
        os.makedirs(ldir, exist_ok=True)
        hpath = os.path.join(ldir, "hashes.json")
        hashes = json.load(open(hpath, encoding="utf-8")) if os.path.exists(hpath) else {}
        system = system_prompt(lang, lc, glossary)
        jobs = [(p, p) for p in pages] + [("_language-en.yml", "_language.yml")]
        for src_rel, out_rel in jobs:
            src_path = os.path.join(ROOT, src_rel) if src_rel.endswith(".qmd") else os.path.join(TR, src_rel)
            src = open(src_path, encoding="utf-8").read()
            sh = sha(src + "|" + lang + "|" + PROMPT_VERSION)
            out_path = os.path.join(ldir, out_rel)
            rec = hashes.get(out_rel, {})
            if not args.force and rec.get("src") == sh and os.path.exists(out_path):
                continue
            if args.limit and done >= args.limit:
                continue
            edited = os.path.exists(out_path) and rec.get("out") and sha(open(out_path, encoding="utf-8").read()) != rec["out"]
            try:
                if args.mock:
                    out = mock_translate(src, lang) if src_rel.endswith(".qmd") else src
                else:
                    user = src if src_rel.endswith(".qmd") else (
                        "Translate the quoted values of this YAML file. Keep every key and every {0} placeholder. Return only the YAML.\n\n" + src)
                    out, note = "", ""
                    for attempt in range(3):
                        text, usage = call_model(cfg["model"], system, user + note, key)
                        total_in += usage.get("input_tokens", 0); total_out += usage.get("output_tokens", 0)
                        out = strip_fences(text)
                        bad = check(src, out) if src_rel.endswith(".qmd") else []
                        if not bad:
                            break
                        note = "\n\n(Your previous answer changed the file's structure: %s. Return the file again, keeping the structure exactly.)" % ", ".join(bad)
                    else:
                        raise RuntimeError("structure check failed: " + ", ".join(bad))
                if edited:
                    out_path = os.path.splitext(out_path)[0] + ".new" + os.path.splitext(out_path)[1]
                    problems.append(f"{lang}/{out_rel}: edited by hand; the English changed, so a new version was saved as {os.path.basename(out_path)} for you to compare")
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(out)
                if not edited:
                    hashes[out_rel] = {"src": sh, "out": sha(out)}
                done += 1
                print(f"translated {lang}/{out_rel}")
            except Exception as e:
                problems.append(f"{lang}/{out_rel}: {e}")
                print(f"FAILED {lang}/{out_rel}: {e}")
        try:
            n, ti, to = translate_ui(lang, lc, ldir, cfg, key, glossary, args.mock, args.force, problems)
            total_in += ti; total_out += to; done += n
            if n:
                print(f"translated {n} button/window texts for {lang}")
        except Exception as e:
            problems.append(f"{lang}/ui.json: {e}")
        with open(hpath, "w", encoding="utf-8") as f:
            json.dump(hashes, f, indent=1, sort_keys=True, ensure_ascii=False)
    print(f"Done. {done} file(s) translated. Tokens used: {total_in} in, {total_out} out.")
    with open(os.path.join(ROOT, "translation-problems.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(problems) + ("\n" if problems else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
