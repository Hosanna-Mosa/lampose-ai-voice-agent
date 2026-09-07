"""Check that every clip really contains the sentence it is named after.

The splitter can produce the right *number* of pieces and still have them one
sentence out of step. Training on mislabelled audio is the most expensive
mistake available here, so before anything goes near a model every clip is
transcribed and compared with the line it claims to be.

Scoring is by Telugu word recall, not string similarity: the transcriber writes
"property" as "ప్రాపర్టీ", so a plain comparison marks correct code-mixed clips
as wrong. We ask a fairer question — do the Telugu words of the script appear in
what was heard?

    ./venv/bin/python scripts/verify_clips.py                 # report
    ./venv/bin/python scripts/verify_clips.py --quarantine    # move the bad ones

Transcripts are cached, so re-runs cost nothing.
"""

import argparse
import asyncio
import csv
import difflib
import json
import os
import re
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "docs" / "voice_training" / "script_te.tsv"
CACHE = ROOT / "voice_data" / ".transcripts.json"

TELUGU_WORD = re.compile(r"[ఀ-౿]+")


def telugu_words(text):
    return [w for w in TELUGU_WORD.findall(text) if len(w) > 1]


def recall(want, heard):
    """Fraction of the script's Telugu words that turn up in the transcript."""
    want_w, heard_w = telugu_words(want), telugu_words(heard)
    if not want_w:
        return 1.0 if heard_w else 0.0          # an all-English line
    hits = 0
    for w in want_w:
        if any(difflib.SequenceMatcher(None, w, h).ratio() >= 0.75 for h in heard_w):
            hits += 1
    return hits / len(want_w)


async def transcribe(session, sem, key, path):
    async with sem:
        form = aiohttp.FormData()
        form.add_field("file", path.read_bytes(), filename="c.wav",
                       content_type="audio/wav")
        form.add_field("model", "saarika:v2.5")
        form.add_field("language_code", "te-IN")
        for attempt in range(3):
            async with session.post("https://api.sarvam.ai/speech-to-text",
                                    headers={"api-subscription-key": key}, data=form,
                                    timeout=aiohttp.ClientTimeout(total=90)) as r:
                if r.status == 200:
                    return (await r.json()).get("transcript", "")
                if r.status in (429, 500, 502, 503):
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                return f"[HTTP {r.status}]"
    return "[failed]"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wavs", default="voice_data/wavs")
    ap.add_argument("--min", type=float, default=0.7,
                    help="minimum word recall to accept a clip")
    ap.add_argument("--quarantine", action="store_true",
                    help="move failing clips to voice_data/suspect/")
    args = ap.parse_args()

    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        sys.exit("SARVAM_API_KEY is not set. Run:  set -a; . ./.env; set +a")

    rows = {r["id"]: r for r in csv.DictReader(open(TSV, encoding="utf-8"),
                                               delimiter="\t")}
    wavs = sorted(Path(args.wavs).glob("*.wav"))
    if not wavs:
        sys.exit(f"No clips in {args.wavs}. Run scripts/split_all.py --write first.")

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [p for p in wavs if p.stem not in cache]
    print(f"\n{len(wavs)} clips — {len(cache)} already transcribed, {len(todo)} to do")

    if todo:
        sem = asyncio.Semaphore(4)
        async with aiohttp.ClientSession() as s:
            done = 0
            for chunk in [todo[i:i + 40] for i in range(0, len(todo), 40)]:
                out = await asyncio.gather(*[transcribe(s, sem, key, p) for p in chunk])
                # Never cache a failure — otherwise a rate-limit blip becomes a
                # permanent "this clip is bad" verdict on a re-run.
                cache.update({p.stem: t for p, t in zip(chunk, out)
                              if t and not t.startswith("[")})
                done += len(chunk)
                print(f"  transcribed {done}/{len(todo)}")
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0))

    scored, by_section = [], {}
    for p in wavs:
        cid = p.stem
        if cid not in rows:
            continue
        score = recall(rows[cid]["text"], cache.get(cid, ""))
        scored.append((score, cid, rows[cid]["section"]))
        by_section.setdefault(rows[cid]["section"], []).append(score)

    scored.sort()
    bad = [s for s in scored if s[0] < args.min]

    print(f"\n  {'section':14s} {'clips':>5s} {'avg':>6s}  worst")
    for sec, vals in sorted(by_section.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        print(f"  {sec:14s} {len(vals):5d} {sum(vals)/len(vals):6.0%}  {min(vals):.0%}")

    print(f"\n  {len(scored) - len(bad)}/{len(scored)} clips verified "
          f"(word recall >= {args.min:.0%})")
    if bad:
        print(f"\n  {len(bad)} need a look — worst first:")
        for score, cid, sec in bad[:15]:
            print(f"    {cid} {sec:12s} {score:4.0%}")
            print(f"      want : {rows[cid]['text'][:66]}")
            print(f"      heard: {cache.get(cid,'')[:66]}")
        if args.quarantine:
            sus = Path("voice_data/suspect")
            sus.mkdir(parents=True, exist_ok=True)
            for _, cid, _ in bad:
                Path(args.wavs, f"{cid}.wav").rename(sus / f"{cid}.wav")
            print(f"\n  moved {len(bad)} clips to {sus} — they are out of the "
                  f"training set until fixed")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
