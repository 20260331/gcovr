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

"""Tests for component groups with overlapping file sets and hierarchy."""

from __future__ import annotations

import json
import logging
import typing

if typing.TYPE_CHECKING:
    import subprocess
    from pathlib import Path

    import pytest

    from tests.conftest import GcovrTestExec


# The "shared.cpp" file is part of both leaf components, so it would be
# counted twice in the parent if its files were not deduplicated.
COMPONENTS_TOML = r"""
[[component]]
name = "core"
filter = ['core\.cpp$', 'shared\.cpp$']

[[component]]
name = "network"
filter = ['net\.cpp$', 'shared\.cpp$']

[[component]]
name = "product"
components = ["core", "network"]
"""

THRESHOLDS_TOML = r"""
[[component]]
name = "network"
filter = ['net\.cpp$']
fail-under-line = 100.0

[[component]]
name = "core"
filter = ['core\.cpp$', 'shared\.cpp$']
fail-under-line = 100.0
"""

NETWORK_THRESHOLD_TOML = r"""
[[component]]
name = "network"
filter = ['net\.cpp$']
fail-under-line = 100.0
"""


def _build_and_run(gcovr_test_exec: GcovrTestExec) -> None:
    """Compile and run the test program."""
    objects = [
        gcovr_test_exec.cxx_compile(source)
        for source in ("main.cpp", "shared.cpp", "core.cpp", "net.cpp")
    ]
    gcovr_test_exec.cxx_link("testcase", *objects)
    gcovr_test_exec.run("./testcase")


def _write_config(gcovr_test_exec: GcovrTestExec, content: str) -> Path:
    """Write a gcovr configuration file in the output directory."""
    config = gcovr_test_exec.output_dir / "gcovr.toml"
    config.write_text(content, encoding="utf-8")
    return config


def _run_summary(
    gcovr_test_exec: GcovrTestExec, *extra_args: str
) -> subprocess.CompletedProcess[str]:
    """Run gcovr with text and JSON summary output."""
    return gcovr_test_exec.gcovr(
        *extra_args,
        "--txt-summary",
        "--json-summary",
        "summary_coverage.json",
        use_main=True,
    )


def _components_by_name(summary: dict) -> dict[str, dict]:
    """Index the component entries of a JSON summary by name."""
    return {component["name"]: component for component in summary["components"]}


def test_overlapping_components_and_parent(
    gcovr_test_exec: GcovrTestExec,
) -> None:
    """Overlapping file sets are aggregated without double counting."""
    _build_and_run(gcovr_test_exec)
    config = _write_config(gcovr_test_exec, COMPONENTS_TOML)

    result = _run_summary(gcovr_test_exec, f"--config={config.name}")

    assert result.returncode == 0

    # The text summary reports every component with its file count.
    assert "Component coverage:" in result.stdout
    assert "core (2 files):" in result.stdout
    assert "network (2 files):" in result.stdout
    assert "product (3 files):" in result.stdout

    summary = json.loads(
        (gcovr_test_exec.output_dir / "summary_coverage.json").read_text(
            encoding="utf-8"
        )
    )
    components = _components_by_name(summary)
    assert set(components) == {"core", "network", "product"}

    core = components["core"]
    network = components["network"]
    product = components["product"]

    assert core["files"] == 2  # noqa: PLR2004
    assert network["files"] == 2  # noqa: PLR2004
    # shared.cpp belongs to both children but must appear once in the parent.
    assert product["files"] == 3  # noqa: PLR2004

    shared = next(
        filecov
        for filecov in summary["files"]
        if filecov["filename"].endswith("shared.cpp")
    )

    # The parent statistics are the union of the children, the shared file
    # is counted only once.
    assert (
        product["line_total"]
        == core["line_total"] + network["line_total"] - shared["line_total"]
    )
    assert (
        product["line_covered"]
        == core["line_covered"] + network["line_covered"] - shared["line_covered"]
    )

    # The parent does contain uncovered code (shared.cpp and core.cpp).
    assert product["line_percent"] < 100.0  # noqa: PLR2004


def test_component_threshold_failure(
    gcovr_test_exec: GcovrTestExec, caplog: pytest.LogCaptureFixture
) -> None:
    """A component below its threshold fails with the line exit code."""
    _build_and_run(gcovr_test_exec)
    config = _write_config(gcovr_test_exec, THRESHOLDS_TOML)

    result = _run_summary(gcovr_test_exec, f"--config={config.name}")

    # Exit code 2 is the line coverage failure.
    assert result.returncode == 2  # noqa: PLR2004
    error_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert any("Component 'core'" in msg for msg in error_messages)
    assert any("Failed minimum line coverage" in msg for msg in error_messages)
    # network.cpp is fully covered and must not be reported.
    assert not any("Component 'network'" in msg for msg in error_messages)


def test_component_threshold_pass(
    gcovr_test_exec: GcovrTestExec, caplog: pytest.LogCaptureFixture
) -> None:
    """A component meeting its threshold does not fail."""
    _build_and_run(gcovr_test_exec)
    config = _write_config(gcovr_test_exec, NETWORK_THRESHOLD_TOML)

    result = _run_summary(gcovr_test_exec, f"--config={config.name}")

    assert result.returncode == 0
    assert not any(
        "Failed minimum line coverage" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    )


def test_component_simple_cli_form(gcovr_test_exec: GcovrTestExec) -> None:
    """The simple CLI form creates and merges components by name."""
    _build_and_run(gcovr_test_exec)

    result = _run_summary(
        gcovr_test_exec,
        "--component",
        r"core:core\.cpp$",
        "--component",
        r"core:shared\.cpp$",
        "--component",
        r"network:net\.cpp$",
        "--component",
        r"network:shared\.cpp$",
    )

    assert result.returncode == 0

    summary = json.loads(
        (gcovr_test_exec.output_dir / "summary_coverage.json").read_text(
            encoding="utf-8"
        )
    )
    components = _components_by_name(summary)
    assert components["core"]["files"] == 2  # noqa: PLR2004
    assert components["network"]["files"] == 2  # noqa: PLR2004


def test_component_unknown_child(
    gcovr_test_exec: GcovrTestExec, caplog: pytest.LogCaptureFixture
) -> None:
    """Referencing an unknown child component is a command line error."""
    _build_and_run(gcovr_test_exec)
    config = _write_config(
        gcovr_test_exec,
        r"""
[[component]]
name = "product"
components = ["missing"]
""",
    )

    result = _run_summary(gcovr_test_exec, f"--config={config.name}")

    assert result.returncode == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("unknown" in msg and "'product'" in msg for msg in messages)
    assert any("'missing'" in msg for msg in messages)


def test_component_cycle(
    gcovr_test_exec: GcovrTestExec, caplog: pytest.LogCaptureFixture
) -> None:
    """A cycle in the component hierarchy is a command line error."""
    _build_and_run(gcovr_test_exec)
    config = _write_config(
        gcovr_test_exec,
        r"""
[[component]]
name = "alpha"
components = ["beta"]

[[component]]
name = "beta"
components = ["alpha"]
""",
    )

    result = _run_summary(gcovr_test_exec, f"--config={config.name}")

    assert result.returncode == 1
    assert any("Cycle detected" in record.getMessage() for record in caplog.records)
