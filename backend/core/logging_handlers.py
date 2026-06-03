"""Logging handlers hardened for transient Windows file locks."""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from pathlib import Path

from core.safe_files import is_transient_file_error, retry_file_operation


class WindowsSafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler with bounded retry/backoff around file operations.

    This preserves normal RotatingFileHandler behavior, but prevents temporary
    Windows sharing violations from killing or wedging the application when
    Defender, indexing, backup tools, or another process briefly locks a log.
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

    def _retry(self, operation, description):
        return retry_file_operation(
            operation,
            description=description,
            attempts=self.retry_attempts,
            initial_delay_seconds=self.retry_initial_delay_seconds,
            max_delay_seconds=self.retry_max_delay_seconds,
            logger=logging.getLogger(__name__),
        )

    def _close_stream(self) -> None:
        if self.stream is None:
            return

        try:
            self.stream.close()
        finally:
            self.stream = None

    def rotate(self, source, dest):  # noqa: D401
        """Retry safe rotate using os.replace where possible."""

        if not os.path.exists(source):
            return

        self._retry(
            lambda: os.replace(source, dest),
            f"rotate log file {source} to {dest}",
        )

    def doRollover(self):  # noqa: D401
        """Retry-safe rollover.

        If rollover is blocked for too long, logging continues appending to the
        active file instead of raising into application code.
        """

        try:
            self._retry(
                super().doRollover,
                f"roll over log file {self.baseFilename}",
            )
        except OSError as exc:
            if not is_transient_file_error(exc):
                raise

            # Do not let logging rollover failure raise a critical faliure. The file
            # may temporarily exceed maxBytes, but the next emit can retry.
            logging.getLogger(__name__).warning(
                "skipping log rollover because Windows still has the log locked: %s",
                self.baseFilename,
                exc_info=True,
            )
            self._close_stream()

    def emit(self, record):  # noqa: D401
        """Emit a record with retry-safe rollover, write, and flush."""

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

            except Exception:  # pylint: disable=broad-except
                self.handleError(record)
                return
