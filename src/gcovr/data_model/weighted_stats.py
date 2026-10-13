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

"""GCOVR weighted data statistics.

The weighted statistics are only computed if the user provides weight
rules with the ``--add-weights`` option.  Each coverage obligation can
be assigned an individual weight (the default weight is ``1.0``), so
that e.g. security critical code contributes more to the coverage
percentage than ordinary code.

The percentage calculation is identical to the one of
:class:`gcovr.data_model.stats.CoverageStat` to make sure that the shown
weighted percentage and the weighted quality gate always use the same
value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

if TYPE_CHECKING:
    from ..options import Options

_T = TypeVar("_T")


@dataclass
class WeightedCoverageStat:
    """A single weighted coverage metric with floating point counts."""

    total: float
    """The weighted number of elements in total."""

    covered: float
    """The weighted number of covered elements."""

    excluded: float
    """The weighted number of excluded elements."""

    @staticmethod
    def new_empty() -> WeightedCoverageStat:
        """Create a empty weighted coverage statistic."""
        return WeightedCoverageStat(total=0.0, covered=0.0, excluded=0.0)

    @property
    def percent(self) -> float | None:
        """Percentage of covered elements, equivalent to `self.percent_or(None)`."""
        return self.percent_or(None)

    def percent_or(self, default: _T) -> float | _T:
        """
        Percentage of covered weighted elements.

        The rounding is identical to :meth:`CoverageStat.percent_or`.
        """
        if not self.total:
            return default

        # Return 100% only if covered == total.
        if self.covered == self.total:
            return 100.0

        # Round to 1 decimal and clamp to max 99.9%.
        ratio = self.covered / self.total
        return min(99.9, round(ratio * 100.0, 1))

    def __iadd__(self, other: WeightedCoverageStat) -> Self:
        """Add another weighted stat object."""
        self.total += other.total
        self.covered += other.covered
        self.excluded += other.excluded
        return self


@dataclass
class WeightedDecisionCoverageStat:
    """A weighted stat for decision coverage (accounts for Uncheckable cases)."""

    total: float
    """The weighted number of elements in total."""

    covered: float
    """The weighted number of covered elements."""

    uncheckable: float
    """The weighted number of uncheckable elements."""

    @classmethod
    def new_empty(cls) -> WeightedDecisionCoverageStat:
        """Create a empty weighted decision coverage statistic."""
        return cls(0.0, 0.0, 0.0)

    @property
    def to_coverage_stat(self) -> WeightedCoverageStat:
        """Convert a weighted decision coverage statistic to a weighted coverage statistic."""
        return WeightedCoverageStat(total=self.total, covered=self.covered, excluded=0.0)

    @property
    def percent(self) -> float | None:
        """Return the percent value of the coverage."""
        return self.to_coverage_stat.percent

    def percent_or(self, default: _T) -> float | _T:
        """Return the percent value of the coverage or the given default if no coverage is present."""
        return self.to_coverage_stat.percent_or(default)

    def __iadd__(self, other: WeightedDecisionCoverageStat) -> Self:
        """Add another weighted decision coverage object."""
        self.covered += other.covered
        self.total += other.total
        self.uncheckable += other.uncheckable
        return self


@dataclass
class WeightedSummarizedStats:
    """Data class for the weighted summarized coverage statistics."""

    line: WeightedCoverageStat
    branch: WeightedCoverageStat
    condition: WeightedCoverageStat
    decision: WeightedDecisionCoverageStat
    function: WeightedCoverageStat
    call: WeightedCoverageStat

    @staticmethod
    def new_empty() -> WeightedSummarizedStats:
        """Create a empty weighted coverage statistic."""
        return WeightedSummarizedStats(
            line=WeightedCoverageStat.new_empty(),
            branch=WeightedCoverageStat.new_empty(),
            condition=WeightedCoverageStat.new_empty(),
            decision=WeightedDecisionCoverageStat.new_empty(),
            function=WeightedCoverageStat.new_empty(),
            call=WeightedCoverageStat.new_empty(),
        )

    def __iadd__(self, other: WeightedSummarizedStats) -> Self:
        """Add another weighted summarized stat."""
        self.line += other.line
        self.branch += other.branch
        self.condition += other.condition
        self.decision += other.decision
        self.function += other.function
        self.call += other.call
        return self

    def serialize(
        self, default_percent: _T, options: Options
    ) -> dict[str, int | float | _T]:
        """Serialize the weighted object, the keys get a ``weighted`` infix."""
        data_dict = dict[str, int | float | _T](
            {
                "line_weighted_total": self.line.total,
                "line_weighted_covered": self.line.covered,
                "line_weighted_percent": self.line.percent_or(default_percent),
                "function_weighted_total": self.function.total,
                "function_weighted_covered": self.function.covered,
                "function_weighted_percent": self.function.percent_or(default_percent),
                "branch_weighted_total": self.branch.total,
                "branch_weighted_covered": self.branch.covered,
                "branch_weighted_percent": self.branch.percent_or(default_percent),
            }
        )
        if self.condition.total != 0:
            data_dict.update(
                {
                    "condition_weighted_total": self.condition.total,
                    "condition_weighted_covered": self.condition.covered,
                    "condition_weighted_percent": self.condition.percent_or(
                        default_percent
                    ),
                }
            )

        if options.show_decision:
            data_dict.update(
                {
                    "decision_weighted_total": self.decision.total,
                    "decision_weighted_covered": self.decision.covered,
                    "decision_weighted_percent": self.decision.percent_or(
                        default_percent
                    ),
                }
            )

        return data_dict
