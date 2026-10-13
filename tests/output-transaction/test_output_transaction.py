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

"""Tests for the transactional publishing of reports."""

import json
import os
import signal
from pathlib import Path

import pytest

from gcovr.__main__ import EXIT_WRITE_ERROR, main
from gcovr.data_model.version import FORMAT_VERSION
from gcovr.options import OutputOrDefault
from gcovr.output_transaction import (
    STAGING_PREFIX,
    OutputTransaction,
    output_transaction,
)


def _write_tracefile(directory: Path) -> str:
    """Create a JSON tracefile with one source file and return its path."""
    tracefile = directory / "trace.json"
    data = {
        "gcovr/format_version": FORMAT_VERSION,
        "files": [
            {
                "file": "main.cpp",
                "functions": [],
                "lines": [
                    {"line_number": 1, "count": 1, "branches": []},
                    {
                        "line_number": 2,
                        "count": 1,
                        "branches": [
                            {
                                "branchno": 0,
                                "count": 1,
                                "fallthrough": False,
                                "throw": False,
                            },
                            {
                                "branchno": 1,
                                "count": 0,
                                "fallthrough": True,
                                "throw": False,
                            },
                        ],
                    },
                    {"line_number": 3, "count": 0, "branches": []},
                ],
            }
        ],
    }
    tracefile.write_text(json.dumps(data), encoding="utf-8")
    # The HTML details report needs the source file relative to the root.
    (directory / "main.cpp").write_text(
        "int main() {\n  return 0;\n}\n", encoding="utf-8"
    )
    return str(tracefile)


def _force_txt_writer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the text report writer fail."""

    def _failing_write_report(
        _covdata: object, _output_file: object, _options: object
    ) -> None:
        msg = "forced txt writer failure"
        raise RuntimeError(msg)

    monkeypatch.setattr("gcovr.formats.txt.write.write_report", _failing_write_report)


def test_commit_publishes_all_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """All reports are published after a successful run."""
    tracefile = _write_tracefile(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = main(
        [
            "--root",
            str(tmp_path),
            "-a",
            tracefile,
            "--output-transaction",
            "--txt",
            str(out_dir / "coverage.txt"),
            "--json",
            str(out_dir / "coverage.json"),
            "--cobertura",
            str(out_dir / "coverage.xml"),
            "--html-details",
            str(tmp_path / "html") + os.sep,
        ]
    )
    capsys.readouterr()

    assert rc == 0
    assert (out_dir / "coverage.txt").is_file()
    assert (out_dir / "coverage.json").is_file()
    assert (out_dir / "coverage.xml").is_file()
    html_dir = tmp_path / "html"
    assert (html_dir / "coverage_details.html").is_file()
    assert list(html_dir.glob("coverage_details.main.cpp.*.html"))
    assert (html_dir / "coverage_details.css").is_file()
    assert (html_dir / "coverage_details.js").is_file()
    # No hidden staging or backup files remain.
    for directory in [out_dir, html_dir]:
        assert not [
            str(path)
            for path in directory.rglob("*")
            if path.name.startswith(".") or STAGING_PREFIX in path.name
        ]


def test_rollback_restores_previous_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed run keeps previous reports and discards new outputs."""
    tracefile = _write_tracefile(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old_txt = out_dir / "coverage.txt"
    old_txt.write_text("OLD REPORT", encoding="utf-8")
    unrelated = out_dir / "unrelated.txt"
    unrelated.write_text("KEEP ME", encoding="utf-8")
    html_dir = tmp_path / "html"
    assert not html_dir.exists()

    _force_txt_writer_failure(monkeypatch)

    rc = main(
        [
            "--root",
            str(tmp_path),
            "-a",
            tracefile,
            "--output-transaction",
            "--json",
            str(out_dir / "coverage.json"),
            "--cobertura",
            str(out_dir / "coverage.xml"),
            "--html-details",
            str(html_dir) + os.sep,
            "--txt",
            str(old_txt),
        ]
    )
    capsys.readouterr()

    assert rc == EXIT_WRITE_ERROR
    assert "forced txt writer failure" in caplog.text
    # Previous reports are unchanged.
    assert old_txt.read_text(encoding="utf-8") == "OLD REPORT"
    assert unrelated.read_text(encoding="utf-8") == "KEEP ME"
    # New outputs were never published.
    assert not (out_dir / "coverage.json").exists()
    assert not (out_dir / "coverage.xml").exists()
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "coverage.txt",
        "unrelated.txt",
    ]
    # The output directory created during the run is removed again.
    assert not html_dir.exists()


def test_stdout_emitted_only_on_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stdout outputs are buffered until every report was written."""
    tracefile = _write_tracefile(tmp_path)

    rc = main(["-a", tracefile, "--output-transaction", "--txt"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "GCC Code Coverage Report" in captured.out

    _force_txt_writer_failure(monkeypatch)
    json_output = tmp_path / "coverage.json"
    rc = main(
        [
            "-a",
            tracefile,
            "--output-transaction",
            "--json",
            str(json_output),
            "--txt",
        ]
    )
    captured = capsys.readouterr()
    assert rc == EXIT_WRITE_ERROR
    assert captured.out == ""
    assert not json_output.exists()


def test_stdout_outputs_keep_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Several buffered stdout outputs are emitted in writer order."""
    tracefile = _write_tracefile(tmp_path)

    rc = main(["-a", tracefile, "--output-transaction", "--txt", "-s"])
    captured = capsys.readouterr()

    assert rc == 0
    report_pos = captured.out.index("GCC Code Coverage Report")
    summary_pos = captured.out.index("lines:")
    assert report_pos < summary_pos


def test_commit_replaces_existing_file(tmp_path: Path) -> None:
    """Publishing replaces an already existing report file."""
    target = tmp_path / "report.txt"
    target.write_text("OLD", encoding="utf-8")

    transaction = OutputTransaction()
    staged = transaction.stage(str(target))
    Path(staged).write_text("NEW", encoding="utf-8")
    transaction.commit()

    assert target.read_text(encoding="utf-8") == "NEW"
    assert not Path(transaction._root).exists()  # noqa: SLF001


def test_abort_leaves_targets_untouched(tmp_path: Path) -> None:
    """Aborting discards staged files and keeps the destination."""
    target = tmp_path / "report.txt"
    target.write_text("OLD", encoding="utf-8")

    transaction = OutputTransaction()
    staged = transaction.stage(str(target))
    Path(staged).write_text("NEW", encoding="utf-8")
    transaction.abort()

    assert target.read_text(encoding="utf-8") == "OLD"
    assert not Path(transaction._root).exists()  # noqa: SLF001


def test_abort_removes_empty_created_directories(tmp_path: Path) -> None:
    """Abort removes empty option-created dirs, keeps non-empty ones."""
    empty_dir = tmp_path / "newout"
    empty_dir.mkdir()
    kept_dir = tmp_path / "keptout"
    kept_dir.mkdir()
    (kept_dir / "file.txt").write_text("CONTENT", encoding="utf-8")
    OutputOrDefault.created_directories.clear()
    OutputOrDefault.created_directories.update({str(empty_dir), str(kept_dir)})

    transaction = OutputTransaction()
    transaction.abort()

    assert not empty_dir.exists()
    assert kept_dir.is_dir()
    assert (kept_dir / "file.txt").is_file()


def test_signal_triggers_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    """A termination signal aborts the transaction before being propagated."""
    aborted = []
    real_abort = OutputTransaction.abort

    def spy_abort(this: OutputTransaction) -> None:
        aborted.append(True)
        real_abort(this)

    monkeypatch.setattr(OutputTransaction, "abort", spy_abort)
    # Ignore the re-sent signal so the test process survives.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with output_transaction(enabled=True):
            os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    assert aborted
