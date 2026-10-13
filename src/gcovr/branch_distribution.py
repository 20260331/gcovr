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
Branch distribution diagnostics.

Normal branch coverage only records whether each target (outgoing edge)
of a branch point was executed at least once.  This cannot distinguish a
branch that was executed once on each side from a branch that was
executed thousands of times on one side but only once on the other.

This optional diagnostic aggregates the target counts of each branch
point using the already available control flow information:

* targets are grouped by their source block if the source block ID is
  known (GCOV JSON intermediate format and GCOV text output with block
  information),
* otherwise consecutive branch numbers are paired as fallback
  (text output without block information).

Each branch point with at least two reportable targets is classified as

* ``insufficient_samples``: the sum of the target counts is below the
  configured minimum number of samples,
* ``uncovered``: there are enough samples but at least one target was
  never executed (this is already covered by the normal branch
  coverage),
* ``skewed``: there are enough samples, every target was executed and
  the ratio of the largest to the smallest target count is at least the
  configured skew ratio,
* ``balanced``: otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from .logging import LOGGER

if TYPE_CHECKING:
    from .data_model.container import CoverageContainer
    from .data_model.coverage import BranchCoverage, LineCoverage
    from .options import Options


def _classify(
    total: int,
    min_count: int,
    max_count: int,
    min_samples: int,
    skew_ratio: float,
) -> str:
    """
    Classify a branch point from its target counts.

    >>> _classify(2, 1, 1, min_samples=10, skew_ratio=100.0)
    'insufficient_samples'
    >>> _classify(10, 0, 10, min_samples=10, skew_ratio=100.0)
    'uncovered'
    >>> _classify(1001, 1, 1000, min_samples=10, skew_ratio=100.0)
    'skewed'
    >>> _classify(90, 2, 88, min_samples=10, skew_ratio=100.0)
    'balanced'
    """
    if total < min_samples:
        return "insufficient_samples"
    if min_count == 0:
        return "uncovered"
    if (max_count / min_count) >= skew_ratio:
        return "skewed"
    return "balanced"


def _group_branches(
    linecov: LineCoverage,
) -> dict[tuple[str, int], list[BranchCoverage]]:
    """
    Group the reportable targets of a line by their branch point.

    >>> from gcovr.data_model.coverage import FileCoverage
    >>> filecov = FileCoverage("file.gcov", filename="file.c")
    >>> linecov = filecov.insert_line_coverage(
    ...     "file.gcov", lineno=10, count=1, function_name="function"
    ... )
    >>> _ = linecov.insert_branch_coverage(
    ...     "file.gcov", branchno=None, count=1000,
    ...     source_block_id=1, destination_block_id=2
    ... )
    >>> _ = linecov.insert_branch_coverage(
    ...     "file.gcov", branchno=None, count=1,
    ...     source_block_id=1, destination_block_id=3
    ... )
    >>> sorted(
    ...     (key, sorted(branchcov.count for branchcov in group))
    ...     for key, group in _group_branches(linecov).items()
    ... )
    [(('block', 1), [1, 1000])]

    A branch number based fallback is used if no source block is known:

    >>> linecov_without_blocks = filecov.insert_line_coverage(
    ...     "file.gcov", lineno=20, count=1, function_name="function2"
    ... )
    >>> _ = linecov_without_blocks.insert_branch_coverage(
    ...     "file.gcov", branchno=0, count=5
    ... )
    >>> _ = linecov_without_blocks.insert_branch_coverage(
    ...     "file.gcov", branchno=1, count=7
    ... )
    >>> sorted(
    ...     (key, sorted(branchcov.count for branchcov in group))
    ...     for key, group in _group_branches(linecov_without_blocks).items()
    ... )
    [(('pair', 0), [5, 7])]
    """
    groups: dict[tuple[str, int], list[BranchCoverage]] = {}
    for branchcov in linecov.branches():
        if not branchcov.is_reportable:
            continue
        if branchcov.source_block_id is not None:
            key = ("block", branchcov.source_block_id)
        elif branchcov.branchno is not None:
            key = ("pair", branchcov.branchno // 2)
        else:
            continue
        groups.setdefault(key, []).append(branchcov)

    return groups


@dataclass(frozen=True)
class BranchPoint:
    """A single branch point with the counts of all its targets."""

    location: str
    """Source location ``file:line`` of the branch point."""

    group_label: str
    """Human readable identification of the branch point."""

    counts: tuple[int, ...]
    """Target counts sorted in ascending order."""

    @property
    def total(self) -> int:
        """Get the total number of executions of all targets."""
        return sum(self.counts)

    @property
    def min_count(self) -> int:
        """Get the smallest target count."""
        return self.counts[0]

    @property
    def max_count(self) -> int:
        """Get the largest target count."""
        return self.counts[-1]

    @property
    def ratio(self) -> float | None:
        """
        Get the ratio of the largest to the smallest target count.

        If the smallest target count is zero, the ratio is not defined.
        """
        if self.min_count == 0:
            return None
        return self.max_count / self.min_count

    def format_counts(self) -> str:
        """Format the target counts."""
        return "/".join(str(count) for count in self.counts)


@dataclass
class BranchDistributionReport:
    """The aggregated branch distribution diagnostics for a coverage report."""

    min_samples: int
    skew_ratio: float
    evaluated: int = 0
    insufficient_samples: list[BranchPoint] = field(default_factory=list)
    uncovered: list[BranchPoint] = field(default_factory=list)
    skewed: list[BranchPoint] = field(default_factory=list)
    balanced: int = 0

    def add(self, branch_point: BranchPoint) -> None:
        """Add a branch point to the report."""
        self.evaluated += 1
        classification = _classify(
            branch_point.total,
            branch_point.min_count,
            branch_point.max_count,
            min_samples=self.min_samples,
            skew_ratio=self.skew_ratio,
        )
        if classification == "insufficient_samples":
            self.insufficient_samples.append(branch_point)
        elif classification == "uncovered":
            self.uncovered.append(branch_point)
        elif classification == "skewed":
            self.skewed.append(branch_point)
        else:
            self.balanced += 1

    def serialize(self) -> dict[str, Any]:
        """Serialize the counters of the report."""
        return {
            "min_samples": self.min_samples,
            "skew_ratio": self.skew_ratio,
            "evaluated": self.evaluated,
            "insufficient_samples": len(self.insufficient_samples),
            "uncovered": len(self.uncovered),
            "skewed": len(self.skewed),
            "balanced": self.balanced,
        }

    @staticmethod
    def _format_points(points: Iterable[BranchPoint], prefix: str) -> list[str]:
        lines = list[str]()
        for point in sorted(points, key=lambda p: (p.location, p.counts)):
            lines.append(
                f"  {prefix}{point.location} ({point.group_label}, "
                f"counts {point.format_counts()}, total {point.total})"
            )
        return lines

    def as_text(self) -> str:
        """Render the report as text for the summary output."""
        lines = [
            (
                "branch distribution: "
                f"{self.evaluated} evaluated, "
                f"{len(self.insufficient_samples)} insufficient samples, "
                f"{len(self.uncovered)} uncovered, "
                f"{len(self.skewed)} skewed, "
                f"{self.balanced} balanced "
                f"(minimum samples {self.min_samples}, skew ratio {_format_ratio(self.skew_ratio)})"
            )
        ]
        lines.extend(
            self._format_points(self.insufficient_samples, "insufficient samples: ")
        )
        for point in sorted(self.skewed, key=lambda p: (p.location, p.counts)):
            ratio = point.ratio
            ratio_text = "" if ratio is None else f", ratio {_format_ratio(ratio)}"
            lines.append(
                f"  skewed: {point.location} ({point.group_label}, "
                f"counts {point.format_counts()}, total {point.total}{ratio_text})"
            )

        return "\n".join(lines)

    def log_findings(self) -> None:
        """Log the branch points with insufficient samples and with skewed distribution."""
        for point in sorted(self.insufficient_samples, key=lambda p: p.location):
            LOGGER.warning(
                "Branch distribution: insufficient samples at %s (%s, counts %s, total %d, minimum samples %d).",
                point.location,
                point.group_label,
                point.format_counts(),
                point.total,
                self.min_samples,
            )
        for point in sorted(self.skewed, key=lambda p: p.location):
            LOGGER.warning(
                "Branch distribution: skewed at %s (%s, counts %s, ratio %s, skew ratio %s).",
                point.location,
                point.group_label,
                point.format_counts(),
                _format_ratio(point.ratio) if point.ratio is not None else "n/a",
                _format_ratio(self.skew_ratio),
            )


def _format_ratio(value: float) -> str:
    """
    Format a ratio without insignificant trailing zeros.

    >>> _format_ratio(100.0)
    '100'
    >>> _format_ratio(1.5)
    '1.5'
    >>> _format_ratio(1000.0)
    '1000'
    """
    return f"{value:.10g}"


def compute_branch_distribution(
    covdata: CoverageContainer,
    options: Options,
) -> BranchDistributionReport:
    """
    Compute the branch distribution diagnostics for the given coverage data.

    The targets of every reportable branch are grouped by their branch
    point using the available control flow information (source block ID,
    falling back to consecutive branch numbers) and classified.
    """
    report = BranchDistributionReport(
        min_samples=options.branch_distribution_min_samples,
        skew_ratio=options.branch_distribution_skew_ratio,
    )

    for filecov in covdata.filecov(recurse=True):
        filename = filecov.presentable_filename(options.root_filter)
        for linecov in filecov.linecov():
            for (kind, group_id), branchcovs in _group_branches(linecov).items():
                if len(branchcovs) < 2:  # noqa: PLR2004
                    continue
                counts = tuple(sorted(branchcov.count for branchcov in branchcovs))
                group_label = (
                    f"source block {group_id}"
                    if kind == "block"
                    else f"branch pair {group_id}"
                )
                report.add(
                    BranchPoint(
                        location=f"{filename}:{linecov.lineno}",
                        group_label=group_label,
                        counts=counts,
                    )
                )

    return report
