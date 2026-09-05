"""Turn script_te.tsv into things a person can actually read.

Telugu does not render correctly in a terminal — conjuncts and vowel signs are
reshaped by the font, and a monospace terminal does not do that shaping. So the
speaker never reads the TSV. This produces:

    sections/NN_name.txt   one file per section, plain UTF-8
    sessions/session N.txt one file per recording sitting
    script_te.html         open in a browser — correct shaping, large type

The TSV stays the single source of truth; everything here is generated.

    ./venv/bin/python scripts/build_voice_script.py
"""

import csv
import html
import shutil
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "docs" / "voice_training"
TSV = BASE / "script_te.tsv"

# Which sections belong to which sitting, and what to call them.
SESSIONS = OrderedDict([
    ("Session 1 — sounds of Telugu",
     ["vowels", "consonants", "clusters", "gemination", "minimal", "rare"]),
    ("Session 2 — numbers, English words, everyday speech",
     ["numbers", "tenglish", "tenglish2", "natural"]),
    ("Session 3 — the call itself, and tone of voice",
     ["call_open", "call_pitch", "call_qual", "objection", "call_close", "call_more",
      "question", "exclaim", "empathy", "apology", "filler", "confirm",
      "warm", "calm", "energetic", "prosody", "dense"]),
])

TITLES = {
    "vowels": "అచ్చులు — vowels",
    "consonants": "హల్లులు — consonants",
    "clusters": "సంయుక్తాక్షరాలు — consonant clusters",
    "gemination": "ద్విత్వాక్షరాలు — doubled consonants",
    "minimal": "పోలిన పదాలు — similar-sounding pairs",
    "rare": "అరుదైన అక్షరాలు — the rare letters",
    "numbers": "సంఖ్యలు, సమయం, డబ్బు — numbers, time, money",
    "tenglish": "English words in Telugu sentences",
    "tenglish2": "More English words",
    "natural": "సహజ సంభాషణ — everyday speech",
    "call_open": "Call — greeting",
    "call_pitch": "Call — explaining LAMPOSE",
    "call_qual": "Call — asking questions",
    "objection": "Call — when the owner says no",
    "call_close": "Call — ending",
    "call_more": "Call — longer lines",
    "question": "ప్రశ్నలు — questions (voice rises)",
    "exclaim": "ఆశ్చర్యం — surprise and delight",
    "empathy": "సానుభూతి — understanding",
    "apology": "క్షమాపణ — apologising",
    "filler": "ఆలోచిస్తూ — thinking sounds",
    "confirm": "ఖచ్చితత్వం — yes and no",
    "warm": "వెచ్చగా — warmth",
    "calm": "ప్రశాంతంగా — calm",
    "energetic": "ఉత్సాహంగా — energetic",
    "prosody": "భావ ప్రకటన — expression",
    "dense": "కఠినమైన వాక్యాలు — dense sentences",
}

# Telugu first, then whatever the reader's machine has. Works offline.
FONT = ("'Noto Sans Telugu', 'Telugu Sangam MN', 'Kohinoor Telugu', "
        "'Gautami', 'Nirmala UI', 'Pothana2000', sans-serif")


def load():
    with open(TSV, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def by_section(rows):
    out = OrderedDict()
    for r in rows:
        out.setdefault(r["section"], []).append(r)
    return out


HEADER = """LAMPOSE — Telugu voice recording
{title}
{count} sentences

Record ONE file per line. Name the file with the ID on the left: A001.wav
Leave half a second of silence before and after each line.
If you stumble, just record that line again.
{rule}
"""


def write_text_files(sections):
    sect_dir = BASE / "sections"
    sess_dir = BASE / "sessions"
    for d in (sect_dir, sess_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    for i, (name, rows) in enumerate(sections.items(), 1):
        title = TITLES.get(name, name)
        body = "\n\n".join(f"{r['id']}   {r['text']}" for r in rows)
        (sect_dir / f"{i:02d}_{name}.txt").write_text(
            HEADER.format(title=title, count=len(rows), rule="-" * 60) + "\n" + body + "\n",
            encoding="utf-8")

    for n, (title, names) in enumerate(SESSIONS.items(), 1):
        parts, total = [], 0
        for name in names:
            rows = sections.get(name)
            if not rows:
                continue
            total += len(rows)
            parts.append(f"\n\n{'=' * 60}\n{TITLES.get(name, name)}  ({len(rows)})\n{'=' * 60}\n\n"
                         + "\n\n".join(f"{r['id']}   {r['text']}" for r in rows))
        (sess_dir / f"session{n}.txt").write_text(
            HEADER.format(title=title, count=total, rule="=" * 60) + "".join(parts) + "\n",
            encoding="utf-8")
    return sect_dir, sess_dir


def write_html(sections, rows):
    parts = [f"""<!doctype html>
<html lang="te"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LAMPOSE — Telugu recording script</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Telugu:wght@400;600&display=swap"
      rel="stylesheet">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: {FONT}; max-width: 46rem; margin: 0 auto; padding: 1.5rem 1.2rem 6rem;
         line-height: 2.1; font-size: 1.35rem; background: #fbfaf8; color: #1a1a1a; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16181c; color: #e9e6e1; }} }}
  h1 {{ font-size: 1.5rem; line-height: 1.4; }}
  h2 {{ font-size: 1.15rem; margin: 2.5rem 0 .5rem; padding-top: 1rem;
        border-top: 1px solid rgba(128,128,128,.35); color: #b45309; }}
  @media (prefers-color-scheme: dark) {{ h2 {{ color: #f0a860; }} }}
  .note {{ font-size: .95rem; line-height: 1.7; opacity: .75; }}
  ol {{ list-style: none; padding: 0; }}
  li {{ display: flex; gap: .9rem; align-items: baseline; padding: .55rem 0;
        border-bottom: 1px solid rgba(128,128,128,.15); }}
  .id {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .8rem;
         opacity: .55; min-width: 3.4rem; }}
  @media print {{ body {{ background: #fff; color: #000; font-size: 12pt; }}
                  h2 {{ page-break-after: avoid; }} li {{ page-break-inside: avoid; }} }}
</style></head><body>
<h1>LAMPOSE — Telugu voice recording script</h1>
<p class="note">{len(rows)} sentences, about 29 minutes of speech.
Record <strong>one file per line</strong> and name it with the code on the left
(A001.wav). Half a second of silence before and after each line. Stumbled?
Just record that line again.</p>"""]
    for name, items in sections.items():
        parts.append(f"<h2>{html.escape(TITLES.get(name, name))} "
                     f"<span class='note'>({len(items)})</span></h2><ol>")
        for r in items:
            parts.append(f"<li><span class='id'>{r['id']}</span>"
                         f"<span>{html.escape(r['text'])}</span></li>")
        parts.append("</ol>")
    parts.append("</body></html>")
    path = BASE / "script_te.html"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


if __name__ == "__main__":
    rows = load()
    sections = by_section(rows)
    sect_dir, sess_dir = write_text_files(sections)
    page = write_html(sections, rows)
    print(f"{len(rows)} sentences in {len(sections)} sections\n")
    print(f"  {len(list(sect_dir.glob('*.txt')))} section files -> {sect_dir.relative_to(ROOT)}/")
    for f in sorted(sess_dir.glob("*.txt")):
        n = sum(1 for line in f.read_text(encoding='utf-8').splitlines()
                if line[:1].isalpha() and line[1:4].isdigit())
        print(f"  {f.relative_to(ROOT)}  ({n} sentences)")
    print(f"  {page.relative_to(ROOT)}  <- open this in a browser")
