#!/usr/bin/env python3
"""
List converged recordings (admin or compliance officer) via Webex API and return total count.

Uses: List Recordings for Admin or Compliance officer
https://developer.webex.com/calling/docs/api/v1/converged-recordings/list-recordings-for-admin-or-compliance-officer

- Prompts for access token (not read from env, to avoid leaving tokens in shell history).
- Handles pagination (Link: rel="next").
- On 429: reads Retry-After, waits, then retries (with cap and backoff).
- Basic error handling for 4xx/5xx, connection errors, and invalid JSON.
- Writes all items to a CSV file (converged_recordings.csv by default).
"""

import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

BASE_URL = "https://webexapis.com/v1"
# List Recordings for Admin or Compliance officer (converged recordings)
CONVERGED_RECORDINGS_PATH = "admin/convergedRecordings"
DEFAULT_MAX_PER_PAGE = 100
MAX_429_RETRIES = 10
RETRY_AFTER_CAP_SECONDS = 300
RETRY_AFTER_DEFAULT_SECONDS = 60
DEFAULT_CSV_FILENAME = "converged_recordings.csv"

# CSV columns (flat); serviceData fields flattened as locationId, callSessionId
CSV_FIELDNAMES = (
    "id", "topic", "createTime", "timeRecorded", "ownerId", "ownerEmail", "ownerType",
    "format", "durationSeconds", "sizeBytes", "serviceType", "storageRegion", "status",
    "locationId", "callSessionId",
)


def _item_to_row(item: dict) -> dict:
    """Convert one API recording item to a flat dict for CSV (missing keys -> empty string)."""
    service_data = item.get("serviceData") or {}
    row = {
        "id": item.get("id", ""),
        "topic": item.get("topic", ""),
        "createTime": item.get("createTime", ""),
        "timeRecorded": item.get("timeRecorded", ""),
        "ownerId": item.get("ownerId", ""),
        "ownerEmail": item.get("ownerEmail", ""),
        "ownerType": item.get("ownerType", ""),
        "format": item.get("format", ""),
        "durationSeconds": item.get("durationSeconds", ""),
        "sizeBytes": item.get("sizeBytes", ""),
        "serviceType": item.get("serviceType", ""),
        "storageRegion": item.get("storageRegion", ""),
        "status": item.get("status", ""),
        "locationId": service_data.get("locationId", ""),
        "callSessionId": service_data.get("callSessionId", ""),
    }
    return row


def write_recordings_csv(items: list[dict], path: str = DEFAULT_CSV_FILENAME) -> None:
    """Write recording items to a CSV file at path."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow(_item_to_row(item))


def get_token_from_user():
    """Prompt user for Webex access token. Token must have spark-admin:recordings_read or spark-compliance:recordings_read."""
    try:
        token = input("Webex access token (admin or compliance officer): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    if not token:
        print("Error: Access token is required.", file=sys.stderr)
        sys.exit(1)
    return token


def parse_link_header(link_header: str) -> str | None:
    """Parse RFC 5988 Link header; return URL for rel='next' or None."""
    if not link_header:
        return None
    # Example: <https://webexapis.com/v1/convergedRecordings?max=100&...>; rel="next"
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part or "rel='next'" in part:
            match = re.search(r"<([^>]+)>", part)
            if match:
                return match.group(1).strip()
    return None


def fetch_page(url: str, token: str, session) -> tuple[dict, str | None]:
    """
    GET one page; return (response_json, next_url from Link header).
    Raises on non-2xx (except 429, which is handled by caller via Retry-After).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if HAS_REQUESTS:
        r = session.get(url, headers=headers, timeout=60)
        next_url = parse_link_header(r.headers.get("Link", ""))
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            try:
                wait_sec = int(retry_after) if retry_after else RETRY_AFTER_DEFAULT_SECONDS
            except ValueError:
                wait_sec = RETRY_AFTER_DEFAULT_SECONDS
            wait_sec = min(wait_sec, RETRY_AFTER_CAP_SECONDS)
            raise RateLimitError(wait_sec, r)
        r.raise_for_status()
        return r.json(), next_url
    else:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read().decode()
                next_url = parse_link_header(resp.headers.get("Link", ""))
                return json.loads(data), next_url
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                try:
                    wait_sec = int(retry_after) if retry_after else RETRY_AFTER_DEFAULT_SECONDS
                except (ValueError, TypeError):
                    wait_sec = RETRY_AFTER_DEFAULT_SECONDS
                wait_sec = min(wait_sec, RETRY_AFTER_CAP_SECONDS)
                raise RateLimitError(wait_sec, e) from e
            raise


class RateLimitError(Exception):
    """Raised when API returns 429; carries wait_seconds and original response."""
    def __init__(self, wait_seconds: int, response):
        self.wait_seconds = wait_seconds
        self.response = response
        super().__init__(f"Rate limited (429); Retry-After: {wait_seconds}s")


def _past_30_days_iso() -> tuple[str, str]:
    """Return (from_iso, to_iso) for the past 30 days in UTC, API-ready (to=now, from=now-30d)."""
    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(days=30)
    return from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


def list_all_recordings(token: str, csv_path: str = DEFAULT_CSV_FILENAME) -> int:
    """
    Paginate through List Recordings for Admin or Compliance officer; return total count.
    Handles 429 by waiting Retry-After then retrying (up to MAX_429_RETRIES).
    Requests the past 30 days from current date/time (UTC).
    Saves all items to csv_path (default: converged_recordings.csv).
    """
    from_iso, to_iso = _past_30_days_iso()
    query = urlencode({"from": from_iso, "to": to_iso, "max": DEFAULT_MAX_PER_PAGE})
    url = f"{BASE_URL}/{CONVERGED_RECORDINGS_PATH}?{query}"
    all_items: list[dict] = []
    session = requests.Session() if HAS_REQUESTS else None
    consecutive_429 = 0

    while True:
        try:
            data, next_url = fetch_page(url, token, session)
        except RateLimitError as e:
            consecutive_429 += 1
            if consecutive_429 > MAX_429_RETRIES:
                print(f"Error: Rate limited {MAX_429_RETRIES} times; giving up.", file=sys.stderr)
                sys.exit(1)
            print(f"Rate limited (429). Waiting {e.wait_seconds}s (Retry-After) before retry...", file=sys.stderr)
            time.sleep(e.wait_seconds)
            continue
        except Exception as e:
            if HAS_REQUESTS and hasattr(e, "response"):
                try:
                    body = e.response.text
                    err = json.loads(body) if body else {}
                    msg = err.get("message", body) or str(e)
                except Exception:
                    msg = str(e)
                print(f"API error ({getattr(e.response, 'status_code', '')}): {msg}", file=sys.stderr)
            else:
                print(f"Request error: {e}", file=sys.stderr)
            raise

        consecutive_429 = 0
        items = data.get("items")
        if not isinstance(items, list):
            print("Error: API response missing or invalid 'items' array.", file=sys.stderr)
            sys.exit(1)
        all_items.extend(items)

        if not next_url:
            break
        url = next_url

    write_recordings_csv(all_items, csv_path)
    print(f"Saved {len(all_items)} recordings to {csv_path}", file=sys.stderr)
    return len(all_items)


def main() -> None:
    if not HAS_REQUESTS:
        print("Error: This script requires the 'requests' library. Install with: pip install requests", file=sys.stderr)
        sys.exit(1)

    token = get_token_from_user()
    try:
        count = list_all_recordings(token)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("Final count: " + str(count))


if __name__ == "__main__":
    main()
