"""
Structured logging for HKRE scraping.
Enable with SCRAPER_DEBUG=1 or SCRAPER_LOG_LEVEL=DEBUG; optional file via SCRAPER_LOG_FILE.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_configured = False

_LOGGER_ROOT = "hkre"


def configure_scraper_logging(base_dir: Path | None = None) -> None:
    """
    Configure root logger 'hkre' once: stderr always; file if SCRAPER_DEBUG=1 or SCRAPER_LOG_FILE set.
    """
    global _configured
    if _configured:
        return

    debug_env = os.getenv("SCRAPER_DEBUG", "").lower() in ("1", "true", "yes")
    level_name = os.getenv("SCRAPER_LOG_LEVEL", "DEBUG" if debug_env else "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger(_LOGGER_ROOT)
    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    log_file = os.getenv("SCRAPER_LOG_FILE", "").strip()
    if debug_env or log_file:
        if not log_file:
            base = Path(base_dir) if base_dir else Path.cwd()
            debug_d = base / "debug"
            debug_d.mkdir(parents=True, exist_ok=True)
            log_path = debug_d / f"scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        else:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        root.info("Log file: %s", log_path)

    root.info("Logging configured (level=%s)", level_name)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under hkre.* (inherits root handlers)."""
    if name == _LOGGER_ROOT or name.startswith(_LOGGER_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")
