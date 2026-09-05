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
{count} sentences  —  about {mins}

ఈ section మొత్తానికి ఒకే recording. ప్రతి వాక్యానికీ ఆపాల్సిన అవసరం లేదు.
  1. Record నొక్కి, రెండు సెకన్లు ఏమీ మాట్లాడకండి.
  2. ఒక వాక్యం చదవండి.
  3. మూడు సెకన్లు ఆగండి (మనసులో: ఒకటి, రెండు, మూడు). Record ఆపవద్దు.
  4. తర్వాతి వాక్యం చదవండి. ఇలాగే చివరి వరకు.
  5. చివరిలో మూడు సెకన్లు ఆగి, Stop నొక్కండి.

తప్పు జరిగితే: ఆగండి, ఏమీ చెప్పకండి, మూడు సెకన్లు ఆగి, అదే వాక్యం మళ్ళీ చదవండి.

File పేరు: {filename}
{rule}
"""


GAP_SECONDS = 3.0        # silence she leaves between sentences
RETAKE_ALLOWANCE = 1.25  # a beginner re-reads roughly one line in five


def timing(rows):
    """Speech time and realistic wall-clock time for a set of sentences."""
    speech = sum(len(r["text"]) for r in rows) / 11.0
    wall = (speech + GAP_SECONDS * len(rows) + 2) * RETAKE_ALLOWANCE
    return speech, wall


def mins(secs):
    return f"{secs/60:.0f} నిమిషాలు" if secs >= 90 else f"{secs:.0f} సెకన్లు"


def write_text_files(sections):
    sect_dir = BASE / "sections"
    sess_dir = BASE / "sessions"
    for d in (sect_dir, sess_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    for i, (name, rows) in enumerate(sections.items(), 1):
        title = TITLES.get(name, name)
        body = "\n\n".join(f"{r['id']}   {r['text']}" for r in rows)
        _, wall = timing(rows)
        (sect_dir / f"{i:02d}_{name}.txt").write_text(
            HEADER.format(title=title, count=len(rows), mins=mins(wall),
                          filename=f"{i:02d}_{name}", rule="-" * 60)
            + "\n" + body + "\n", encoding="utf-8")

    for n, (title, names) in enumerate(SESSIONS.items(), 1):
        parts, total = [], 0
        for name in names:
            rows = sections.get(name)
            if not rows:
                continue
            total += len(rows)
            parts.append(f"\n\n{'=' * 60}\n{TITLES.get(name, name)}  ({len(rows)})\n{'=' * 60}\n\n"
                         + "\n\n".join(f"{r['id']}   {r['text']}" for r in rows))
        rows_in = [r for nm in names for r in sections.get(nm, [])]
        _, wall = timing(rows_in)
        (sess_dir / f"session{n}.txt").write_text(
            HEADER.format(title=title, count=total, mins=mins(wall),
                          filename="ఒక్కో section కి ఒక file — కింద చూడండి",
                          rule="=" * 60) + "".join(parts) + "\n", encoding="utf-8")
    return sect_dir, sess_dir


GUIDE = [
    ("1. ఇది ఏమిటి?", [
        "మీ గొంతుతో ఒక కంప్యూటర్ వాయిస్ తయారు చేస్తున్నాం.",
        "మీరు ఈ వాక్యాలు చదివితే చాలు — ఇంకేమీ చేయనవసరం లేదు.",
        "మీరు ఎలా మాట్లాడతారో కంప్యూటర్ కూడా అలాగే మాట్లాడుతుంది. అందుకే మీరు "
        "మామూలుగా మాట్లాడితే చాలు.",
    ]),
    ("2. ఎక్కడ కూర్చోవాలి?", [
        "చిన్న గది మంచిది — మంచం, కర్టెన్లు, బట్టలు ఉన్న గది అయితే ఇంకా మంచిది.",
        "ఫ్యాన్ ఆఫ్ చేయండి. ఏసీ ఆఫ్ చేయండి. ఇది చాలా ముఖ్యం.",
        "కిటికీలు, తలుపులు మూసేయండి. ఫోన్ సైలెంట్‌లో పెట్టండి.",
        "హాల్‌లో గానీ, బాత్‌రూంలో గానీ వద్దు — అక్కడ ప్రతిధ్వని వస్తుంది.",
    ]),
    ("3. ఫోన్ ఎలా పెట్టుకోవాలి?", [
        "Voice Recorder app తెరిచి, settings లో WAV అని పెట్టండి. MP3 వద్దు.",
        "ఫోన్‌ని నోటికి ఒక జానెడు దూరంలో పెట్టండి — సుమారు ఇరవై సెంటీమీటర్లు.",
        "నోటికి నేరుగా కాకుండా కొంచెం పక్కకి పెట్టండి.",
        "ఫోన్‌ని చేతిలో పట్టుకోవద్దు. పుస్తకాల మీద పెట్టండి — చేతి శబ్దం రికార్డ్ అవుతుంది.",
    ]),
    ("4. ఎలా చదవాలి?", [
        "ఫోన్‌లో ఒక మనిషితో మాట్లాడుతున్నట్టు చదవండి.",
        "న్యూస్ చదివినట్టు వద్దు. నాటకం లాగా వద్దు.",
        "మామూలుగా, మర్యాదగా, కొంచెం చిరునవ్వుతో.",
        "మొదటి వాక్యం ఎలా చదివారో, చివరి వాక్యం కూడా సరిగ్గా అలాగే చదవాలి. "
        "ఇది అన్నిటికంటే ముఖ్యం.",
    ]),
    ("5. రికార్డ్ ఎలా చేయాలి? — ఇది ముఖ్యమైన భాగం", [
        "ఒక section మొత్తానికి ఒకే recording. ప్రతి వాక్యానికీ ఆపాల్సిన అవసరం లేదు.",
        "మొదట Record బటన్ నొక్కండి.",
        "మనసులో ఒకటి, రెండు అనుకోండి — ఏమీ మాట్లాడకండి. (రెండు సెకన్లు)",
        "మొదటి వాక్యం చదవండి.",
        "చదవడం అయ్యాక మనసులో ఒకటి, రెండు, మూడు అనుకోండి. (మూడు సెకన్లు) "
        "Record ఆపవద్దు.",
        "తర్వాతి వాక్యం చదవండి. ఇలాగే section మొత్తం చదవండి.",
        "చివరిలో మూడు సెకన్లు ఆగి, అప్పుడు Stop నొక్కండి.",
        "ఆ మూడు సెకన్ల నిశ్శబ్దం చాలా అవసరం — దాని సాయంతోనే మేము వాక్యాలను "
        "విడివిడిగా వేరు చేస్తాం.",
    ]),
    ("6. తప్పు జరిగితే?", [
        "కంగారు పడకండి. ఇది మామూలే.",
        "ఆగిపోండి. సారీ అని కూడా చెప్పకండి — ఏమీ మాట్లాడకండి.",
        "మనసులో ఒకటి, రెండు, మూడు అనుకోండి.",
        "అదే వాక్యం మొదటి నుంచి మళ్ళీ చదవండి.",
        "Record ఆపవద్దు. తప్పు భాగాన్ని మేము తీసేస్తాం.",
    ]),
    ("7. విశ్రాంతి", [
        "ప్రతి పదిహేను నిమిషాలకు ఒకసారి మూడు నిమిషాలు ఆగండి.",
        "గోరువెచ్చని నీరు తాగండి. చల్లని నీరు వద్దు.",
        "ఒక రోజులో నలభై ఐదు నిమిషాల కంటే ఎక్కువ చేయవద్దు — గొంతు అలసిపోతుంది.",
        "మొత్తం మూడు sessions. వేరే రోజుల్లో చేసినా ఫర్వాలేదు.",
        "కానీ ప్రతిసారీ అదే గది, అదే ఫోన్, అదే దూరం. ఇది మారితే వాయిస్ మారిపోతుంది.",
    ]),
    ("8. ఫైల్ పేరు ఎలా పెట్టాలి?", [
        "ప్రతి section కి ఒక file. ఆ section పేరే file పేరుగా పెట్టండి.",
        "ఉదాహరణకు: 01_vowels, 02_consonants, 03_clusters — ఇలా.",
        "కింద ప్రతి section పేరు ఇచ్చాం. అదే పేరు పెట్టండి.",
    ]),
    ("9. మొదలుపెట్టే ముందు — రెండు చిన్న పనులు", [
        "మొదట పది సెకన్లు ఏమీ మాట్లాడకుండా record చేయండి. గది శబ్దం తెలుసుకోవడానికి. "
        "దాని పేరు: room_tone",
        "తర్వాత రెండు మూడు వాక్యాలు record చేసి, వెనక్కి విని చూడండి.",
        "మీ గొంతు స్పష్టంగా వినిపిస్తే కొనసాగించండి. లేకపోతే ఫోన్ కొంచెం దగ్గరగా పెట్టండి.",
    ]),
]


GUIDE_EN = [
    ("1. What you are doing", [
        "We are building a Telugu voice for a computer, using your voice.",
        "All you have to do is read the sentences in this booklet out loud.",
        "The computer will end up speaking the way you speak — so just speak "
        "normally. You do not have to perform.",
    ]),
    ("2. Where to sit", [
        "A small room is best — a bedroom with a bed, curtains and clothes in it.",
        "Switch the fan OFF. Switch the AC OFF. This matters more than anything else.",
        "Close the windows and the door. Put your phone on silent.",
        "Do not record in a hall or a bathroom — the echo cannot be removed later.",
    ]),
    ("3. Setting up the phone", [
        "Open the voice recorder app and set the format to WAV in its settings. "
        "Not MP3, and not the iPhone Voice Memos app, which records .m4a.",
        "Put the phone about one hand-span from your mouth — roughly 20 cm.",
        "Point it slightly to the side of your mouth, not straight at it. "
        "That keeps 'p' and 'b' sounds from thumping.",
        "Rest the phone on a pile of books. Do not hold it — your hand makes noise.",
    ]),
    ("4. How to read", [
        "Read as if you are talking to a real person on the phone.",
        "Not like reading the news. Not like acting.",
        "Ordinary, polite, with a slight smile in your voice.",
        "Read the last sentence exactly the way you read the first one. "
        "Staying the same all the way through is the single most important thing.",
    ]),
    ("5. How to record — the important part", [
        "One recording for a whole section. You do NOT stop after each sentence.",
        "Press record.",
        "Count TWO seconds in your head. Say nothing.",
        "Read one sentence.",
        "Count THREE seconds in your head — one, two, three. Do NOT stop recording.",
        "Read the next sentence. Carry on to the end of the section.",
        "At the end, wait three seconds, then press stop.",
        "Those three-second gaps are how we cut the recording into separate "
        "sentences afterwards, so please keep them clear.",
    ]),
    ("6. If you make a mistake", [
        "Do not worry — this happens to everyone.",
        "Stop. Say nothing at all — not even 'sorry'.",
        "Count three seconds.",
        "Read the same sentence again from the beginning.",
        "Do not stop the recording. We will remove the bad attempt.",
    ]),
    ("7. Rest", [
        "Take a three-minute break every fifteen minutes.",
        "Drink room-temperature water. Not cold water.",
        "Do not record for more than forty-five minutes in a day — your voice tires "
        "and starts to sound different.",
        "There are three sittings in total. Different days are fine.",
        "But use the same room, the same phone and the same distance every time. "
        "If that changes, the voice changes.",
    ]),
    ("8. Naming the files", [
        "Save one file per section, named after that section.",
        "For example: 01_vowels, 02_consonants, 03_clusters, and so on.",
        "The name to use is printed at the start of every section in this booklet.",
    ]),
    ("9. Two things to do before you start", [
        "Record ten seconds of silence without speaking, so we can hear what the "
        "room sounds like. Name it room_tone.",
        "Then record two or three sentences and play them back.",
        "If your voice is clear and there is no hum or echo, carry on. "
        "If it sounds far away, move the phone a little closer.",
    ]),
]


def write_guide_text(sections):
    lines = ["LAMPOSE — రికార్డింగ్ గైడ్", "=" * 50, ""]
    for title, points in GUIDE:
        lines += [title, "-" * len(title)]
        lines += [f"  • {p}" for p in points] + [""]
    lines += ["10. ఎంత సమయం పడుతుంది?", "-" * 26, ""]
    for i, (name, rows) in enumerate(sections.items(), 1):
        speech, wall = timing(rows)
        lines.append(f"  {i:02d}_{name:<12} {len(rows):3d} వాక్యాలు   సుమారు {mins(wall)}")
    total_speech, total_wall = timing([r for rows in sections.values() for r in rows])
    lines += ["", f"  మొత్తం: {total_wall/60:.0f} నిమిషాలు "
                  f"(మూడు sessions గా విడగొట్టండి)", ""]
    path = BASE / "speaker_guide_te.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_html(sections, rows):
    guide_html = "".join(
        f"<h3>{html.escape(t)}</h3><ul>"
        + "".join(f"<li>{html.escape(p)}</li>" for p in pts) + "</ul>"
        for t, pts in GUIDE)
    total_speech, total_wall = timing(rows)
    guide_html += "<h3>10. ఎంత సమయం పడుతుంది?</h3><table>"
    for i, (name, items) in enumerate(sections.items(), 1):
        _, wall = timing(items)
        guide_html += (f"<tr><td>{i:02d}_{html.escape(name)}</td>"
                       f"<td>{len(items)} వాక్యాలు</td><td>{mins(wall)}</td></tr>")
    guide_html += (f"<tr><th>మొత్తం</th><th>{len(rows)} వాక్యాలు</th>"
                   f"<th>{total_wall/60:.0f} నిమిషాలు</th></tr></table>")
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
  .guide {{ background: rgba(180,83,9,.07); border-radius: 14px; padding: .2rem 1.2rem 1rem;
            margin: 1.5rem 0 2rem; font-size: 1.15rem; line-height: 1.9; }}
  .guide h3 {{ font-size: 1.1rem; margin: 1.4rem 0 .4rem; color: #b45309; }}
  @media (prefers-color-scheme: dark) {{ .guide h3 {{ color: #f0a860; }} }}
  .guide ul {{ margin: 0; padding-left: 1.2rem; }}
  .guide li {{ display: list-item; border: 0; padding: .2rem 0; }}
  .guide table {{ width: 100%; border-collapse: collapse; font-size: .95rem; margin-top: .5rem; }}
  .guide td, .guide th {{ text-align: left; padding: .3rem .4rem;
                          border-bottom: 1px solid rgba(128,128,128,.2); }}
  @media print {{ body {{ background: #fff; color: #000; font-size: 12pt; }}
                  h2 {{ page-break-after: avoid; }} li {{ page-break-inside: avoid; }} }}
</style></head><body>
<h1>LAMPOSE — రికార్డింగ్</h1>
<p class="note">మొత్తం {len(rows)} వాక్యాలు. కింద ముందుగా చిన్న గైడ్ ఉంది —
ఒకసారి చదవండి. తర్వాత వాక్యాలు మొదలవుతాయి.</p>
<div class="guide">{guide_html}</div>"""]
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


def write_print_html(sections, rows):
    """A booklet: English instructions, then every sentence. For printing/PDF."""
    guide = "".join(
        f"<h3>{html.escape(t)}</h3><ul>"
        + "".join(f"<li>{html.escape(p)}</li>" for p in pts) + "</ul>"
        for t, pts in GUIDE_EN)
    _, total_wall = timing(rows)
    rowsx = "".join(
        f"<tr><td>{i:02d}_{html.escape(n)}</td><td>{len(it)}</td>"
        f"<td>{timing(it)[1]/60:.0f} min</td></tr>"
        for i, (n, it) in enumerate(sections.items(), 1))

    body = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>LAMPOSE — Telugu voice recording</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Telugu:wght@400;600&display=swap"
      rel="stylesheet">
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font-family: {FONT}; color: #000; background: #fff;
          font-size: 11.5pt; line-height: 1.65; margin: 0; }}
  h1 {{ font-size: 20pt; margin: 0 0 2pt; }}
  .sub {{ font-size: 10.5pt; color: #444; margin-bottom: 14pt; }}
  h2 {{ font-size: 13pt; margin: 16pt 0 6pt; padding-top: 6pt;
        border-top: 1.5pt solid #000; page-break-after: avoid; }}
  h3 {{ font-size: 11.5pt; margin: 11pt 0 3pt; page-break-after: avoid; }}
  ul {{ margin: 0 0 0 0; padding-left: 16pt; }}
  li {{ margin: 2pt 0; page-break-inside: avoid; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 10pt; margin-top: 6pt; }}
  td, th {{ text-align: left; padding: 2.5pt 4pt; border-bottom: .5pt solid #bbb; }}
  .sent {{ page-break-inside: avoid; display: flex; gap: 10pt; align-items: baseline;
           padding: 3.5pt 0; border-bottom: .4pt solid #e2e2e2; font-size: 13pt;
           line-height: 1.9; }}
  .id {{ font-family: Menlo, monospace; font-size: 8.5pt; color: #777; min-width: 34pt; }}
  .howto {{ border: 1pt solid #000; padding: 6pt 10pt; margin: 8pt 0 12pt;
            font-size: 10.5pt; background: #f4f4f4; page-break-inside: avoid; }}
  .break {{ page-break-before: always; }}
</style></head><body>
<h1>Recording a Telugu voice for LAMPOSE</h1>
<div class="sub">{len(rows)} sentences · about {total_wall/60:.0f} minutes of recording,
split over three sittings · please read pages 1–2 before you start</div>
{guide}
<h3>10. How long each section takes</h3>
<table><tr><th>Section (use this as the file name)</th><th>Sentences</th><th>Time</th></tr>
{rowsx}</table>"""]

    for i, (name, items) in enumerate(sections.items(), 1):
        _, wall = timing(items)
        body.append(f'<div class="break"></div><h2>{i:02d}_{name} — '
                    f'{html.escape(TITLES.get(name, name))}</h2>')
        body.append(f'<div class="howto"><b>Save this recording as: '
                    f'{i:02d}_{name}</b><br>{len(items)} sentences, about '
                    f'{wall/60:.0f} minute(s). Press record → wait 2 seconds → read a '
                    f'sentence → wait 3 seconds → read the next one. Do not stop until '
                    f'the section is finished.</div>')
        for r in items:
            body.append(f'<div class="sent"><span class="id">{r["id"]}</span>'
                        f'<span>{html.escape(r["text"])}</span></div>')
    body.append("</body></html>")
    path = BASE / "recording_booklet_en.html"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]


def write_pdf(booklet):
    """Print the booklet to PDF with headless Chrome.

    Chrome is used because it shapes Telugu correctly (HarfBuzz) and embeds the
    font, so the PDF looks the same on a phone that has no Telugu font at all.
    Most Python PDF libraries do not do Indic shaping and would silently produce
    broken conjuncts.
    """
    import subprocess
    chrome = next((c for c in CHROME_PATHS if Path(c).exists()), None)
    if not chrome:
        print("  (no Chrome found — open recording_booklet_en.html and use "
              "File → Print → Save as PDF)")
        return None
    pdf = BASE / "LAMPOSE_recording_guide.pdf"
    subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=10000",   # let the web font load
                    f"--print-to-pdf={pdf}", f"file://{booklet}"],
                   capture_output=True, check=True)
    return pdf


if __name__ == "__main__":
    rows = load()
    sections = by_section(rows)
    sect_dir, sess_dir = write_text_files(sections)
    page = write_html(sections, rows)
    guide = write_guide_text(sections)
    booklet = write_print_html(sections, rows)
    print(f"{len(rows)} sentences in {len(sections)} sections\n")
    print(f"  {len(list(sect_dir.glob('*.txt')))} section files -> {sect_dir.relative_to(ROOT)}/")
    for f in sorted(sess_dir.glob("*.txt")):
        n = sum(1 for line in f.read_text(encoding='utf-8').splitlines()
                if line[:1].isalpha() and line[1:4].isdigit())
        print(f"  {f.relative_to(ROOT)}  ({n} sentences)")
    print(f"  {guide.relative_to(ROOT)}")
    print(f"  {page.relative_to(ROOT)}  <- web page")
    print(f"  {booklet.relative_to(ROOT)}")
    pdf = write_pdf(booklet)
    if pdf:
        print(f"  {pdf.relative_to(ROOT)}  <- print this and give it to her")
