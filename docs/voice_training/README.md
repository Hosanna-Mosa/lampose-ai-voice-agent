# Recording a Telugu voice for LAMPOSE

Everything needed to record one speaker and train a voice model from it.

| File | What it is |
|---|---|
| `script_te.tsv` | The 475 sentences, with IDs. The single source of truth — everything else is generated from it by `scripts/build_voice_script.py`. |
| `LAMPOSE_recording_guide.pdf` | **Give her this.** 43-page A4 booklet: English instructions, then every sentence in Telugu. Print it or send the file — no software needed. |
| `script_te.html` | The same thing as a web page, with the guide in Telugu. |
| `speaker_guide_te.txt` | The same guide as plain text, for WhatsApp. |
| `sections/`, `sessions/` | The sentences as plain text, per section and per sitting. |
| `../../scripts/split_session_recording.py` | Cuts one section recording into one file per sentence. |
| `../../scripts/check_voice_corpus.py` | Proves the script covers Telugu — run it if you edit the script. |
| `../../scripts/check_recordings.py` | Checks the recordings before they go anywhere near training. |

---

## 0. Before anything: consent

You are making a copy of a real person's voice. Get her **written permission**
first, and be specific about it: that the voice will be synthesized, used for
outbound sales calls to strangers on behalf of LAMPOSE, for how long, and what
happens if she leaves. Pay her for the session. Keep the signed note with the
recordings.

This is not paperwork for its own sake — a cloned voice making sales calls
without the owner's informed agreement is the kind of thing that ends badly for
everyone, and it is trivially avoidable.

---

## 1. Who should read it

She is playing **Kavya** — a polite, warm young woman from a LAMPOSE office
calling a property owner she has never met. Not a newsreader, not an actor, not
an announcer. The model copies whatever style it hears, so the style is the
product.

Direction to give her, in her words:

- Talk as if a real person is on the other end of the phone, listening.
- Polite and warm, never sing-song, never over-excited.
- Native Telugu speaker, comfortable mixing everyday English words the way
  people actually do — *property*, *booking*, *WhatsApp* stay English.
- Same energy on sentence 1 and sentence 475. **Consistency beats brilliance.**
  A model trained on a voice that drifts sounds unstable.

What to avoid: dramatic emphasis, whispering, shouting, smiling too hard
(it changes the tone), and speeding up when tired.

---

## 2. Equipment

**Good enough** — a USB condenser mic (Blue Yeti, Maono, Fifine, ~₹5,000) on a
stand with a pop filter, plugged into a laptop.

**Acceptable** — a modern phone's voice recorder set to **WAV / lossless**, held
on a stand about a hand's width away, slightly off to the side of the mouth.
A phone in a quiet room beats a good mic in a noisy one.

**Not acceptable** — laptop built-in mics, Bluetooth earbuds, anything that
records to MP3 or AAC. Compression artefacts get baked into the model and
cannot be removed later.

### Room

- Small, soft room. A bedroom with a bed, curtains and a wardrobe is ideal.
  Bathrooms and empty halls are the worst — the echo is unremovable.
- **Fan off. AC off. Fridge away. Phone on aeroplane mode.** A fan hum you
  stop noticing after five minutes will be in every sentence the model speaks.
- Record a **10-second silence** at the start of every session with everything
  running. That is the room's noise floor and the QC script uses it.

### Settings

| Setting | Value |
|---|---|
| Format | WAV, uncompressed |
| Sample rate | 48 kHz (44.1 kHz fine; never below 24 kHz) |
| Bit depth | 24-bit, or 16-bit |
| Channels | Mono |
| Level | Peaks around −6 dBFS. **Never let it touch 0.** |

Recording at 48 kHz even though phone calls are 8 kHz is deliberate: the model
trains on the clean version, and downsampling later is free. The reverse is not.

---

## 3. How to record

**One recording per section**, not per sentence. Creating, stopping, saving and
naming 475 files is how a session with an inexperienced speaker falls apart —
she presses record once, reads the whole section with a clear gap between
sentences, and stops. `scripts/split_session_recording.py` cuts it up afterwards
and names each piece by ID.

Her instructions are in `speaker_guide_te.txt` and at the top of
`script_te.html`, in Telugu. The rhythm:

1. Press record, **2 seconds of silence**.
2. Read one sentence.
3. **3 seconds of silence** — count *one, two, three* — do **not** stop recording.
4. Next sentence, and so on to the end of the section.
5. 3 seconds of silence, then stop.

That 3-second gap is what the splitter cuts on, so it matters more than it looks.

**If she stumbles:** stop, say nothing (not even "sorry"), wait 3 seconds, and
read the same sentence again from the beginning. The splitter will report one
segment too many and show where — pass it `--skip N` to drop the bad take.

**Read what is written.** If a word feels unnatural to her, do not improvise —
note the ID and we will fix the script and re-record that line. The text must
match the audio exactly or it teaches the model the wrong thing.

Name each section recording after the section: `01_vowels.wav`, `02_consonants.wav`…
Then:

```bash
./venv/bin/python scripts/split_session_recording.py 01_vowels.wav --section vowels
# check the report, then:
./venv/bin/python scripts/split_session_recording.py 01_vowels.wav --section vowels --write
```

### Session plan

475 sentences is about **29 minutes of speech**, which is roughly **70–90
minutes** of real time. Split across **3 sessions of about 45 minutes**:

| Session | What it covers | Sentences | File to read from |
|---|---|---|---|
| 1 | The sounds of Telugu | 135 | `sessions/session1.txt` |
| 2 | Numbers, English words, everyday speech | 150 | `sessions/session2.txt` |
| 3 | The call itself, and tone of voice | 190 | `sessions/session3.txt` |

**Do not read from the TSV, and do not read from a terminal** — Telugu conjuncts
and vowel signs do not render correctly there. Open `script_te.html` in a
browser (phone or laptop, correct shaping, large type, works offline), or the
plain `sessions/*.txt` files in any editor. Regenerate them after editing the
script with `./venv/bin/python scripts/build_voice_script.py`.

Water at room temperature, breaks every 15 minutes, and **the same room, same
mic, same distance every session**. If session 2 is in a different room than
session 1, the model learns two voices.

---

## 4. Why the script is shaped this way

It is not random sentences. Sections, and what each is for:

| Section | Purpose |
|---|---|
| `vowels`, `consonants` | Every Telugu vowel and consonant, several times each |
| `rare` | The letters that almost never occur naturally — ఔ, ౌ, ఋ, ృ, ః, ఛ, ఝ, ఞ, ఠ, ఢ, ఙ. Without these the model guesses when it meets them |
| `clusters`, `gemination`, `minimal` | Conjunct consonants, doubled consonants, and near-identical word pairs |
| `numbers` | Ratings, times, money, days — spelled in words, never digits |
| `tenglish`, `tenglish2` | Every English word the agent actually says on calls |
| `call_open` … `call_more` | The real script: greeting, pitch, qualifying, objections, closing |
| `question`, `exclaim`, `empathy`, `apology`, `filler`, `warm`, `calm`, `energetic`, `prosody` | The tunes — rising questions, apologies, hesitation sounds. A model that never heard a question never learns the rise |
| `natural`, `dense` | Ordinary speech, so the voice generalises past our script |

To verify after any edit:

```bash
./venv/bin/python scripts/check_voice_corpus.py
```

It fails if a sound is missing or appears fewer than five times.

---

## 5. Delivering the recordings

```
voice_data/
  wavs/
    A001.wav
    A002.wav
    …
  room_tone.wav        ← the 10 seconds of silence
```

Then check them **before** training:

```bash
./venv/bin/python scripts/check_recordings.py voice_data/wavs
```

It reports clipping, wrong sample rates, background noise, files that are
suspiciously short or long for their sentence, missing IDs — and writes
`metadata.csv` in the `id|text` format most training tools expect.

Fix what it flags and re-run. A dataset that passes this is worth training on;
one that does not will waste the five days.

---

## 6. How much is enough

| Audio | What it gets you |
|---|---|
| 3–10 min | Few-shot cloning (XTTS, F5, ElevenLabs instant) — recognisable, not stable |
| **25–45 min** | **Fine-tuning — this script. The sweet spot for a production voice** |
| 2+ hours | Marginal gains, mostly in rare contexts |

More audio does not fix inconsistent audio. 29 clean, consistent minutes beat
two sloppy hours, which is why §3 is strict about the room and the mic.

---

## 7. After training

The voice plugs in where Sarvam does today — see §8 of `../PROJECT_CONTEXT.md`.
Three places speak: `SarvamTTSFlushShort` in `app/bot.py` (the live stream),
`app/filler.py` (greeting and filler clips) and `app/voices.py` (samples).
Anything that streams 8 kHz mono PCM can take its place.

Judge the result the way we judged everything else in this project: with the
round-trip test (speak a line, transcribe it back, compare) plus your own ears
on the **Voices** tab at *Phone 8 kHz* — not on a laptop at 24 kHz, which
flatters every voice.
