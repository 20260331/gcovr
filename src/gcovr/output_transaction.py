#  ************************** Copyrights and license ***************************
#
# This file is part of gcovr 8.6+main, a parsing and reporting tool for gcov.
# https://gcovr.com/en/main
#
# _____________________________________________________________________________
#
# Copyright (c) 2013-2026 the gcovr authors
# Copyright (c) 2013 Sandia Corporation.
# Under the terms of Contract DE-AC04-94AL85000 with Sandia Corporation,
# the U.S. Government retains certain rights in this software.
#
# This software is distributed under the 3-clause BSD License.
# For more information, see the README.rst file.
#
# ****************************************************************************

"""
Transactional publishing of report outputs.

All reports of one gcovr run are written into a private staging
directory. Only when every report was generated successfully are the
files moved to their final locations, replacing previous reports. If a
writer fails or the process is interrupted, nothing is published and
the output locations keep their state from before the run.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import shutil
import signal
import sys
import tempfile
from contextlib import contextmanager
from typing import Generator

from .logging import LOGGER
from .options import OutputOrDefault

STAGING_PREFIX = ".gcovr-staging-"
SHADOW_DIRNAME = "files"
STDOUT_DIRNAME = "_stdout"
DRIVE_POSTFIX = "_drive"


def _remove_path(path: str) -> None:
    """Remove a file, symlink or directory."""
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


class OutputTransaction:
    """Collect outputs in a private directory and publish them as a unit."""

    def __init__(self) -> None:
        """Initialize the OutputTransaction instance."""
        self._token = secrets.token_hex(4)
        self._root = tempfile.mkdtemp(prefix=STAGING_PREFIX)
        self._shadow_dir = os.path.join(self._root, SHADOW_DIRNAME)
        os.mkdir(self._shadow_dir)
        self._stdout_dir = os.path.join(self._root, STDOUT_DIRNAME)
        os.mkdir(self._stdout_dir)
        self._stdout_files = list[str]()
        self._completed = False

    def stage(self, abspath: str) -> str:
        """Return the private path mirroring the given output path."""
        shadow = self._shadow_path(abspath)
        directory = shadow if abspath.endswith(os.sep) else os.path.dirname(shadow)
        os.makedirs(directory, exist_ok=True)
        return shadow

    def capture_stdout(self) -> str:
        """Return a private file path for an output that would go to stdout."""
        path = os.path.join(self._stdout_dir, f"{len(self._stdout_files):04d}")
        self._stdout_files.append(path)
        return path

    def commit(self) -> None:
        """Publish all staged outputs, replacing any previous reports."""
        if self._completed:
            return

        moves: list[tuple[str, str | None, str]] = []
        # Phase 1: move existing targets to backup paths.
        for shadow, target in self._iter_leaves():
            backup = None
            if os.path.lexists(target):
                backup = f"{target}.gcovr-backup-{self._token}"
                os.rename(target, backup)
            moves.append((target, backup, shadow))

        try:
            # Phase 2: publish the staged files.
            for target, _backup, shadow in moves:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                self._move_into_place(shadow, target)
        except BaseException:
            self._restore_after_failed_publish(moves)
            raise

        # Phase 3: remove superseded backups, flush stdout captures and clean up.
        for _target, backup, _shadow in moves:
            if backup is not None:
                _remove_path(backup)
        self._emit_stdout()
        shutil.rmtree(self._root, ignore_errors=True)
        self._completed = True

    def abort(self) -> None:
        """Discard all staged outputs without touching the output locations."""
        if self._completed:
            return

        shutil.rmtree(self._root, ignore_errors=True)
        # Directories created while resolving the output options did not
        # exist before the run, remove them again if they are still empty.
        for directory in list(OutputOrDefault.created_directories):
            with contextlib.suppress(OSError):
                os.rmdir(directory)
            OutputOrDefault.created_directories.discard(directory)
        self._completed = True

    def _shadow_path(self, abspath: str) -> str:
        """Get the staging path mirroring the given absolute path."""
        drive, path = os.path.splitdrive(abspath)
        parts = list[str]()
        if drive:
            parts.append(f"{drive.rstrip(':')}{DRIVE_POSTFIX}")
        relative = path.lstrip(os.sep)
        if relative:
            parts.append(relative)
        shadow = os.path.join(self._shadow_dir, *parts)
        if abspath.endswith(os.sep):
            shadow += os.sep
        return shadow

    def _target_for_shadow(self, path: str) -> str:
        """Get the absolute destination path for a staging path."""
        relative = os.path.relpath(path, self._shadow_dir)
        parts = relative.split(os.sep)
        drive = ""
        if parts[0].endswith(DRIVE_POSTFIX):
            drive = f"{parts[0][: -len(DRIVE_POSTFIX)]}:"
            parts = parts[1:]
        target = os.path.join(*parts) if parts else ""
        if drive:
            return drive + os.sep + target
        return os.sep + target

    def _iter_leaves(self) -> Generator[tuple[str, str], None, None]:
        """Yield pairs of staging path and destination path for all outputs."""
        for root, dirs, files in os.walk(self._shadow_dir):
            for name in files:
                path = os.path.join(root, name)
                yield path, self._target_for_shadow(path)
            # Symlinks to directories show up in dirs but are not traversed.
            for name in dirs:
                path = os.path.join(root, name)
                if os.path.islink(path):
                    yield path, self._target_for_shadow(path)

    def _move_into_place(self, shadow: str, target: str) -> None:
        """Move a staged output to its destination."""
        try:
            os.rename(shadow, target)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Staging directory and target live on different file systems,
            # copy to a hidden sibling first and replace the target atomically.
            incoming = f"{target}.gcovr-incoming-{self._token}"
            try:
                if os.path.islink(shadow):
                    os.symlink(os.readlink(shadow), incoming)
                    os.unlink(shadow)
                elif os.path.isdir(shadow):
                    shutil.copytree(shadow, incoming, symlinks=True)
                    shutil.rmtree(shadow)
                else:
                    shutil.copy2(shadow, incoming, follow_symlinks=False)
                    os.unlink(shadow)
                os.replace(incoming, target)
            except BaseException:
                if os.path.lexists(incoming):
                    _remove_path(incoming)
                raise

    def _restore_after_failed_publish(
        self, moves: list[tuple[str, str | None, str]]
    ) -> None:
        """Remove published outputs and restore the backups."""
        for target, backup, _shadow in moves:
            if os.path.lexists(target):
                _remove_path(target)
            if backup is not None and os.path.lexists(backup):
                try:
                    os.rename(backup, target)
                except OSError:
                    LOGGER.error("Could not restore %s from backup %s.", target, backup)

    def _emit_stdout(self) -> None:
        """Write the buffered stdout outputs in capture order."""
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            return
        for path in self._stdout_files:
            with open(path, "rb") as fh_in:
                shutil.copyfileobj(fh_in, buffer)
        buffer.flush()


@contextmanager
def output_transaction(
    *, enabled: bool
) -> Generator[OutputTransaction | None, None, None]:
    """Provide an output transaction if enabled."""
    if not enabled:
        yield None
        return

    transaction = OutputTransaction()
    previous: dict[int, object] = {}

    def _rollback_and_propagate(signum: int, _frame: object) -> None:
        """Abort the transaction and re-raise the signal."""
        LOGGER.error("Interrupted by signal %s, discarding new reports.", signum)
        transaction.abort()
        handler = previous.get(signum, signal.SIG_DFL)
        signal.signal(signum, handler)  # type: ignore[arg-type]
        os.kill(os.getpid(), signum)

    def _install(sig: int) -> bool:
        """Install the rollback handler, return if the signal could be changed."""
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, _rollback_and_propagate)
        except (OSError, ValueError):
            return False
        return True

    def _restore(sig: int) -> None:
        """Restore the previous signal handler."""
        with contextlib.suppress(OSError, ValueError):
            signal.signal(sig, previous[sig])  # type: ignore[arg-type]

    installed = [sig for sig in (signal.SIGINT, signal.SIGTERM) if _install(sig)]

    try:
        yield transaction
        transaction.commit()
    except BaseException:
        transaction.abort()
        raise
    finally:
        for sig in installed:
            _restore(sig)
