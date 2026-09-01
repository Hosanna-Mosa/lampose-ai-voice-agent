"""Text as it is spoken, and the voice knobs behind it.

Covers the last-mile clean-up (digits, half-transliterated tokens) and the
expressiveness presets — including the bound the API actually enforces, which
is not the one the documentation states.

Run: PYTHONPATH=. ./venv/bin/python tests/test_voice_text.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import voices                       # noqa: E402
from app.telugu_text import for_tts, number_in_words   # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


print("\nSpoken text clean-up")
out, fixes = for_tts("నాలుగు పాయింట్ ఎight రేటింగ్ ఉంది")
check("the real ACVPS bug: 'ఎight' becomes a word she can say",
      "ఎనిమిది" in out and "ight" not in out, out)
out, _ = for_tts("మీ property కి 4.8 rating ఉంది సర్.")
check("a rating is spoken, not spelled in digits",
      "నాలుగు పాయింట్ ఎనిమిది" in out, out)
out, _ = for_tts("రేపు 10 గంటలకు call చేస్తాను.")
check("a callback time is spoken", "పది గంటలకు" in out, out)

untouched = "మీ property ని free గా onboard చేద్దామని call చేశాను."
out, fixes = for_tts(untouched)
check("ordinary English words are left alone (round-trip proved they are fine)",
      out == untouched and not fixes, out)
out, _ = for_tts("2026 లో launch అయ్యింది.")
check("long numbers are left alone", "2026" in out, out)

check("numbers read correctly across the range",
      (number_in_words(0), number_in_words(8), number_in_words(12),
       number_in_words(20), number_in_words(45), number_in_words(100))
      == ("సున్నా", "ఎనిమిది", "పన్నెండు", "ఇరవై", "నలభై ఐదు", "వంద"))

print("\nExpressiveness presets")
temps = [t for _, t, _ in voices.EXPRESSIVENESS]
check("ten modes, flat through excited", len(voices.EXPRESSIVENESS) == 10,
      ", ".join(n for n, _, _ in voices.EXPRESSIVENESS))
check("every mode is inside the range the API really accepts (0.01-1.0)",
      all(0.01 <= t <= 1.0 for t in temps), f"{min(temps)}-{max(temps)}")
check("they increase in liveliness", temps == sorted(temps))
check("a name maps to its dial", voices.mode_temperature("warm") == 0.45)
check("an unknown name is refused rather than guessed",
      voices.mode_temperature("cheerful") is None)
check("no mode means no override", voices.mode_temperature(None) is None)

print(f"\n{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
