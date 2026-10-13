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

"""
Risk weighted coverage support.

This module implements the optional weight configuration that allows to
change the contribution of individual coverage obligations to the
coverage statistics.  A weight rule can select obligations by

* the coverage metric (line, branch, condition, decision, function,
  call or ``all``),
* a regular expression matching the root relative file path, and
* line number ranges.

Rules are evaluated in the order they are defined and the first matching
rule wins.  Obligations that do not match any rule get the default
weight of ``1.0`` so that the weighted statistics are identical to the
standard statistics for an empty rule set.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, cast

from .data_model.weighted_stats import (
    WeightedCoverageStat,
    WeightedDecisionCoverageStat,
    WeightedSummarizedStats,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

if TYPE_CHECKING:
    from .data_model.container import CoverageContainer
    from .data_model.coverage import FileCoverage
    from .options import Options

#: Metrics a weight rule can be applied to.
WEIGHT_METRICS = (
    "all",
    "line",
    "branch",
    "condition",
    "decision",
    "function",
    "call",
)

#: The weight that is used if no rule matches.
DEFAULT_WEIGHT = 1.0

_CACHE_ATTRIBUTE = "_gcovr_weight_profile"


class WeightConfigError(ValueError):
    """Raised when a weight configuration file is invalid."""


@dataclass(frozen=True)
class WeightRule:
    """A single rule that assigns a weight to matching obligations."""

    weight: float
    """The weight assigned to matching coverage obligations."""

    metric: str = "all"
    """The metric this rule applies to, ``all`` matches every metric."""

    path_pattern: re.Pattern[str] | None = None
    """If set, the rule only matches files whose path matches this pattern."""

    line_ranges: tuple[tuple[int, int], ...] | None = None
    """If set, the rule only matches the given line number ranges."""

    def matches(self, metric: str, filename: str, lineno: int) -> bool:
        """Check if the rule matches the given coverage obligation."""
        return (
            self.metric in ("all", metric)
            and (
                self.path_pattern is None
                or self.path_pattern.search(filename) is not None
            )
            and (
                self.line_ranges is None
                or any(start <= lineno <= end for start, end in self.line_ranges)
            )
        )


class WeightProfile:
    """An ordered collection of :class:`WeightRule` objects."""

    def __init__(self, rules: Iterable[WeightRule] = ()) -> None:
        """Initialize the weight profile."""
        self.rules = tuple(rules)

    @classmethod
    def from_files(cls, filenames: Iterable[str]) -> WeightProfile:
        """Load and merge the rules from the given TOML configuration files."""
        rules = list[WeightRule]()
        for filename in filenames:
            try:
                with open(filename, "rb") as fh:
                    data = tomllib.load(fh)
            except OSError as exc:
                msg = f"Could not read weights file {filename!r}: {exc.strerror or exc}"
                raise WeightConfigError(msg) from None
            except tomllib.TOMLDecodeError as exc:
                msg = f"Invalid TOML in weights file {filename!r}: {exc}"
                raise WeightConfigError(msg) from None

            rules.extend(_parse_rules(data, filename))

        return cls(rules)

    def weight_for(self, metric: str, filename: str, lineno: int) -> float:
        """Get the weight for the given obligation, the default is 1.0."""
        return next(
            (
                rule.weight
                for rule in self.rules
                if rule.matches(metric, filename, lineno)
            ),
            DEFAULT_WEIGHT,
        )


def _parse_rules(data: dict[str, Any], filename: str) -> list[WeightRule]:
    """Parse the rules of a single weights file."""
    unknown_keys = set(data.keys()) - {"rules"}
    if unknown_keys:
        msg = (
            f"Unknown key(s) {', '.join(sorted(unknown_keys))!r} in weights file "
            f"{filename!r}, only 'rules' is allowed."
        )
        raise WeightConfigError(msg)

    raw_rules = data.get("rules")
    if raw_rules is None:
        return []
    if not isinstance(raw_rules, list) or not raw_rules:
        msg = f"The 'rules' in weights file {filename!r} must be a non empty array of tables."
        raise WeightConfigError(msg)

    rules = list[WeightRule]()
    for index, raw_rule in enumerate(raw_rules, start=1):
        location = f"rule #{index} in weights file {filename!r}"
        if not isinstance(raw_rule, dict):
            msg = f"Each entry of 'rules' in weights file {filename!r} must be a table."
            raise WeightConfigError(msg)

        unknown_keys = set(raw_rule.keys()) - {"weight", "metric", "path", "lines"}
        if unknown_keys:
            msg = (
                f"Unknown key(s) {', '.join(sorted(unknown_keys))!r} in {location}, "
                "allowed keys are 'weight', 'metric', 'path' and 'lines'."
            )
            raise WeightConfigError(msg)

        if "weight" not in raw_rule:
            msg = f"Missing required key 'weight' in {location}."
            raise WeightConfigError(msg)
        weight = raw_rule["weight"]
        # bool is a subclass of int, reject it explicitly.
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            msg = f"The 'weight' in {location} must be a number, got {weight!r}."
            raise WeightConfigError(msg)
        weight = float(weight)
        if weight < 0.0:
            msg = f"The 'weight' in {location} must not be negative, got {weight}."
            raise WeightConfigError(msg)

        metric = raw_rule.get("metric", "all")
        if not isinstance(metric, str) or metric not in WEIGHT_METRICS:
            msg = (
                f"The 'metric' in {location} must be one of "
                f"{', '.join(WEIGHT_METRICS)!r}, got {metric!r}."
            )
            raise WeightConfigError(msg)

        path_pattern = None
        if "path" in raw_rule:
            path = raw_rule["path"]
            if not isinstance(path, str) or not path:
                msg = f"The 'path' in {location} must be a non empty regex string."
                raise WeightConfigError(msg)
            try:
                path_pattern = re.compile(path)
            except re.error as exc:
                msg = f"The 'path' in {location} is not a valid regex: {exc}"
                raise WeightConfigError(msg) from None

        line_ranges = None
        if "lines" in raw_rule:
            line_ranges = _parse_line_ranges(raw_rule["lines"], location)

        rules.append(
            WeightRule(
                weight=weight,
                metric=metric,
                path_pattern=path_pattern,
                line_ranges=line_ranges,
            )
        )

    return rules


def _parse_line_ranges(value: Any, location: str) -> tuple[tuple[int, int], ...]:
    """Parse a line range specification like ``"10-20,42"`` or ``7``."""

    def parse_single(raw_value: Any) -> tuple[int, int]:
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, str)):
            msg = f"The 'lines' in {location} must be integers or ranges, got {raw_value!r}."
            raise WeightConfigError(msg)
        text = str(raw_value).strip()
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", text)
        if match is None:
            msg = f"Invalid line range {raw_value!r} in {location}, expected e.g. '10-20'."
            raise WeightConfigError(msg)
        start = int(match.group(1))
        end = int(match.group(2) or match.group(1))
        if start <= 0 or end <= 0:
            msg = f"Line numbers in {location} must be positive, got {raw_value!r}."
            raise WeightConfigError(msg)
        if start > end:
            msg = f"Invalid line range {raw_value!r} in {location}, start must not exceed end."
            raise WeightConfigError(msg)
        return start, end

    if isinstance(value, list):
        if not value:
            msg = f"The 'lines' in {location} must not be an empty list."
            raise WeightConfigError(msg)
        return tuple(parse_single(entry) for entry in value)

    return tuple(parse_single(part) for part in str(value).split(","))


def get_weight_profile(options: Options) -> WeightProfile | None:
    """
    Get the weight profile configured by the ``--add-weights`` option.

    The result is cached on the options object so that report writers and
    the quality gate share the same parsed profile.
    """
    if hasattr(options, _CACHE_ATTRIBUTE):
        return cast("WeightProfile | None", getattr(options, _CACHE_ATTRIBUTE))

    paths = options.add_weights or []
    profile = WeightProfile.from_files(paths) if paths else None
    setattr(options, _CACHE_ATTRIBUTE, profile)
    return profile


def weighted_file_stats(
    filecov: FileCoverage,
    profile: WeightProfile,
    root_filter: re.Pattern[str],
) -> WeightedSummarizedStats:
    """Calculate the weighted statistics of a single file."""
    filename = filecov.presentable_filename(root_filter)

    line = WeightedCoverageStat.new_empty()
    branch = WeightedCoverageStat.new_empty()
    condition = WeightedCoverageStat.new_empty()
    decision = WeightedDecisionCoverageStat.new_empty()
    call = WeightedCoverageStat.new_empty()

    for linecov in filecov.linecov():
        lineno = linecov.lineno

        line_weight = profile.weight_for("line", filename, lineno)
        if linecov.is_reportable and linecov.is_covered:
            line.covered += line_weight
        if linecov.is_excluded:
            line.excluded += line_weight
        else:
            line.total += line_weight

        branch_weight = profile.weight_for("branch", filename, lineno)
        for branchcov in linecov.branches():
            if branchcov.is_reportable and branchcov.is_covered:
                branch.covered += branch_weight
            if branchcov.is_excluded:
                branch.excluded += branch_weight
            else:
                branch.total += branch_weight

        condition_weight = profile.weight_for("condition", filename, lineno)
        for conditioncov in linecov.conditions():
            if conditioncov.is_reportable:
                condition.covered += condition_weight * conditioncov.covered
            if conditioncov.is_excluded:
                condition.excluded += condition_weight * conditioncov.count
            else:
                condition.total += condition_weight * conditioncov.count

        decision_weight = profile.weight_for("decision", filename, lineno)
        decision_stat = linecov.decision_coverage()
        decision.total += decision_weight * decision_stat.total
        decision.covered += decision_weight * decision_stat.covered
        decision.uncheckable += decision_weight * decision_stat.uncheckable

        call_weight = profile.weight_for("call", filename, lineno)
        for callcov in linecov.calls():
            if callcov.is_reportable and callcov.is_covered:
                call.covered += call_weight
            if callcov.is_excluded:
                call.excluded += call_weight
            else:
                call.total += call_weight

    function = WeightedCoverageStat.new_empty()
    for functioncov in filecov.functioncov():
        for lineno, excluded_function in functioncov.excluded.items():
            function_weight = profile.weight_for("function", filename, lineno)
            if excluded_function:
                function.excluded += function_weight
            else:
                function.total += function_weight
                if (functioncov.execution_count[lineno] or 0) > 0:
                    function.covered += function_weight

    return WeightedSummarizedStats(
        line=line,
        branch=branch,
        condition=condition,
        decision=decision,
        function=function,
        call=call,
    )


def weighted_stats(
    covdata: CoverageContainer,
    profile: WeightProfile,
    root_filter: re.Pattern[str],
) -> WeightedSummarizedStats:
    """Calculate the weighted statistics of all files in the container."""
    stats = WeightedSummarizedStats.new_empty()
    for filecov in covdata.filecov(recurse=True):
        stats += weighted_file_stats(filecov, profile, root_filter)
    return stats
