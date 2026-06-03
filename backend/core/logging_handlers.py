"""Logging handlers hardened for transient Windows file locks."""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from pathlib import Path

from core.safe_files import is_transient_file_error


class WindowsSafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler with bounded retry/backoff around Windows locks.

    This preserves normal RotatingFileHandler behavior, but prevents temporary
    sharing violations from escaping into application code.
    """

    def __init__(
        self,
        filename,
        mode="a",
        maxBytes=0,  # pylint: disable=invalid-name
        backupCount=0,  # pylint: disable=invalid-name
        encoding=None,
        delay=False,
        errors=None,
        retry_attempts=8,
        retry_initial_delay_seconds=0.25,
        retry_max_delay_seconds=2.0,
    ):
        self.retry_attempts = retry_attempts
        self.retry_initial_delay_seconds = retry_initial_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds

        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        super().__init__(
            filename,
            mode=mode,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            errors=errors,
        )

    def _close_stream(self) -> None:
        if self.stream is None:
            return

        try:
            self.stream.close()
        finally:
            self.stream = None

    def _retry_transient_os_error(self, operation) -> bool:
        delay = self.retry_initial_delay_seconds

        for attempt in range(1, self.retry_attempts + 1):
            try:
                operation()
                return True
            except OSError as exc:
                if not is_transient_file_error(exc):
                    raise

                self._close_stream()

                if attempt >= self.retry_attempts:
                    return False

                time.sleep(delay)
                delay = min(self.retry_max_delay_seconds, delay * 1.5)

        return False

    def rotate(self, source, dest):
        """Retry-safe log-file rename."""

        if not os.path.exists(source):
            return

        success = self._retry_transient_os_error(
            lambda: os.replace(source, dest)
        )
        if not success:
            raise PermissionError(
                f"could not rotate locked log file after retries: {source}"
            )

    def doRollover(self):
        """Retry safe rollover.

        If rollover is temporarily blocked, leave the current log in place.
        A later emit will try rollover again.
        """

        success = self._retry_transient_os_error(super().doRollover)
        if not success:
            self._close_stream()

    def emit(self, record):
        """Emit with retry-safe rollover, open, write, and flush."""

        delay = self.retry_initial_delay_seconds

        for attempt in range(1, self.retry_attempts + 1):
            try:
                if self.shouldRollover(record):
                    self.doRollover()

                if self.stream is None:
                    self.stream = self._open()

                message = self.format(record)
                self.stream.write(message + self.terminator)
                self.flush()
                return

            except OSError as exc:
                if not is_transient_file_error(exc):
                    self.handleError(record)
                    return

                self._close_stream()

                if attempt >= self.retry_attempts:
                    self.handleError(record)
                    return

                time.sleep(delay)
                delay = min(self.retry_max_delay_seconds, delay * 1.5)

            except Exception:
                self.handleError(record)
                return
