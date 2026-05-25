"""
Google Docs Operations Module
Handles logging operations in Google Docs
"""

import os
import random
import time

from googleapiclient.errors import HttpError

# Google Docs quotas are strict (often ~60 write requests/min per user). Each update_log
# issues documents.get + batchUpdate; scraping can exceed that burst without throttling/retry.

_LAST_WRITE_MONO = [0.0]
_DOCUMENT_ID = "1GxDfL0Y5_62HniHOxDS9j1VdHIO22QohkK7UaOT-PtA"


def _docs_write_delay_sec() -> float:
    raw = os.getenv("HKRE_DOCS_WRITE_DELAY_SEC")
    if raw is not None and str(raw).strip() != "":
        return max(0.0, float(raw))
    # In CI bursts are continuous; pace writes below the default Docs write quota (~60/min).
    if os.getenv("GITHUB_ACTIONS", "").strip():
        return 1.1
    return 0.0


def _throttle_writes():
    delay = _docs_write_delay_sec()
    if delay <= 0:
        return
    now = time.monotonic()
    wait = delay - (now - _LAST_WRITE_MONO[0])
    if wait > 0:
        time.sleep(wait)


def update_log(docs, text):
    """
    Append text to the Google Docs log document.

    Env:
      HKRE_SKIP_DOCS_LOG=1 — disable Doc writes (scrapes keep running).
      HKRE_DOCS_WRITE_DELAY_SEC — seconds between Doc writes (default 1.1 in GitHub Actions, else 0).
    """
    if os.getenv("HKRE_SKIP_DOCS_LOG", "").strip().lower() in ("1", "true", "yes"):
        return

    backoff = float(os.getenv("HKRE_DOCS_429_RETRY_BASE_SEC", "4"))

    # Retry transient 429 RESOURCE_EXHAUSTED from Docs write quota.
    for attempt in range(12):
        try:
            _throttle_writes()

            document = docs.documents().get(documentId=_DOCUMENT_ID).execute()
            body = document.get("body", {}) or {}
            content = body.get("content", []) or []

            end_index = None
            if content:
                last_element = content[-1]
                end_index = last_element.get("endIndex", 1)

            if end_index is None:
                end_index = 2

            requests_payload = [
                {
                    "insertText": {
                        "location": {"index": end_index - 1},
                        "text": f"{text}",
                    }
                }
            ]

            result = docs.documents().batchUpdate(
                documentId=_DOCUMENT_ID, body={"requests": requests_payload}
            ).execute()

            _LAST_WRITE_MONO[0] = time.monotonic()
            return result

        except HttpError as e:
            if e.resp is not None and e.resp.status == 429 and attempt < 11:
                ra = getattr(e.resp, "get", lambda k, d=None: d)("retry-after", None)
                try:
                    wait = float(ra) + random.uniform(0.25, 1.25)
                except (TypeError, ValueError):
                    wait = backoff * (2**attempt) + random.uniform(0.5, 2.5)
                time.sleep(min(wait, 120))
                continue
            raise
