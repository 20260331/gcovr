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

"""Unit tests for the risk weighted coverage feature."""

import re
from pathlib import Path
from typing import Callable

import pytest

from gcovr.data_model.coverage import (
    DecisionCoverageConditional,
    FileCoverage,
)
from gcovr.data_model.weighted_stats import (
    WeightedCoverageStat,
    WeightedSummarizedStats,
)
from gcovr.options import Options
from gcovr.weights import (
    DEFAULT_WEIGHT,
    WeightConfigError,
    WeightProfile,
    WeightRule,
    get_weight_profile,
    weighted_file_stats,
)

ROOT_FILTER = re.compile(r"^/root/")

# Weights used in the tests.
WEIGHT_HIGH = 10.0
WEIGHT_MEDIUM = 5.0
WEIGHT_LOW = 2.0
WEIGHT_BRANCH = 4.0
WEIGHT_LINE_RANGE = 3.0
WEIGHT_DECISION = 3.0
WEIGHT_CALL = 2.0
WEIGHT_FUNCTION = 5.0
WEIGHT_HALF = 0.5
RULE_COUNT = 2


@pytest.fixture
def weights_file(tmp_path: Path) -> Callable[[str], str]:
    """Write a weights file with a unique name and return its path."""
    counter = 0

    def _write(content: str) -> str:
        nonlocal counter
        counter += 1
        path = tmp_path / f"weights-{counter}.toml"
        path.write_text(content, encoding="utf-8")
        return str(path)

    return _write


def test_default_weight_is_one() -> None:
    """Without rules every obligation gets weight 1.0."""
    profile = WeightProfile()
    assert profile.weight_for("line", "a.c", 1) == DEFAULT_WEIGHT
    assert profile.weight_for("branch", "a.c", 2) == DEFAULT_WEIGHT


def test_first_matching_rule_wins() -> None:
    """Rules are evaluated in order, the first match wins."""
    profile = WeightProfile(
        [
            WeightRule(weight=WEIGHT_HIGH, path_pattern=re.compile("crypto")),
            WeightRule(weight=WEIGHT_MEDIUM),
        ]
    )
    assert profile.weight_for("line", "src/crypto/a.c", 1) == WEIGHT_HIGH  # noqa: PLR2004
    assert profile.weight_for("line", "src/main.c", 1) == WEIGHT_MEDIUM  # noqa: PLR2004


def test_metric_selector() -> None:
    """A metric specific rule only applies to that metric."""
    profile = WeightProfile(
        [
            WeightRule(weight=WEIGHT_HIGH, metric="line"),
            WeightRule(weight=WEIGHT_LOW, metric="branch"),
        ]
    )
    assert profile.weight_for("line", "a.c", 1) == WEIGHT_HIGH  # noqa: PLR2004
    assert profile.weight_for("branch", "a.c", 1) == WEIGHT_LOW  # noqa: PLR2004
    # No matching rule for other metrics -> default.
    assert profile.weight_for("function", "a.c", 1) == 1.0


def test_line_range_selector() -> None:
    """The line selector supports single lines and inclusive ranges."""
    profile = WeightProfile(
        [WeightRule(weight=WEIGHT_LINE_RANGE, line_ranges=((10, 12), (20, 20)))]
    )
    assert profile.weight_for("line", "a.c", 9) == 1.0
    assert profile.weight_for("line", "a.c", 10) == WEIGHT_LINE_RANGE  # noqa: PLR2004
    assert profile.weight_for("line", "a.c", 12) == WEIGHT_LINE_RANGE  # noqa: PLR2004
    assert profile.weight_for("line", "a.c", 13) == 1.0
    assert profile.weight_for("line", "a.c", 20) == WEIGHT_LINE_RANGE  # noqa: PLR2004


def test_load_rules_from_toml(weights_file: Callable[[str], str]) -> None:
    """A valid TOML file is parsed into ordered rules."""
    path = weights_file(
        """
        [[rules]]
        weight = 10
        metric = "line"
        path = '.*crypto.*'
        lines = "10-12,20"

        [[rules]]
        weight = 0.5
        lines = [1, "3-4"]
        """
    )
    profile = WeightProfile.from_files([path])
    assert len(profile.rules) == RULE_COUNT  # noqa: PLR2004
    first, second = profile.rules
    assert first.weight == WEIGHT_HIGH  # noqa: PLR2004
    assert first.metric == "line"
    assert first.path_pattern is not None
    assert first.line_ranges == ((10, 12), (20, 20))
    assert second.line_ranges == ((1, 1), (3, 4))
    assert profile.weight_for("line", "crypto/a.c", 11) == WEIGHT_HIGH  # noqa: PLR2004
    assert profile.weight_for("branch", "crypto/a.c", 11) == 1.0
    assert profile.weight_for("branch", "main.c", 3) == WEIGHT_HALF  # noqa: PLR2004


def test_rules_of_several_files_are_concatenated(
    weights_file: Callable[[str], str],
) -> None:
    """Rules from multiple files are appended in the given order."""
    first = weights_file(
        '[[rules]]\nweight = 7.0\npath = "a.c"\n',
    )
    second_path = weights_file(
        '[[rules]]\nweight = 9.0\npath = "b.c"\n',
    )
    profile = WeightProfile.from_files([first, second_path])
    assert [rule.weight for rule in profile.rules] == [7.0, 9.0]  # noqa: PLR2004


@pytest.mark.parametrize(
    "content",
    [
        '[[rules]]\nmetric = "line"\n',  # missing weight
        "[[rules]]\nweight = -1\n",  # negative weight
        '[[rules]]\nweight = "heavy"\n',  # not a number
        '[[rules]]\nweight = 1\nmetric = "unknown"\n',  # bad metric
        '[[rules]]\nweight = 1\npath = "("\n',  # invalid regex
        '[[rules]]\nweight = 1\npath = ""\n',  # empty path
        '[[rules]]\nweight = 1\nlines = "0"\n',  # non positive line
        '[[rules]]\nweight = 1\nlines = "20-10"\n',  # inverted range
        '[[rules]]\nweight = 1\nlines = "x"\n',  # bad range
        "[[rules]]\nweight = 1\nunknown = 2\n",  # unknown rule key
        "unknown = 1\n",  # unknown top level key
        "rules = []\n",  # empty rules
    ],
)
def test_invalid_weights_file(
    weights_file: Callable[[str], str], content: str
) -> None:
    """Invalid weight files raise a WeightConfigError with context."""
    path = weights_file(content)
    with pytest.raises(WeightConfigError):
        WeightProfile.from_files([path])


def test_missing_weights_file() -> None:
    """A missing weight file raises a WeightConfigError."""
    with pytest.raises(WeightConfigError):
        WeightProfile.from_files(["/does/not/exist/weights.toml"])


def test_invalid_toml(weights_file: Callable[[str], str]) -> None:
    """Syntactically invalid TOML is reported."""
    path = weights_file("[[rules\nweight = 1\n")
    with pytest.raises(WeightConfigError):
        WeightProfile.from_files([path])


def _build_filecov() -> FileCoverage:
    """Build an object with two lines, branches, a condition, decision, call and function."""
    filecov = FileCoverage("test.gcov", filename="/root/src/critical.c")

    line10 = filecov.insert_line_coverage(
        "test.gcov", lineno=10, count=3, function_name="critical"
    )
    line10.insert_branch_coverage("test.gcov", branchno=0, count=1)
    line10.insert_branch_coverage("test.gcov", branchno=1, count=0)
    line10.insert_condition_coverage(
        "test.gcov",
        conditionno=0,
        count=2,
        covered=1,
        not_covered_true=[1],
        not_covered_false=[],
    )
    line10.insert_decision_coverage(
        DecisionCoverageConditional(
            line10, "test.gcov", count_true=1, count_false=0
        )
    )
    line10.insert_call_coverage(
        "test.gcov", callno=0, source_block_id=0, destination_block_id=None, returned=1
    )

    line20 = filecov.insert_line_coverage(
        "test.gcov", lineno=20, count=0, function_name="other"
    )
    line20.insert_branch_coverage("test.gcov", branchno=0, count=0)
    line20.insert_branch_coverage("test.gcov", branchno=1, count=0)

    filecov.insert_function_coverage(
        "test.gcov",
        mangled_name="critical",
        demangled_name=None,
        lineno=10,
        count=3,
        blocks=None,
    )
    filecov.insert_function_coverage(
        "test.gcov",
        mangled_name="other",
        demangled_name=None,
        lineno=20,
        count=None,
        blocks=None,
    )

    return filecov


def test_weighted_stats_without_rules_match_standard_stats() -> None:
    """Default weight 1.0 makes weighted stats identical to the standard stats."""
    filecov = _build_filecov()
    weighted = weighted_file_stats(filecov, WeightProfile(), ROOT_FILTER)
    standard = filecov.stats

    assert weighted.line.total == float(standard.line.total)
    assert weighted.line.covered == float(standard.line.covered)
    assert weighted.branch.total == float(standard.branch.total)
    assert weighted.branch.covered == float(standard.branch.covered)
    assert weighted.condition.total == float(standard.condition.total)
    assert weighted.condition.covered == float(standard.condition.covered)
    assert weighted.function.total == float(standard.function.total)
    assert weighted.function.covered == float(standard.function.covered)
    assert weighted.call.total == float(standard.call.total)
    assert weighted.call.covered == float(standard.call.covered)
    assert weighted.decision.total == float(standard.decision.total)
    assert weighted.decision.covered == float(standard.decision.covered)
    assert weighted.line.percent == standard.line.percent


def test_weighted_stats_apply_weights_per_metric_and_line() -> None:
    """Weights are applied per metric and per line."""
    filecov = _build_filecov()
    profile = WeightProfile(
        [
            # Only line 10 of the critical file, only for line coverage.
            WeightRule(
                weight=WEIGHT_HIGH,
                metric="line",
                path_pattern=re.compile("critical"),
                line_ranges=((10, 10),),
            ),
            # All branches in the critical file get weight 4.
            WeightRule(
                weight=WEIGHT_BRANCH,
                metric="branch",
                path_pattern=re.compile("critical"),
            ),
            # Everything else default.
        ]
    )
    weighted = weighted_file_stats(filecov, profile, ROOT_FILTER)

    # Line metric: line 10 covered contributes 10, line 20 uncovered contributes 1.
    assert weighted.line.total == 11.0  # noqa: PLR2004
    assert weighted.line.covered == WEIGHT_HIGH  # noqa: PLR2004
    # 10/11 -> 90.9%.
    assert weighted.line.percent == 90.9  # noqa: PLR2004

    # Branch metric: four branches each with weight 4 -> total 16, one covered -> 4.
    assert weighted.branch.total == 16.0  # noqa: PLR2004
    assert weighted.branch.covered == WEIGHT_BRANCH  # noqa: PLR2004
    assert weighted.branch.percent == 25.0  # noqa: PLR2004

    # Other metrics stay unweighted.
    assert weighted.condition.total == WEIGHT_LOW  # noqa: PLR2004
    assert weighted.condition.covered == 1.0
    assert weighted.function.total == WEIGHT_LOW  # noqa: PLR2004
    assert weighted.function.covered == 1.0
    assert weighted.call.total == 1.0
    assert weighted.call.covered == 1.0


def test_zero_weight_removes_contribution() -> None:
    """A weight of zero removes obligations from the weighted statistics."""
    filecov = _build_filecov()
    profile = WeightProfile([WeightRule(weight=0.0)])
    weighted = weighted_file_stats(filecov, profile, ROOT_FILTER)
    assert weighted.line.total == 0.0
    assert weighted.line.covered == 0.0
    # No weighted obligations -> default percentage is returned.
    assert weighted.line.percent_or(0.0) == 0.0
    assert weighted.branch.percent_or(100.0) == 100.0  # noqa: PLR2004


def test_decision_and_call_weights() -> None:
    """Weights also apply to decisions and calls."""
    filecov = _build_filecov()
    profile = WeightProfile(
        [
            WeightRule(weight=WEIGHT_DECISION, metric="decision", line_ranges=((10, 10),)),
            WeightRule(weight=WEIGHT_CALL, metric="call", line_ranges=((10, 10),)),
        ]
    )
    weighted = weighted_file_stats(filecov, profile, ROOT_FILTER)
    # Conditional decision: total 2, covered 1.
    assert weighted.decision.total == 6.0  # noqa: PLR2004
    assert weighted.decision.covered == WEIGHT_DECISION  # noqa: PLR2004
    assert weighted.call.total == WEIGHT_CALL  # noqa: PLR2004
    assert weighted.call.covered == WEIGHT_CALL  # noqa: PLR2004


def test_function_weight_uses_definition_line() -> None:
    """Function weights match the function definition line."""
    filecov = _build_filecov()
    profile = WeightProfile(
        [WeightRule(weight=WEIGHT_FUNCTION, metric="function", line_ranges=((10, 10),))]
    )
    weighted = weighted_file_stats(filecov, profile, ROOT_FILTER)
    # Function at line 10 weighted 5 (covered), line 20 default 1 (uncovered).
    assert weighted.function.total == 6.0  # noqa: PLR2004
    assert weighted.function.covered == WEIGHT_FUNCTION  # noqa: PLR2004


def test_weighted_percent_rounding() -> None:
    """Weighted percentage reuses the standard rounding and 99.9% cap."""
    assert WeightedCoverageStat(10000, 1234, 0).percent_or("x") == 12.3  # noqa: PLR2004
    assert WeightedCoverageStat(10000, 9999, 0).percent_or("x") == 99.9  # noqa: PLR2004
    assert WeightedCoverageStat(10000, 10000, 0).percent_or("x") == 100.0  # noqa: PLR2004
    assert WeightedCoverageStat(0, 0, 0).percent_or("x") == "x"


def test_serialized_keys_have_weighted_infix() -> None:
    """Serialization adds a 'weighted' infix and honors condition/decision options."""
    stats = WeightedSummarizedStats.new_empty()
    stats.line.total = WEIGHT_HIGH
    stats.line.covered = 8.0
    data = stats.serialize(0.0, Options(show_decision=False))
    assert data["line_weighted_total"] == WEIGHT_HIGH  # noqa: PLR2004
    assert data["line_weighted_covered"] == 8.0  # noqa: PLR2004
    assert data["line_weighted_percent"] == 80.0  # noqa: PLR2004
    assert "branch_weighted_percent" in data
    assert "function_weighted_percent" in data
    assert "condition_weighted_total" not in data
    assert "decision_weighted_total" not in data

    data_decision = stats.serialize(0.0, Options(show_decision=True))
    assert "decision_weighted_total" in data_decision


def test_get_weight_profile_caching(weights_file: Callable[[str], str]) -> None:
    """The profile is parsed once and cached on the options object."""
    path = weights_file("[[rules]]\nweight = 2.0\n")
    options = Options(add_weights=[path])
    first = get_weight_profile(options)
    second = get_weight_profile(options)
    assert first is second
    assert first.weight_for("line", "a.c", 1) == WEIGHT_LOW  # noqa: PLR2004

    assert get_weight_profile(Options(add_weights=[])) is None
