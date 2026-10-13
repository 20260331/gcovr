#  ************************** Copyrights and license ***************************
#
# This file is part of gcovr 8.6+main, a parsing and reporting tool for gcov.
# https://gcovr.com/en/stable
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

"""End to end tests for the risk weighted coverage feature."""

import json
import logging
import typing

import pytest

if typing.TYPE_CHECKING:
    from tests.conftest import GcovrTestExec

WEIGHTS_TOML = """
# Everything in the security critical file gets ten times the contribution.
[[rules]]
weight = 10.0
path = '.*critical\\.cpp'
"""


def _write_weights(gcovr_test_exec: "GcovrTestExec") -> str:
    """Write the weights file into the test output directory."""
    path = gcovr_test_exec.output_dir / "weights.toml"
    path.write_text(WEIGHTS_TOML, encoding="utf-8")
    return "weights.toml"


def _expected_weighted(data: dict, metric: str) -> tuple[float, float]:
    """Compute the expected weighted totals from the unweighted per file data."""
    total = 0.0
    covered = 0.0
    for file_entry in data["files"]:
        weight = 10.0 if "critical" in file_entry["filename"] else 1.0
        total += weight * file_entry[f"{metric}_total"]
        covered += weight * file_entry[f"{metric}_covered"]
    return total, covered


def test_weighted_reports_and_gates(
    gcovr_test_exec: "GcovrTestExec", caplog: pytest.LogCaptureFixture
) -> None:
    """Weighted statistics appear in the reports and drive the quality gates."""
    gcovr_test_exec.cxx_link("testcase", "main.cpp", "critical.cpp")
    gcovr_test_exec.run("./testcase")
    weights = _write_weights(gcovr_test_exec)

    # JSON summary with weights.
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        "--json-summary-pretty",
        "--json-summary=summary_weighted.json",
        use_main=True,
    )
    assert process.returncode == 0
    data = json.loads(
        (gcovr_test_exec.output_dir / "summary_weighted.json").read_text(
            encoding="utf-8"
        )
    )

    # Weighted keys are present at root level.
    for key in (
        "line_weighted_total",
        "line_weighted_covered",
        "line_weighted_percent",
        "branch_weighted_total",
        "branch_weighted_covered",
        "branch_weighted_percent",
        "function_weighted_percent",
    ):
        assert key in data

    # The values must match the weights applied to the unweighted per file data.
    for metric in ("line", "branch", "function"):
        total, covered = _expected_weighted(data, metric)
        assert data[f"{metric}_weighted_total"] == total
        assert data[f"{metric}_weighted_covered"] == covered
        if total:
            ratio = covered / total
            expected_percent = 100.0 if covered == total else min(99.9, round(ratio * 100.0, 1))
            assert data[f"{metric}_weighted_percent"] == expected_percent

    # Per file weighted data: the critical file has a ten times weighted total.
    per_file = {entry["filename"]: entry for entry in data["files"]}
    critical_name = next(name for name in per_file if "critical" in name)
    critical = per_file[critical_name]
    assert critical["line_weighted_total"] == 10.0 * critical["line_total"]
    assert critical["line_weighted_covered"] == 10.0 * critical["line_covered"]

    # The uncovered critical line must dilute the weighted coverage much more.
    assert data["line_weighted_percent"] < data["line_percent"]

    # Standard keys are still present and unchanged.
    standard_summary = json.loads(
        (gcovr_test_exec.output_dir / "summary_weighted.json").read_text(
            encoding="utf-8"
        )
    )
    assert "line_percent" in standard_summary

    # JSON summary without weights must not contain weighted keys.
    gcovr_test_exec.gcovr(
        "--json-summary-pretty",
        "--json-summary=summary_plain.json",
        use_main=True,
    )
    plain = json.loads(
        (gcovr_test_exec.output_dir / "summary_plain.json").read_text(
            encoding="utf-8"
        )
    )
    assert "line_weighted_total" not in plain
    assert "line_percent" in plain

    # Text summary contains weighted lines only with weights loaded.
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        "--print-summary",
        use_main=True,
    )
    assert "lines:" in process.stdout
    assert "weighted lines:" in process.stdout
    assert "weighted functions:" in process.stdout
    assert "weighted branches:" in process.stdout

    process = gcovr_test_exec.gcovr("--print-summary", use_main=True)
    assert "weighted lines:" not in process.stdout

    # The full text report gets a WEIGHTED TOTAL row only with weights.
    gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        "--txt=coverage_weighted.txt",
        use_main=True,
    )
    weighted_txt = (
        gcovr_test_exec.output_dir / "coverage_weighted.txt"
    ).read_text(encoding="utf-8")
    assert "WEIGHTED TOTAL" in weighted_txt

    gcovr_test_exec.gcovr("--txt=coverage_plain.txt", use_main=True)
    plain_txt = (gcovr_test_exec.output_dir / "coverage_plain.txt").read_text(
        encoding="utf-8"
    )
    assert "WEIGHTED TOTAL" not in plain_txt

    def assert_gate_error(expected_message: str) -> None:
        messages = [message for _, level, message in caplog.record_tuples if level == logging.ERROR]
        assert any(message.startswith(expected_message) for message in messages), messages

    # Weighted quality gate: a threshold above the weighted coverage fails with 32.
    weighted_percent = data["line_weighted_percent"]
    caplog.clear()
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        f"--fail-under-weighted-line={weighted_percent + 0.1:.1f}",
        use_main=True,
    )
    assert process.returncode == 32  # noqa: PLR2004
    assert_gate_error("Failed minimum weighted line coverage ")

    # A threshold equal to the coverage passes.
    caplog.clear()
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        f"--fail-under-weighted-line={weighted_percent:.1f}",
        use_main=True,
    )
    assert process.returncode == 0
    assert not [
        message
        for _, level, message in caplog.record_tuples
        if level == logging.ERROR and "weighted" in message
    ]

    # The weighted branch gate reports its own metric.
    caplog.clear()
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        f"--fail-under-weighted-branch={data['branch_weighted_percent'] + 0.1:.1f}",
        use_main=True,
    )
    assert process.returncode == 32  # noqa: PLR2004
    assert_gate_error("Failed minimum weighted branch coverage ")

    # Standard gate still uses the unweighted statistics with weights loaded.
    caplog.clear()
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        f"--fail-under-line={data['line_percent'] + 0.1:.1f}",
        use_main=True,
    )
    assert process.returncode == 2  # noqa: PLR2004
    assert_gate_error("Failed minimum line coverage ")

    # Standard and weighted gates can be combined (2 OR 32 == 34).
    caplog.clear()
    process = gcovr_test_exec.gcovr(
        f"--add-weights={weights}",
        f"--fail-under-line={data['line_percent'] + 0.1:.1f}",
        f"--fail-under-weighted-line={weighted_percent + 0.1:.1f}",
        use_main=True,
    )
    assert process.returncode == 34  # noqa: PLR2004
    assert_gate_error("Failed minimum line coverage ")
    assert_gate_error("Failed minimum weighted line coverage ")

    # A weighted threshold without a weights file is a command line error.
    caplog.clear()
    process = gcovr_test_exec.gcovr(
        "--fail-under-weighted-line=50",
        use_main=True,
    )
    assert process.returncode == 1


def test_weights_from_config_file(
    gcovr_test_exec: "GcovrTestExec", caplog: pytest.LogCaptureFixture
) -> None:
    """The weights file and weighted gates can be configured in a gcovr config file."""
    gcovr_test_exec.cxx_link("testcase", "main.cpp", "critical.cpp")
    gcovr_test_exec.run("./testcase")
    _write_weights(gcovr_test_exec)
    (gcovr_test_exec.output_dir / "gcovr.cfg").write_text(
        "add-weights = weights.toml\n"
        "fail-under-weighted-line = 99\n",
        encoding="utf-8",
    )

    # The config file in the root directory is loaded automatically.
    process = gcovr_test_exec.gcovr("--print-summary", use_main=True)
    assert process.returncode == 32  # noqa: PLR2004
    assert "weighted lines:" in process.stdout
    messages = [message for _, level, message in caplog.record_tuples if level == logging.ERROR]
    assert any(
        message.startswith("Failed minimum weighted line coverage ") for message in messages
    ), messages
