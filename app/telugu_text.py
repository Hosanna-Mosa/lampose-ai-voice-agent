"""Last-mile clean-up of the text just before it is spoken.

Two things reach the TTS badly formed no matter how the prompt is worded:

* **Digits.** "4.8 rating" is read as digits rather than the way a person says
  it. Ratings are in every opening line, so this one is constant.
* **Half-transliterated words.** Claude occasionally emits a token that is part
  Telugu and part Latin — a real call went out saying "నాలుగు పాయింట్ ఎight",
  which no TTS can pronounce. The prompt cannot reliably prevent this; a
  deterministic pass here can.

Round-trip testing (speak it, transcribe it back) showed Sarvam already
pronounces ordinary English words correctly inside Telugu — property, hostel,
To-Let, WhatsApp all came back clean — so this deliberately does NOT rewrite
English into Telugu script. Only the two failure modes above are touched.
"""

from __future__ import annotations

import re

ONES = ["సున్నా", "ఒకటి", "రెండు", "మూడు", "నాలుగు", "ఐదు", "ఆరు", "ఏడు",
        "ఎనిమిది", "తొమ్మిది", "పది", "పదకొండు", "పన్నెండు", "పదమూడు", "పద్నాలుగు",
        "పదిహేను", "పదహారు", "పదిహేడు", "పద్దెనిమిది", "పంతొమ్మిది", "ఇరవై"]
TENS = {30: "ముప్పై", 40: "నలభై", 50: "యాభై", 60: "అరవై",
        70: "డెబ్బై", 80: "ఎనభై", 90: "తొంభై", 100: "వంద"}

# What Claude was reaching for when it produced a half-Latin token.
ENGLISH_NUMBERS = {
    "zero": "సున్నా", "one": "ఒకటి", "two": "రెండు", "three": "మూడు", "four": "నాలుగు",
    "five": "ఐదు", "six": "ఆరు", "seven": "ఏడు", "eight": "ఎనిమిది", "nine": "తొమ్మిది",
    "ten": "పది",
}

_TELUGU = r"ఀ-౿"
_MIXED = re.compile(rf"\b(?=[^\s]*[{_TELUGU}])(?=[^\s]*[A-Za-z])[{_TELUGU}A-Za-z]+\b")
_DECIMAL = re.compile(r"\b(\d{1,2})[.,](\d)\b")
_INTEGER = re.compile(r"\b(\d{1,3})\b")


def number_in_words(n: int) -> str:
    """Telugu for 0-100. Outside that range digits read fine as they are."""
    if 0 <= n <= 20:
        return ONES[n]
    if n in TENS:
        return TENS[n]
    if 20 < n < 100:
        tens, ones = (n // 10) * 10, n % 10
        base = TENS.get(tens, "")
        return f"{base} {ONES[ones]}".strip() if base else str(n)
    return str(n)


def _fix_mixed_token(token: str) -> str:
    """Repair a token that mixes scripts, e.g. "ఎight" -> "ఎనిమిది".

    The Latin remnant is usually the tail of an English number word, because
    the model started writing the word in Telugu and finished it in English.
    If nothing matches, the token is left exactly as it was — never make it
    worse than what the model produced.
    """
    latin = "".join(re.findall(r"[A-Za-z]+", token)).lower()
    if not latin:
        return token
    for word, telugu in ENGLISH_NUMBERS.items():
        if word.endswith(latin) or latin.endswith(word) or latin == word:
            return telugu
    return token


def for_tts(text: str) -> tuple[str, list[str]]:
    """Text as it should be spoken, plus a note of anything repaired."""
    fixes: list[str] = []

    def repair(m):
        before = m.group(0)
        after = _fix_mixed_token(before)
        if after != before:
            fixes.append(f"{before} -> {after}")
        return after

    out = _MIXED.sub(repair, text)

    def decimal(m):
        whole, frac = int(m.group(1)), int(m.group(2))
        spoken = f"{number_in_words(whole)} పాయింట్ {number_in_words(frac)}"
        fixes.append(f"{m.group(0)} -> {spoken}")
        return spoken

    out = _DECIMAL.sub(decimal, out)

    def integer(m):
        n = int(m.group(1))
        if n > 100:                     # years, counts, anything long: leave alone
            return m.group(0)
        spoken = number_in_words(n)
        fixes.append(f"{m.group(0)} -> {spoken}")
        return spoken

    out = _INTEGER.sub(integer, out)
    return out, fixes
