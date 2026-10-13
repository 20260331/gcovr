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
Component groups for coverage of overlapping file sets and hierarchical components.

A :class:`ComponentGroup` selects files by filters, like the global filters.
The file sets of different components are allowed to overlap. A component can
reference other components as children. The files of a parent component are
the union of its own files and the files of all its descendants. When the
same file belongs to several children, it is counted only once in the parent.
"""

from __future__ import annotations

import re
from argparse import ArgumentTypeError
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .data_model.stats import SummarizedStats
from .filter import Filter, is_file_excluded
from .logging import LOGGER
from .options import ComponentOption

if TYPE_CHECKING:
    from .data_model.container import CoverageContainer
    from .data_model.coverage import FileCoverage

MetricName = Literal["line", "branch", "condition_or_decision", "function"]


@dataclass
class ComponentGroup:
    """A resolved component group with its (hierarchical) set of files."""

    name: str
    """Unique name of the component."""

    include_filter: tuple[Filter, ...]
    """Filters a file must match to be part of the component itself."""

    exclude_filter: tuple[Filter, ...]
    """Filters which exclude a file from the component itself."""

    children: tuple[str, ...]
    """Names of the direct child components."""

    fail_under_line: float
    """Minimum line coverage, 0.0 disables the threshold."""

    fail_under_branch: float
    """Minimum branch coverage, 0.0 disables the threshold."""

    fail_under_condition_or_decision: float
    """Minimum condition or decision coverage, 0.0 disables the threshold."""

    fail_under_function: float
    """Minimum function coverage, 0.0 disables the threshold."""

    files: list[FileCoverage] = field(default_factory=list)
    """The deduplicated file set after :func:`resolve_component_files`."""

    _stats: SummarizedStats | None = field(default=None, init=False, repr=False)

    @property
    def stats(self) -> SummarizedStats:
        """Get the summarized coverage statistics of the component."""
        if self._stats is None:
            stats = SummarizedStats.new_empty()
            for filecov in self.files:
                stats += filecov.stats
            self._stats = stats

        return self._stats

    def reset_stats(self) -> None:
        """Reset the cached coverage statistics."""
        self._stats = None


def build_component_groups(
    definitions: list[ComponentOption],
) -> tuple[ComponentGroup, ...]:
    """
    Build component groups from the option definitions.

    Definitions with the same name are merged. The component hierarchy is
    validated: child components must exist and the hierarchy must not
    contain a cycle.

    Raise a :class:`RuntimeError` if the definitions are invalid.
    """
    merged_definitions = dict[str, ComponentOption]()
    name_order = list[str]()
    for definition in definitions:
        existing = merged_definitions.get(definition.name)
        if existing is None:
            merged_definitions[definition.name] = definition
            name_order.append(definition.name)
        else:
            try:
                existing.merge(definition)
            except ArgumentTypeError as e:
                raise RuntimeError(str(e)) from None

    groups = list[ComponentGroup]()
    for name in name_order:
        definition = merged_definitions[name]
        try:
            include_filter = tuple(f.build_filter() for f in definition.include_filter)
            exclude_filter = tuple(f.build_filter() for f in definition.exclude_filter)
        except re.error as e:
            msg = f"Error setting up filter of component {name!r}='{e.pattern!s}': {e}"
            raise RuntimeError(msg) from None

        groups.append(
            ComponentGroup(
                name=name,
                include_filter=include_filter,
                exclude_filter=exclude_filter,
                children=tuple(dict.fromkeys(definition.components)),
                fail_under_line=definition.fail_under_line,
                fail_under_branch=definition.fail_under_branch,
                fail_under_condition_or_decision=(
                    definition.fail_under_condition_or_decision
                ),
                fail_under_function=definition.fail_under_function,
            )
        )

    groups_by_name = {group.name: group for group in groups}

    for group in groups:
        for child_name in group.children:
            if child_name == group.name:
                msg = (
                    f"Component {group.name!r} must not reference itself "
                    "as a child component."
                )
                raise RuntimeError(msg)
            if child_name not in groups_by_name:
                msg = (
                    f"Component {group.name!r} references the unknown "
                    f"child component {child_name!r}."
                )
                raise RuntimeError(msg)

    _check_for_cycles(groups_by_name, name_order)

    return tuple(groups)


def _check_for_cycles(
    groups_by_name: dict[str, ComponentGroup], name_order: list[str]
) -> None:
    """Check that the component hierarchy contains no cycle."""
    state_unvisited = 0
    state_active = 1
    state_done = 2
    state = dict.fromkeys(name_order, state_unvisited)

    def visit(name: str, path: list[str]) -> None:
        if state[name] == state_active:
            cycle_start = path.index(name)
            cycle = " -> ".join([*path[cycle_start:], name])
            msg = f"Cycle detected in component hierarchy: {cycle}"
            raise RuntimeError(msg)
        if state[name] == state_done:
            return

        state[name] = state_active
        for child_name in groups_by_name[name].children:
            visit(child_name, [*path, name])
        state[name] = state_done

    for name in name_order:
        visit(name, [])


def resolve_component_files(
    groups: tuple[ComponentGroup, ...], covdata: CoverageContainer
) -> None:
    """
    Resolve the file set of every component.

    A component's file set is the union of the files matched by its own
    filters and the file sets of all its child components. Files belonging
    to several components are deduplicated by filename, so a parent never
    counts the same file twice.
    """
    all_files = {filecov.filename: filecov for filecov in covdata.filecov(recurse=True)}

    own_files = dict[str, set[str]]()
    for group in groups:
        matched = set[str]()
        if group.include_filter:
            for filename in all_files:
                if not is_file_excluded(
                    "component",
                    filename,
                    group.include_filter,
                    group.exclude_filter,
                ):
                    matched.add(filename)
        own_files[group.name] = matched

    groups_by_name = {group.name: group for group in groups}
    resolved_files = dict[str, set[str]]()

    def files_of(name: str) -> set[str]:
        if name in resolved_files:
            return resolved_files[name]

        result = set(own_files[name])
        for child_name in groups_by_name[name].children:
            result.update(files_of(child_name))
        resolved_files[name] = result
        return result

    for group in groups:
        filenames = files_of(group.name)
        group.files = sorted(
            (all_files[filename] for filename in filenames),
            key=lambda filecov: filecov.filename,
        )
        group.reset_stats()
        if not group.files:
            LOGGER.warning("Component %r doesn't contain any files.", group.name)


def failing_component_thresholds(
    groups: tuple[ComponentGroup, ...],
) -> set[MetricName]:
    """
    Check the per-component coverage thresholds.

    Log an error for every failed threshold and return the set of metrics
    which failed in at least one component. The semantics match the global
    ``--fail-under-*`` options.
    """
    failed_metrics = set[MetricName]()

    for group in groups:
        stats = group.stats
        file_info = f"Component {group.name!r}: "

        if group.fail_under_line > 0.0:
            # If there are no lines, mark as uncovered
            # (indicates no matching file, likely a configuration error).
            percent_lines = stats.line.percent_or(0.0)
            if percent_lines < group.fail_under_line:
                failed_metrics.add("line")
                LOGGER.error(
                    "%sFailed minimum line coverage (got %s%%, minimum %s%%)",
                    file_info,
                    percent_lines,
                    group.fail_under_line,
                )

        if group.fail_under_branch > 0.0:
            # Allow data with no branches.
            percent_branches = stats.branch.percent_or(100.0)
            if percent_branches < group.fail_under_branch:
                failed_metrics.add("branch")
                LOGGER.error(
                    "%sFailed minimum branch coverage (got %s%%, minimum %s%%)",
                    file_info,
                    percent_branches,
                    group.fail_under_branch,
                )

        if group.fail_under_condition_or_decision > 0.0:
            # Allow data with no conditions.
            percent_condition = stats.condition.percent_or(100.0)
            if percent_condition < group.fail_under_condition_or_decision:
                failed_metrics.add("condition_or_decision")
                LOGGER.error(
                    "%sFailed minimum condition coverage (got %s%%, minimum %s%%)",
                    file_info,
                    percent_condition,
                    group.fail_under_condition_or_decision,
                )

            # Allow data with no decisions.
            percent_decision = stats.decision.percent_or(100.0)
            if percent_decision < group.fail_under_condition_or_decision:
                failed_metrics.add("condition_or_decision")
                LOGGER.error(
                    "%sFailed minimum decision coverage (got %s%%, minimum %s%%)",
                    file_info,
                    percent_decision,
                    group.fail_under_condition_or_decision,
                )

        if group.fail_under_function > 0.0:
            # Allow data with no functions.
            percent_function = stats.function.percent_or(100.0)
            if percent_function < group.fail_under_function:
                failed_metrics.add("function")
                LOGGER.error(
                    "%sFailed minimum function coverage (got %s%%, minimum %s%%)",
                    file_info,
                    percent_function,
                    group.fail_under_function,
                )

    return failed_metrics
