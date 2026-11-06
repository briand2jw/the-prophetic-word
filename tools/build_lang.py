#!/usr/bin/env python3
"""
Builds the translated copies of the site (for example /es/, /hi/, /ar/).

Run after the English site is built. For each language that has translations in
_translations/<language>/, this makes a small copy of the project in _build-lang/<language>/,
renders it with Quarto (HTML and EPUB), and puts the result in _site/<language>/.
It also writes _site/languages.json, which the language picker reads.

Usage:  python3 tools/build_lang.py [--lang es] [--mock]
"""
import argparse, copy, json, os, re, shutil, subprocess, sys
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = ["fonts", "icons", "logo.png", "manifest.webmanifest", "sw.js", "epub.css",
          "nav-helpers.html", "templates"]


def filter_chapters(items, have):
    out = []
    for it in items:
        if isinstance(it, str):
            if it in have:
                out.append(it)
        elif isinstance(it, dict):
            part = it.get("part")
            if part and part not in have:
                continue
            new = dict(it)
            if "chapters" in it:
                new["chapters"] = filter_chapters(it["chapters"], have)
            out.append(new)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang"); ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "_translations", "config.json"), encoding="utf-8"))
    tdir = os.path.join(ROOT, "_translations_mock" if args.mock else "_translations")
    base = yaml.safe_load(open(os.path.join(ROOT, "_quarto.yml"), encoding="utf-8"))
    site = os.path.join(ROOT, "_site")
    available = {"en": {"name": "English", "native": "English", "dir": "ltr"}}
    problems = []
    for lang, lc in cfg["languages"].items():
        if args.lang and lang != args.lang:
            continue
        src = os.path.join(tdir, lang)
        if not os.path.isdir(src):
            continue
        have = set()
        for dp, _, fs in os.walk(src):
            for f in fs:
                if f.endswith(".qmd") and ".new." not in f:
                    rel = os.path.relpath(os.path.join(dp, f), src)
                    if re.search(r"^(<<<<<<<|>>>>>>>)", open(os.path.join(dp, f), encoding="utf-8").read(), re.M):
                        problems.append(f"{lang}/{rel}: contains unfinished merge marks, so this page was left out")
                        continue
                    have.add(rel)
        if "index.qmd" not in have:
            print(f"{lang}: no translated home page yet, skipped")
            continue
        work = os.path.join(ROOT, "_build-lang", lang)
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        for rel in have:
            dest = os.path.join(work, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(os.path.join(src, rel), dest)
        for a in ASSETS:
            s = os.path.join(ROOT, a)
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(work, a))
            elif os.path.exists(s):
                shutil.copy(s, os.path.join(work, a))
        for f in os.listdir(ROOT):
            if re.match(r"^(style-.*|fonts)\.css$", f):
                shutil.copy(os.path.join(ROOT, f), os.path.join(work, f))
        if os.path.exists(os.path.join(src, "_language.yml")):
            shutil.copy(os.path.join(src, "_language.yml"), os.path.join(work, "_language.yml"))
        y = copy.deepcopy(base)
        y["project"]["output-dir"] = "_site"
        y["book"].pop("language", None)
        if os.path.exists(os.path.join(work, "_language.yml")):
            y["language"] = "_language.yml"
        y["book"]["chapters"] = filter_chapters(y["book"]["chapters"], have)
        html = y["format"]["html"]
        html["css"] = list(html["css"]) + ["lang.css"]
        y["lang"] = lang
        if lc.get("dir") == "rtl":
            html["dir"] = "rtl"
        head = html.get("include-in-header", {})
        text = head.get("text", "") if isinstance(head, dict) else ""
        text += f'<meta name="pl-lang" content="{lang}">\n'
        html["include-in-header"] = {"text": text}
        shutil.copy(os.path.join(ROOT, "_translations", "lang.css"), os.path.join(work, "lang.css"))
        with open(os.path.join(work, "_quarto.yml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(y, f, allow_unicode=True, sort_keys=False)
        r = subprocess.run(["quarto", "render"], cwd=work, capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"{lang}: the page build failed:\n{r.stderr[-600:]}")
            print(r.stderr[-1500:])
            continue
        out = os.path.join(site, lang)
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(os.path.join(work, "_site"), out)
        available[lang] = {"name": lc["name"], "native": lc["native"], "dir": lc.get("dir", "ltr"),
                           "notice": lc.get("notice", ""), "original": lc.get("original", "")}
        print(f"built {lang}")
    if os.path.isdir(site):
        old = os.path.join(site, "languages.json")
        with open(old, "w", encoding="utf-8") as f:
            json.dump(available, f, ensure_ascii=False, indent=1)
    if problems:
        with open(os.path.join(ROOT, "translation-problems.txt"), "a", encoding="utf-8") as f:
            f.write("\n".join(problems) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
