"""Step-based logging: readable [STEP xx-NAME] lines on the console,
full DEBUG detail (pipecat internals included) in logs/agent.log."""

import sys
from pathlib import Path

from loguru import logger

_configured = False


def setup_logging():
    global _configured
    if _configured:
        return
    _configured = True
    logger.remove()
    console_fmt = (
        "<green>{time:HH:mm:ss.SSS}</green> <level>{level:<7}</level> {message}"
    )
    logger.add(sys.stderr, level="INFO", format=console_fmt, colorize=True)
    Path("logs").mkdir(exist_ok=True)
    logger.add(
        "logs/agent.log",
        level="DEBUG",
        rotation="20 MB",
        retention=10,
        enqueue=True,
        backtrace=False,
    )
    logger.info("Logging ready — console: INFO, file: logs/agent.log (DEBUG)")


def step(name: str, msg: str = ""):
    """One milestone line the user can follow, e.g. [STEP 14-STT-READY] ..."""
    logger.info(f"[STEP {name}] {msg}")
