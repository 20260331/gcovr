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
# For more information, see the README.rst file in the repo root.
#
# ****************************************************************************

"""
Tests for the optional branch distribution diagnostics.

The tests use a hand crafted JSON tracefile to get deterministic branch
counts independent of the compiler and its version:

* line 10: counts 1000/1  -> skewed with the default settings
* line 20: counts 1/1     -> insufficient samples
* line 30: counts 10/0    -> enough samples but uncovered target
* line 40: counts 50/40   -> balanced
"""

# cspell:ignore tracefile

import json
import logging
import typing

import pytest

from gcovr.data_model.version import FORMAT_VERSION

if typing.TYPE_CHECKING:
    from tests.conftest import GcovrTestExec


def _branch(
    source_block_id: int,
    destination_block_id: int,
    count: int,
    *,
    fallthrough: bool = False,
) -> dict:
    return {
        "count": count,
        "fallthrough": fallthrough,
        "throw": False,
        "source_block_id": source_block_id,
        "destination_block_id": destination_block_id,
    }


def _line(lineno: int, branches: list[dict]) -> dict:
    return {
        "line_number": lineno,
        "count": sum(branch["count"] for branch in branches),
        "function_name": "function",
        "branches": branches,
    }


TRACEFILE = {
    "gcovr/format_version": FORMAT_VERSION,
    "files": [
        {
            "file": "main.c",
            "lines": [
                _line(
                    10,
                    [
                        _branch(1, 2, 1000),
                        _branch(1, 3, 1, fallthrough=True),
                    ],
                ),
                _line(
                    20,
                    [
                        _branch(4, 5, 1),
                        _branch(4, 6, 1, fallthrough=True),
                    ],
                ),
                _line(
                    30,
                    [
                        _branch(7, 8, 10),
                        _branch(7, 9, 0, fallthrough=True),
                    ],
                ),
                _line(
                    40,
                    [
                        _branch(10, 11, 50),
                        _branch(10, 12, 40, fallthrough=True),
                    ],
                ),
            ],
            "functions": [],
        }
    ],
}


@pytest.fixture
def tracefile(gcovr_test_exec: "GcovrTestExec") -> "GcovrTestExec":
    """Write the hand crafted tracefile and return the test executor."""
    (gcovr_test_exec.output_dir / "coverage.json").write_text(
        json.dumps(TRACEFILE), encoding="utf-8"
    )
    return gcovr_test_exec


def test_text_summary(tracefile: "GcovrTestExec") -> None:
    """The text summary reports the branch distribution classification."""
    process = tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--branch-distribution",
        "--txt-summary",
        use_main=True,
    )
    assert process.returncode == 0
    assert (
        "branch distribution: 4 evaluated, 1 insufficient samples, "
        "1 uncovered, 1 skewed, 1 balanced "
        "(minimum samples 10, skew ratio 100)"
    ) in process.stdout
    assert (
        "  skewed: main.c:10 (source block 1, counts 1/1000, total 1001, ratio 1000)"
    ) in process.stdout
    assert (
        "  insufficient samples: main.c:20 (source block 4, counts 1/1, total 2)"
        in process.stdout
    )


def test_disabled_by_default(tracefile: "GcovrTestExec") -> None:
    """Without the option no branch distribution output is produced."""
    process = tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--txt-summary",
        use_main=True,
    )
    assert process.returncode == 0
    assert "branch distribution" not in process.stdout


def test_json_summary(tracefile: "GcovrTestExec") -> None:
    """The JSON summary contains the branch distribution counters."""
    tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--branch-distribution",
        "--json-summary-pretty",
        "--json-summary=coverage_summary.json",
        use_main=True,
    )
    summary = json.loads(
        (tracefile.output_dir / "coverage_summary.json").read_text(encoding="utf-8")
    )
    assert summary["branch_distribution"] == {
        "min_samples": 10,
        "skew_ratio": 100.0,
        "evaluated": 4,
        "insufficient_samples": 1,
        "uncovered": 1,
        "skewed": 1,
        "balanced": 1,
    }


def test_skew_threshold(tracefile: "GcovrTestExec") -> None:
    """A more relaxed skew ratio moves the branch point into the balance."""
    tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--branch-distribution",
        "--branch-distribution-skew-ratio=2000",
        "--json-summary-pretty",
        "--json-summary=coverage_summary.json",
        use_main=True,
    )
    summary = json.loads(
        (tracefile.output_dir / "coverage_summary.json").read_text(encoding="utf-8")
    )
    assert summary["branch_distribution"]["skewed"] == 0
    assert summary["branch_distribution"]["balanced"] == 2  # noqa: PLR2004

    tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--branch-distribution",
        "--branch-distribution-skew-ratio=1.2",
        "--json-summary-pretty",
        "--json-summary=coverage_summary_strict.json",
        use_main=True,
    )
    summary = json.loads(
        (tracefile.output_dir / "coverage_summary_strict.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["branch_distribution"]["skewed"] == 2  # noqa: PLR2004


def test_min_samples_threshold(tracefile: "GcovrTestExec") -> None:
    """Branch points below the minimum sample size are not evaluated."""
    tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--branch-distribution",
        "--branch-distribution-min-samples=2000",
        "--json-summary-pretty",
        "--json-summary=coverage_summary.json",
        use_main=True,
    )
    summary = json.loads(
        (tracefile.output_dir / "coverage_summary.json").read_text(encoding="utf-8")
    )
    assert summary["branch_distribution"]["insufficient_samples"] == 4  # noqa: PLR2004
    assert summary["branch_distribution"]["skewed"] == 0


def test_gate(tracefile: "GcovrTestExec", caplog: pytest.LogCaptureFixture) -> None:
    """The skew threshold can be used as gate with exit status 32."""
    process = tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--fail-under-branch-skew",
        use_main=True,
    )
    assert process.returncode == 32  # noqa: PLR2004
    error_messages = [
        message
        for _logger, level, message in caplog.record_tuples
        if level == logging.ERROR
    ]
    assert len(error_messages) == 1
    assert error_messages[0].startswith("Failed branch distribution skew threshold")
    # The findings are reported as warnings as well.
    warning_messages = [
        message
        for _logger, level, message in caplog.record_tuples
        if level == logging.WARNING
    ]
    assert any("skewed at main.c:10" in message for message in warning_messages)
    assert any(
        "insufficient samples at main.c:20" in message for message in warning_messages
    )
    # The uncovered branch point is not classified as skewed.
    assert not any("main.c:30" in message for message in warning_messages)
    caplog.clear()

    # A relaxed ratio lets the gate pass.
    process = tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--fail-under-branch-skew",
        "--branch-distribution-skew-ratio=2000",
        use_main=True,
    )
    assert process.returncode == 0
    assert not any(
        level == logging.ERROR for _logger, level, _message in caplog.record_tuples
    )
    caplog.clear()

    # With a higher minimum sample size the formerly skewed branch point
    # has insufficient samples and the gate passes.
    process = tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--fail-under-branch-skew",
        "--branch-distribution-min-samples=2000",
        use_main=True,
    )
    assert process.returncode == 0
    assert not any(
        level == logging.ERROR for _logger, level, _message in caplog.record_tuples
    )


def test_gate_is_ored_with_other_exit_codes(tracefile: "GcovrTestExec") -> None:
    """The skew exit status 32 can be ORed with the branch status 4."""
    process = tracefile.gcovr(
        "--json-add-tracefile=coverage.json",
        "--fail-under-branch=100",
        "--fail-under-branch-skew",
        use_main=True,
    )
    assert process.returncode == 4 | 32
