"""
notify_board.py — fail-safe failure alert for the Equity Scoreboard cron.

Mirrors the razor notify.py discipline (OUTBOUND-ONLY, SECRET-FREE, FAIL-SAFE,
STDLIB-ONLY) but is SELF-CONTAINED: it imports NO private razor modules
(no txlog, no config) because this file ships in the PUBLIC equity-scoreboard
subtree. It carries nothing secret and depends on nothing outside this folder.

What it does:
  - On a FAILED refresh (engine error or validation REJECT), the workflow calls
    `bump_failure()`; on success it calls `clear()`.
  - Failures are counted in a tiny state file (.refresh_failures.txt). When the
    count reaches N consecutive failures (default 2), one ntfy push fires so the
    board can't drift silently. Dedup: it fires ONCE when the threshold is first
    crossed and stays quiet until a success clears the counter.
  - The board page already shows "generated X ago" (honest staleness); this adds
    the active push so a stuck pipeline pages the phone.

Privacy/secret model (identical to razor notify.py): the ntfy TOPIC is the secret
(a long unguessable string in env RAZOR_NTFY_TOPIC). It is never logged in full,
never put in a message body, never committed. Message bodies carry only pipeline
status — never keys/tokens/order data — and are scanned before egress.

Env:
  RAZOR_NTFY_TOPIC   — ntfy topic (the secret). Absent → clean no-op.
  NTFY_SERVER        — override server (default https://ntfy.sh).
  FAIL_ALERT_AFTER   — N consecutive failures before paging (default 2).

Usage (from the workflow):
  python -m engine.notify_board fail "validation REJECT: 3 checks"
  python -m engine.notify_board ok
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

_DEFAULT_SERVER = "https://ntfy.sh"
_HTTP_TIMEOUT_S = 8.0
_STATE = os.path.join(os.path.dirname(__file__), "..", ".refresh_failures.txt")

# Secret-signature substrings refused in any body before egress (belt-and-braces;
# the call sites pass only pipeline status). Mirrors razor notify._SECRET_MARKERS.
_SECRET_MARKERS = ("0x", "private key", "begin ", "secret", "bearer ", "token")


def _topic() -> str:
    return os.environ.get("RAZOR_NTFY_TOPIC", "").strip()


def _server() -> str:
    return os.environ.get("NTFY_SERVER", _DEFAULT_SERVER).rstrip("/")


def _threshold() -> int:
    try:
        return max(1, int(os.environ.get("FAIL_ALERT_AFTER", "2")))
    except ValueError:
        return 2


def _read_count() -> int:
    try:
        with open(_STATE, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_count(n: int) -> None:
    try:
        with open(_STATE, "w", encoding="utf-8") as f:
            f.write(str(n))
    except OSError:
        pass  # best-effort; never raise


def _assert_no_secret(text: str) -> None:
    low = text.lower()
    for m in _SECRET_MARKERS:
        if m in low:
            raise ValueError(f"refusing body with secret marker {m!r}")


def push(title: str, message: str, *, priority: str = "high",
         tags: str | None = None) -> dict:
    """Send ONE outbound ntfy push. Best-effort, NEVER raises."""
    topic = _topic()
    if not topic:
        return {"pushed": False, "reason": "no topic (unprovisioned) — no-op"}
    try:
        _assert_no_secret(title)
        _assert_no_secret(message)
    except ValueError as e:
        return {"pushed": False, "reason": f"secret-guard: {e}"}
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(
        f"{_server()}/{topic}", data=message.encode("utf-8"),
        method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            return {"pushed": resp.status == 200, "status": resp.status}
    except urllib.error.HTTPError as e:
        return {"pushed": False, "reason": f"http {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"pushed": False, "reason": f"error: {e.__class__.__name__}"}


def bump_failure(detail: str = "") -> dict:
    """Increment the consecutive-failure counter. Page ONCE when it reaches the
    threshold; stay quiet on further failures until a success clears it."""
    n = _read_count() + 1
    _write_count(n)
    thr = _threshold()
    if n < thr:
        return {"pushed": False, "reason": f"failure {n}/{thr} — below alert threshold"}
    if n > thr:
        return {"pushed": False, "reason": f"failure {n} — already paged at {thr} (dedup)"}
    # n == thr: cross the threshold exactly once
    title = "Scoreboard: ⚠ refresh FAILING — board going stale"
    safe_detail = (detail or "")[:300]
    message = (
        f"The Equity Scoreboard refresh has failed {n} consecutive runs.\n"
        f"Last: {safe_detail}\n"
        f"The live board is NOT updating (last good snapshot still served). "
        f"Check the GitHub Actions run."
    )
    return push(title, message, priority="high", tags="warning,chart_with_downwards_trend")


def clear() -> dict:
    """Reset the counter on a successful refresh. If we had previously paged
    (count >= threshold), send a low-priority 'recovered' note."""
    n = _read_count()
    _write_count(0)
    if n >= _threshold():
        return push("Scoreboard: ✓ refresh recovered",
                    "The Equity Scoreboard refresh is succeeding again; board is fresh.",
                    priority="low", tags="white_check_mark")
    return {"pushed": False, "reason": "no prior alert — nothing to clear"}


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "fail":
        detail = argv[2] if len(argv) > 2 else ""
        print("notify_board:", bump_failure(detail), file=sys.stderr)
        return 0  # alerting must never itself fail the workflow
    if cmd == "ok":
        print("notify_board:", clear(), file=sys.stderr)
        return 0
    print("usage: python -m engine.notify_board {fail [detail] | ok}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
