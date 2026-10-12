"""Application logs kept on disk for CERT-In's 180 days.

CERT-In Directions (28 Apr 2022) require logs of ICT systems to be kept for
180 days (within India). Container logs live only as long as the container,
so when LOG_DIR is set every process also writes to
<LOG_DIR>/<LOG_NAME>-YYYY-MM-DD.log. A new file per day (rather than renaming
one file at midnight) keeps this safe with several worker processes writing
at once; purge_old_logs() removes files past the retention period.
"""
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone

DATED = re.compile(r"^(?P<name>[\w.-]+)-(?P<day>\d{4}-\d{2}-\d{2})\.log$")


class DailyFileHandler(logging.Handler):
    def __init__(self, directory, name="app", level=logging.NOTSET):
        super().__init__(level)
        self.directory = directory
        self.name_prefix = name
        self._day = None
        self._stream = None
        self._disabled = False
        try:
            os.makedirs(directory, exist_ok=True)
            probe = os.path.join(directory, ".write-test")
            open(probe, "a").close()
            os.remove(probe)
        except OSError as exc:
            # Don't break (or flood) logging if the folder isn't writable: say so once.
            import sys

            sys.stderr.write(f"Daily log files disabled: can't write to {directory} ({exc.strerror}).\n")
            self._disabled = True

    def _open(self, day):
        if self._stream:
            self._stream.close()
        path = os.path.join(self.directory, f"{self.name_prefix}-{day.isoformat()}.log")
        self._stream = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered appends
        self._day = day

    def emit(self, record):
        if self._disabled:
            return
        try:
            day = datetime.fromtimestamp(record.created, tz=timezone.utc).date()
            self.acquire()
            try:
                if day != self._day:
                    self._open(day)
                self._stream.write(self.format(record) + "\n")
            finally:
                self.release()
        except Exception:  # noqa: BLE001 — logging must never break a request
            self.handleError(record)

    def close(self):
        self.acquire()
        try:
            if self._stream:
                self._stream.close()
                self._stream = None
        finally:
            self.release()
        super().close()


def purge_old_logs(directory, keep_days, today=None) -> int:
    """Delete dated log files older than `keep_days` (never fewer than 180)."""
    if not directory or not os.path.isdir(directory):
        return 0
    keep_days = max(180, int(keep_days))
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    removed = 0
    for name in os.listdir(directory):
        m = DATED.match(name)
        if m and date.fromisoformat(m.group("day")) < cutoff:
            os.remove(os.path.join(directory, name))
            removed += 1
    return removed
