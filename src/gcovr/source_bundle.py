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
# For more information, see the README.rst file in the top level directory.
#
# ****************************************************************************

"""
Export and import of verifiable source bundles.

A source bundle is a ZIP archive which contains the source files that are
part of the coverage report together with a manifest holding integrity
information (hash digest and size) for every file.  It is used to generate
the annotated HTML reports on a machine which doesn't have a checkout of the
source code (e.g. a dedicated reporting machine).
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .logging import LOGGER
from .utils import PRETTY_JSON_INDENT, force_unix_separator, get_version_for_report

if TYPE_CHECKING:
    from types import TracebackType

    try:
        from typing import Self
    except ImportError:
        from typing_extensions import Self

#: Version of the source bundle format, only the major number has to match.
BUNDLE_FORMAT_VERSION = "1.0"

#: Name of the manifest member inside the archive.
MANIFEST_FILENAME = "manifest.json"

#: Prefix of the members which hold the source file contents.
SOURCES_PREFIX = "sources/"

#: Algorithm used for the integrity digests, stored in the manifest.
HASH_ALGORITHM = "sha256"

#: Suffix of files which are read from stdin and therefore can't be bundled.
STDIN_SUFFIX = "<stdin>"


def _normalize_filename(filename: str) -> str:
    """Normalize a filename to the notation used as key in the bundle."""
    return force_unix_separator(os.path.normpath(filename))


def _member_name(digest: str) -> str:
    """Get the archive member name for the content with the given digest."""
    return SOURCES_PREFIX + digest


def _digest(data: bytes) -> str:
    """Calculate the integrity digest of the given bytes."""
    return hashlib.new(HASH_ALGORITHM, data, usedforsecurity=False).hexdigest()


def _digest_of_file(filename: str) -> tuple[int, str]:
    """Calculate size and integrity digest of a file without loading it at once."""
    hasher = hashlib.new(HASH_ALGORITHM, usedforsecurity=False)
    size = 0
    with Path(filename).open("rb") as fh_in:
        while chunk := fh_in.read(1024 * 1024):
            size += len(chunk)
            hasher.update(chunk)
    return size, hasher.hexdigest()


def write_source_bundle(
    output_path: str,
    sources: Iterable[tuple[str, str]],
    *,
    coverage_inputs: Iterable[str] = (),
) -> None:
    """
    Write a source bundle with all given source files.

    ``sources`` is an iterable of ``(entry_name, path_on_disk)`` tuples where
    ``entry_name`` is the machine independent name used as key in the bundle
    (the root relative source filename) and ``path_on_disk`` is the actual
    file to read on the exporting machine.  The files are stored content
    addressed by their digest so that equal contents are only stored once.
    Integrity information (digest and size) for every file and for the
    optional coverage input files is written to the manifest.  If any source
    file can't be read a ``RuntimeError`` is raised and no bundle is written.
    """
    file_entries = list[dict[str, object]]()
    blobs_by_digest = dict[str, bytes]()
    missing_files = list[str]()
    seen_entries: dict[str, str] = {}

    for entry_name, source_path in sources:
        filename = _normalize_filename(entry_name)
        if filename in seen_entries:
            continue
        seen_entries[filename] = source_path

        if filename.endswith(STDIN_SUFFIX):
            LOGGER.info(
                "Skipping %s for the source bundle, it has no real file.",
                filename,
            )
            continue

        try:
            data = Path(source_path).read_bytes()
        except OSError as exc:
            LOGGER.warning(
                "Can't add source file to source bundle, using empty lines "
                "in the report instead: %s",
                exc,
            )
            missing_files.append(filename)
            continue

        digest = _digest(data)
        if digest not in blobs_by_digest:
            blobs_by_digest[digest] = data
        file_entries.append(
            {
                "file": filename,
                "size": len(data),
                "digest": digest,
            }
        )

    if missing_files:
        msg = (
            f"{len(missing_files)} source file(s) for the source bundle could "
            "not be read:\n  " + "\n  ".join(sorted(missing_files))
        )
        raise RuntimeError(msg)

    coverage_entries = list[dict[str, object]]()
    for coverage_input in coverage_inputs:
        size, digest = _digest_of_file(coverage_input)
        coverage_entries.append(
            {
                "name": os.path.basename(coverage_input),
                "size": size,
                "digest": digest,
            }
        )

    manifest = {
        "gcovr/bundle_format_version": BUNDLE_FORMAT_VERSION,
        "gcovr/version": get_version_for_report(),
        "hash_algorithm": HASH_ALGORITHM,
        "files": sorted(file_entries, key=lambda entry: str(entry["file"])),
        "coverage_inputs": sorted(
            coverage_entries, key=lambda entry: str(entry["name"])
        ),
    }

    LOGGER.info(
        "Writing source bundle with %d source file(s) to %s",
        len(file_entries),
        output_path,
    )
    with zipfile.ZipFile(
        output_path, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for digest, data in sorted(blobs_by_digest.items()):
            archive.writestr(_member_name(digest), data)
        archive.writestr(
            MANIFEST_FILENAME,
            json.dumps(manifest, indent=PRETTY_JSON_INDENT) + "\n",
        )


class SourceBundleReader:
    """Read source files from a verified source bundle."""

    def __init__(self, bundle_path: str) -> None:
        """Open the bundle, parse the manifest and verify its integrity."""
        self._path = bundle_path
        try:
            # The archive is closed by close()/__exit__() of this class.
            self._archive = zipfile.ZipFile(bundle_path)  # pylint: disable=consider-using-with
        except (OSError, zipfile.BadZipFile) as exc:
            msg = f"Can't open source bundle {bundle_path!r}: {exc}"
            raise RuntimeError(msg) from None

        try:
            manifest = json.loads(self._archive.read(MANIFEST_FILENAME).decode("utf-8"))
        except KeyError:
            msg = (
                f"Source bundle {bundle_path!r} doesn't contain a {MANIFEST_FILENAME}."
            )
            raise RuntimeError(msg) from None
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ) as exc:
            msg = f"The manifest of source bundle {bundle_path!r} is invalid: {exc}"
            raise RuntimeError(msg) from None

        try:
            format_version = str(manifest["gcovr/bundle_format_version"])
            hash_algorithm = str(manifest["hash_algorithm"])
            files = list(manifest["files"])
            coverage_inputs = list(manifest.get("coverage_inputs", []))
        except KeyError as exc:
            msg = (
                f"The manifest of source bundle {bundle_path!r} is invalid, "
                f"missing entry {exc}."
            )
            raise RuntimeError(msg) from None
        except (AttributeError, TypeError) as exc:
            msg = f"The manifest of source bundle {bundle_path!r} has an invalid structure."
            raise RuntimeError(msg) from exc

        major_version = format_version.split(".", maxsplit=1)[0]
        if major_version != BUNDLE_FORMAT_VERSION.split(".", maxsplit=1)[0]:
            msg = (
                f"Source bundle {bundle_path!r} has unsupported format version "
                f"{format_version!r}, expected {BUNDLE_FORMAT_VERSION!r}."
            )
            raise RuntimeError(msg)

        if hash_algorithm != HASH_ALGORITHM:
            msg = (
                f"Source bundle {bundle_path!r} uses unsupported hash algorithm "
                f"{hash_algorithm!r}, expected {HASH_ALGORITHM!r}."
            )
            raise RuntimeError(msg)

        self._entries = dict[str, tuple[str, int]]()
        for entry in files:
            try:
                filename = _normalize_filename(str(entry["file"]))
                digest = str(entry["digest"])
                size = int(entry["size"])
            except (KeyError, TypeError, ValueError):
                msg = (
                    f"The manifest of source bundle {bundle_path!r} contains "
                    f"an invalid file entry: {entry!r}."
                )
                raise RuntimeError(msg) from None
            if filename in self._entries:
                msg = (
                    f"The manifest of source bundle {bundle_path!r} contains "
                    f"the file {filename!r} more than once."
                )
                raise RuntimeError(msg)
            self._entries[filename] = (digest, size)

        self._coverage_digests: set[str] | None
        coverage_digests = set[str]()
        try:
            for coverage_entry in coverage_inputs:
                if not isinstance(coverage_entry, dict):
                    raise TypeError
                coverage_digests.add(str(coverage_entry["digest"]))
        except (KeyError, TypeError, ValueError):
            msg = (
                f"The manifest of source bundle {bundle_path!r} contains "
                "an invalid coverage input entry."
            )
            raise RuntimeError(msg) from None
        # An empty list means that the bundle was created without recording
        # the coverage inputs so the binding can't be verified.
        self._coverage_digests = coverage_digests or None

        self._verify_integrity()

    def _verify_integrity(self) -> None:
        """Verify digest and size of every bundled source file."""
        LOGGER.info(
            "Verifying integrity of %d file(s) in source bundle %s...",
            len(self._entries),
            self._path,
        )
        for filename, (digest, expected_size) in self._entries.items():
            member = _member_name(digest)
            try:
                data = self._archive.read(member)
            except KeyError:
                msg = (
                    f"Integrity check failed for {filename!r}: the source bundle "
                    f"{self._path!r} is missing the member {member!r}."
                )
                raise RuntimeError(msg) from None
            except (OSError, zipfile.BadZipFile) as exc:
                msg = (
                    f"Integrity check failed for {filename!r}: the source bundle "
                    f"{self._path!r} is corrupted: {exc}"
                )
                raise RuntimeError(msg) from None

            if len(data) != expected_size:
                msg = (
                    f"Integrity check failed for {filename!r}: expected size "
                    f"{expected_size} but got {len(data)}."
                )
                raise RuntimeError(msg)
            actual_digest = _digest(data)
            if actual_digest != digest:
                msg = (
                    f"Integrity check failed for {filename!r}: expected digest "
                    f"{digest} but got {actual_digest}."
                )
                raise RuntimeError(msg)

    def __contains__(self, filename: str) -> bool:
        """Check if the bundle contains an entry for the given filename."""
        return _normalize_filename(filename) in self._entries

    def read_source(self, filename: str) -> bytes:
        """
        Read the source file content from the bundle.

        Only the bundle itself is accessed, a local file with the same name
        is never opened.  A ``KeyError`` is raised if the file is not part of
        the bundle.
        """
        entry = self._entries.get(_normalize_filename(filename))
        if entry is None:
            raise KeyError(filename)
        return self._archive.read(_member_name(entry[0]))

    def verify_coverage_inputs(self, tracefiles: Iterable[str]) -> None:
        """
        Verify that the given coverage data files belong to the bundle.

        The digests recorded at export time have to match the given coverage
        input files.  If the bundle contains no coverage information (it was
        created without a JSON report) the check is skipped.
        """
        if self._coverage_digests is None:
            LOGGER.info(
                "Source bundle %s contains no coverage data information, "
                "skipping verification of the coverage input files.",
                self._path,
            )
            return

        for tracefile in tracefiles:
            try:
                _, digest = _digest_of_file(tracefile)
            except OSError as exc:
                msg = f"Can't read coverage data file {tracefile!r} for verification: {exc}"
                raise RuntimeError(msg) from None

            if digest not in self._coverage_digests:
                msg = (
                    f"The coverage data file {tracefile!r} doesn't match the "
                    f"source bundle {self._path!r}, it was created for different "
                    "coverage data."
                )
                raise RuntimeError(msg)
            LOGGER.debug("Coverage data file %s matches the source bundle.", tracefile)

    def close(self) -> None:
        """Close the underlying archive."""
        self._archive.close()

    def __enter__(self) -> Self:
        """Return the reader as context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying archive on exit."""
        self.close()
