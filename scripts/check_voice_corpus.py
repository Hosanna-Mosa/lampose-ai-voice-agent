"""Does the recording script actually cover Telugu?

A voice model only learns sounds it hears. A script that "feels complete" is
not evidence — this counts every vowel, consonant, vowel sign and cluster in
docs/voice_training/script_te.tsv and reports what is missing or thin, so gaps
are fixed before the speaker sits down rather than after.

    ./venv/bin/python scripts/check_voice_corpus.py
    ./venv/bin/python scripts/check_voice_corpus.py --min 8   # stricter
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docs" / "voice_training" / "script_te.tsv"

VOWELS = "అ ఆ ఇ ఈ ఉ ఊ ఋ ఎ ఏ ఐ ఒ ఓ ఔ".split()
SIGNS = {"ా": "aa", "ి": "i", "ీ": "ii", "ు": "u", "ూ": "uu", "ృ": "r̥",
         "ె": "e", "ే": "ee", "ై": "ai", "ొ": "o", "ో": "oo", "ౌ": "au",
         "ం": "anusvara", "ః": "visarga"}
CONSONANTS = ("క ఖ గ ఘ ఙ చ ఛ జ ఝ ఞ ట ఠ డ ఢ ణ త థ ద ధ న "
              "ప ఫ బ భ మ య ర ల వ శ ష స హ ళ").split()
VIRAMA = "్"

# The English words the agent actually says on calls (from app/prompts.py).
LOANWORDS = """property booking online platform free photos rooms beds PG hostel
hotel To-Let website accept reject WhatsApp Google Maps customers brokers
walk-ins onboard onboarding details number team time interest rating reviews
launch LAMPOSE request students professionals families commission simple guide
live call message add confirm""".split()

# Roughly how long a Telugu sentence takes to say, from measured Sarvam output:
# ~11 characters per second at a natural pace.
CHARS_PER_SECOND = 11.0


def load():
    with open(SCRIPT, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def report(rows, minimum):
    text = " ".join(r["text"] for r in rows)
    chars = Counter(text)
    ok = True

    print(f"\n{len(rows)} sentences, {len(text):,} characters")
    by_section = Counter(r["section"] for r in rows)
    for section, n in by_section.most_common():
        print(f"    {section:12s} {n:4d}")
    mins = len(text) / CHARS_PER_SECOND / 60
    print(f"\n  Estimated recording time: {mins:.0f} min of speech "
          f"(~{mins * 2.5:.0f} min in the booth with retakes)")

    def block(title, items, label=lambda c: c):
        nonlocal ok
        missing = [c for c in items if chars[c] == 0]
        thin = [(c, chars[c]) for c in items if 0 < chars[c] < minimum]
        print(f"\n  {title}: {len(items) - len(missing)}/{len(items)} present")
        if missing:
            ok = False
            print(f"    MISSING  {' '.join(label(c) for c in missing)}")
        if thin:
            ok = False
            print(f"    thin (<{minimum})  " +
                  "  ".join(f"{label(c)}×{n}" for c, n in thin))
        if not missing and not thin:
            rarest = min(items, key=lambda c: chars[c])
            print(f"    all at least {minimum}× (rarest: {label(rarest)}×{chars[rarest]})")

    block("Independent vowels", VOWELS)
    block("Vowel signs", list(SIGNS), label=lambda c: f"{c}({SIGNS[c]})")
    block("Consonants", CONSONANTS)

    clusters = Counter(re.findall(rf"([క-హళ]{VIRAMA}[క-హళ])", text))
    print(f"\n  Consonant clusters: {len(clusters)} distinct, "
          f"{sum(clusters.values())} occurrences")
    print("    most common: " + "  ".join(f"{c}×{n}" for c, n in clusters.most_common(8)))

    lower = text.lower()
    missing_words = [w for w in LOANWORDS if w.lower() not in lower]
    print(f"\n  English words the agent says: "
          f"{len(LOANWORDS) - len(missing_words)}/{len(LOANWORDS)} present")
    if missing_words:
        ok = False
        print(f"    MISSING  {' '.join(missing_words)}")

    digits = re.findall(r"\d", text)
    print(f"\n  Digits in the script: {len(digits)} "
          f"({'spell them in words — the model should never see bare digits' if digits else 'none — good'})")

    dupes = [t for t, n in Counter(r["text"] for r in rows).items() if n > 1]
    if dupes:
        ok = False
        print(f"\n  DUPLICATE sentences: {len(dupes)}")
        for d in dupes[:5]:
            print(f"    {d}")

    long_ones = [r for r in rows if len(r["text"]) > 120]
    if long_ones:
        print(f"\n  {len(long_ones)} sentences over 120 characters "
              f"(hard to say in one breath — consider splitting)")
        for r in long_ones[:3]:
            print(f"    {r['id']}  {r['text'][:70]}…")

    print()
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=5,
                    help="minimum occurrences before a sound counts as covered")
    args = ap.parse_args()
    sys.exit(0 if report(load(), args.min) else 1)
