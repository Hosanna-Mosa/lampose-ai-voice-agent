"""Central configuration, loaded from .env."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(override=True)
# Optional local overrides for laptop testing (tunnel URL etc.) — wins over .env
load_dotenv(".env.local", override=True)


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Twilio
TWILIO_ACCOUNT_SID = _get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = _get("TWILIO_NUMBER")

# Sarvam
SARVAM_API_KEY = _get("SARVAM_API_KEY")
STT_MODEL_NAME = _get("SARVAM_STT_MODEL", "saaras:v3")
STT_LANGUAGE = _get("STT_LANGUAGE", "te-IN")
STT_MODE = _get("STT_MODE")  # "", "codemix", "transcribe", ...
TTS_VOICE = _get("TTS_VOICE", "kavya")
TTS_MODEL = _get("TTS_MODEL", "bulbul:v3")
TTS_PACE = float(_get("TTS_PACE", "1.15"))
OUTPUT_GAIN_DB = float(_get("OUTPUT_GAIN_DB", "-5"))  # bot audio level; was peaking at -0.6 dBFS

# Anthropic
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-haiku-4-5")

# Server
SERVER_URL = _get("SERVER_URL", "https://voice.lampose.in").rstrip("/")
PORT = int(_get("PORT", "7860"))

# Business
SALES_TRANSFER_NUMBER = _get("SALES_TRANSFER_NUMBER", "+919398334115")
AGENT_NAME = _get("AGENT_NAME", "Kavya")
COMPANY_NAME = _get("COMPANY_NAME", "LAMPOSE")

# Dialer
DIALER_ENABLED_DEFAULT = _get("DIALER_ENABLED", "false").lower() == "true"
MAX_CONCURRENT_CALLS = int(_get("MAX_CONCURRENT_CALLS", "3"))
CALLING_HOURS_START = int(_get("CALLING_HOURS_START", "10"))
CALLING_HOURS_END = int(_get("CALLING_HOURS_END", "19"))
MAX_ATTEMPTS = int(_get("MAX_ATTEMPTS", "3"))
RETRY_DELAY_HOURS = float(_get("RETRY_DELAY_HOURS", "4"))

# Mongo
MONGO_URL = _get("MONGO_URL", "mongodb://127.0.0.1:27017")
MONGO_DB = _get("MONGO_DB", "lampose_voice")

# Dashboard auth
DASHBOARD_USER = _get("DASHBOARD_USER", "lampose")
DASHBOARD_PASSWORD = _get("DASHBOARD_PASSWORD", "")

TZ = ZoneInfo(_get("TIMEZONE", "Asia/Kolkata"))

# Security
TWILIO_VALIDATE = _get("TWILIO_VALIDATE", "true").lower() == "true"
MAX_ACTIVE_PIPELINES = int(_get("MAX_ACTIVE_PIPELINES", "6"))

# Vapi-parity features
FILLER_ENABLED = _get("FILLER_ENABLED", "true").lower() == "true"
FILLER_DELAY_SECS = float(_get("FILLER_DELAY_SECS", "1.3"))
FILLER_BY_CONTEXT = {
    "tool": "సరే సర్, ఒకసారి చెక్ చేస్తున్నాను...",
    "question": "ఒక్క క్షణం సర్, చెప్తాను...",
    "statement": "అలాగే సర్, ఒక్క క్షణం...",
}
NOISE_FILTER = _get("NOISE_FILTER", "false").lower() == "true"  # RNNoise; ~0.6x CPU/call
LLM_RETRY_TIMEOUT_SECS = float(_get("LLM_RETRY_TIMEOUT_SECS", "3.5"))

# Backchanneling: short acknowledgments while the owner speaks at length
BACKCHANNEL_ENABLED = _get("BACKCHANNEL_ENABLED", "true").lower() == "true"
BACKCHANNEL_AFTER_SECS = float(_get("BACKCHANNEL_AFTER_SECS", "4.0"))
BACKCHANNEL_MAX_PER_TURN = int(_get("BACKCHANNEL_MAX_PER_TURN", "2"))
BACKCHANNEL_PHRASES = ["హా...", "ఊ...", "అలాగే...", "ఆహా..."]

# Call recording: stereo WAV per call (user=left, agent=right) in recordings/
RECORD_CALLS = _get("RECORD_CALLS", "true").lower() == "true"

# Emotion v1: realtime voice-energy analysis -> silent note to the LLM
EMOTION_ENABLED = _get("EMOTION_ENABLED", "true").lower() == "true"
EMOTION_LOUD_RMS = int(_get("EMOTION_LOUD_RMS", "4000"))

# Ambient background sound: a faint room bed under the agent so the line does
# not sound like dead digital silence. Beds are synthesized in app/ambient.py.
# Off by default — audition it on a test call before enabling for everyone.
AMBIENT_ENABLED = _get("AMBIENT_ENABLED", "false").lower() == "true"
AMBIENT_SOUND = _get("AMBIENT_SOUND", "office")     # quiet|office|call_center|cafe|street
AMBIENT_VOLUME = float(_get("AMBIENT_VOLUME", "0.08"))  # 0.08 ≈ 25 dB under speech
# Hard ceiling. At 1.0 the bed is as loud as Kavya (call ACVPS12): her words came
# back garbled, and the owner's phone echoed the bed into our STT so their
# replies transcribed as nonsense. Nothing above this is ever worth shipping.
AMBIENT_MAX_VOLUME = float(_get("AMBIENT_MAX_VOLUME", "0.25"))

# Sarvam bulbul:v3 voices offered in the dashboard test-call picker
TEST_VOICES = ["kavya", "shreya", "pooja", "ritu", "shubh", "gokul", "aditya", "rohan"]
