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

"""End to end tests for the source bundle export and import."""

import hashlib
import json
import typing
import zipfile
from pathlib import Path

import pytest

from gcovr.source_bundle import (
    BUNDLE_FORMAT_VERSION,
    HASH_ALGORITHM,
    MANIFEST_FILENAME,
    SOURCES_PREFIX,
    SourceBundleReader,
    write_source_bundle,
)

if typing.TYPE_CHECKING:
    from tests.conftest import GcovrTestExec


ORIGINAL_MARKER = "GCOVR_SOURCE_BUNDLE_MARKER"
TAMPERED_MARKER = "GCOVR_TAMPERED_LOCAL_FILE"


def test_source_bundle_end_to_end(gcovr_test_exec: "GcovrTestExec") -> None:
    """Export a source bundle and use it on a machine without the source code."""
    gcovr_test_exec.cxx_link(
        "testcase",
        gcovr_test_exec.cxx_compile("main.cpp"),
    )
    gcovr_test_exec.run("./testcase")

    output_dir = gcovr_test_exec.output_dir

    # On the build machine: generate the JSON tracefile together with the
    # source bundle.
    gcovr_test_exec.gcovr(
        "--json-pretty",
        "--json=coverage.json",
        "--source-bundle=sources.zip",
        use_main=True,
    )

    # Check the structure and the integrity information of the bundle.
    with zipfile.ZipFile(output_dir / "sources.zip") as archive:
        manifest = json.loads(archive.read(MANIFEST_FILENAME).decode("utf-8"))
        assert manifest["gcovr/bundle_format_version"] == BUNDLE_FORMAT_VERSION
        assert manifest["hash_algorithm"] == HASH_ALGORITHM
        assert [entry["file"] for entry in manifest["files"]] == ["main.cpp"]
        entry = manifest["files"][0]
        source_data = (output_dir / "main.cpp").read_bytes()
        assert entry["size"] == len(source_data)
        assert (
            entry["digest"]
            == hashlib.new(
                HASH_ALGORITHM, source_data, usedforsecurity=False
            ).hexdigest()
        )
        assert archive.namelist() == [
            SOURCES_PREFIX + entry["digest"],
            MANIFEST_FILENAME,
        ]
        # The bundle is bound to the generated JSON tracefile.
        assert len(manifest["coverage_inputs"]) == 1
        coverage_data = (output_dir / "coverage.json").read_bytes()
        assert (
            manifest["coverage_inputs"][0]["digest"]
            == hashlib.new(
                HASH_ALGORITHM, coverage_data, usedforsecurity=False
            ).hexdigest()
        )

    # Simulate the reporting machine which has no source code checkout:
    # only the tracefile and the bundle are copied to an empty directory.
    report_dir = output_dir / "report"
    report_dir.mkdir()
    (report_dir / "coverage.json").write_bytes(coverage_data)
    (report_dir / "sources.zip").write_bytes((output_dir / "sources.zip").read_bytes())
    # A local file with the same name but different content exists. It must
    # never be opened when a source bundle is used.
    (report_dir / "main.cpp").write_text(
        "#include <iostream>\n"
        "int main() {\n"
        f'    std::cout << "{TAMPERED_MARKER}" << std::endl;\n'
        "    return 1;\n"
        "}\n",
        encoding="utf-8",
    )

    result = gcovr_test_exec.gcovr(
        "-r",
        ".",
        "-a",
        "coverage.json",
        "--html-details=coverage.html",
        "--use-source-bundle=sources.zip",
        cwd=report_dir,
        use_main=True,
    )
    assert result.returncode == 0

    detail_pages = list(report_dir.glob("coverage.main.cpp.*.html"))
    assert len(detail_pages) == 1
    detail_content = detail_pages[0].read_text(encoding="utf-8")
    assert ORIGINAL_MARKER in detail_content
    assert TAMPERED_MARKER not in detail_content


def test_source_bundle_rejects_tampered_content(
    gcovr_test_exec: "GcovrTestExec",
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bundle with modified source content fails the integrity check."""
    gcovr_test_exec.cxx_link(
        "testcase",
        gcovr_test_exec.cxx_compile("main.cpp"),
    )
    gcovr_test_exec.run("./testcase")
    gcovr_test_exec.gcovr(
        "--json",
        "coverage.json",
        "--source-bundle=sources.zip",
        use_main=True,
    )

    tampered_bundle = gcovr_test_exec.output_dir / "tampered.zip"
    with (
        zipfile.ZipFile(gcovr_test_exec.output_dir / "sources.zip") as source_archive,
        zipfile.ZipFile(tampered_bundle, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for info in source_archive.infolist():
            data = source_archive.read(info.filename)
            if info.filename.startswith(SOURCES_PREFIX):
                data = data.replace(b"42", b"23")
            target_archive.writestr(info, data)

    result = gcovr_test_exec.gcovr(
        "-a",
        "coverage.json",
        "--html-details=coverage.html",
        f"--use-source-bundle={tampered_bundle}",
        use_main=True,
    )
    assert result.returncode == 64  # noqa: PLR2004
    assert "Integrity check failed" in caplog.text


def test_source_bundle_rejects_foreign_coverage_data(
    gcovr_test_exec: "GcovrTestExec",
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bundle can't be used with coverage data it was not created for."""
    gcovr_test_exec.cxx_link(
        "testcase",
        gcovr_test_exec.cxx_compile("main.cpp"),
    )
    gcovr_test_exec.run("./testcase")
    gcovr_test_exec.gcovr(
        "--json",
        "coverage.json",
        "--source-bundle=sources.zip",
        use_main=True,
    )

    coverage_data = json.loads(
        (gcovr_test_exec.output_dir / "coverage.json").read_text(encoding="utf-8")
    )
    coverage_data["files"][0]["lines"][0]["count"] = 999
    (gcovr_test_exec.output_dir / "coverage_modified.json").write_text(
        json.dumps(coverage_data), encoding="utf-8"
    )

    result = gcovr_test_exec.gcovr(
        "-a",
        "coverage_modified.json",
        "--txt",
        "--use-source-bundle=sources.zip",
        use_main=True,
    )
    assert result.returncode == 64  # noqa: PLR2004
    assert "doesn't match the source bundle" in caplog.text


def test_source_bundle_option_validation(
    gcovr_test_exec: "GcovrTestExec",
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid combinations of the source bundle options are rejected."""
    # The input file is checked for existence during the argument parsing.
    (gcovr_test_exec.output_dir / "sources.zip").write_bytes(b"")
    result = gcovr_test_exec.gcovr(
        "--txt",
        "--use-source-bundle=sources.zip",
        use_main=True,
    )
    assert result.returncode == 1
    assert "--use-source-bundle can only be used together" in caplog.text

    result = gcovr_test_exec.gcovr(
        "-a",
        "coverage.json",
        "--source-bundle=exported.zip",
        "--use-source-bundle=sources.zip",
        use_main=True,
    )
    assert result.returncode == 1
    assert "can not be used together" in caplog.text


def test_source_bundle_writer_roundtrip(tmp_path: Path) -> None:
    """The source bundle API can write and read back source files."""
    first = tmp_path / "first.cpp"
    second = tmp_path / "second.cpp"
    third = tmp_path / "third.cpp"
    first.write_text("first content\n", encoding="utf-8")
    second.write_text("second content\n", encoding="utf-8")
    # Same content as the second file, it has to be stored only once.
    third.write_text("second content\n", encoding="utf-8")

    bundle_path = tmp_path / "sources.zip"
    write_source_bundle(
        str(bundle_path),
        [
            ("src/first.cpp", str(first)),
            ("src/second.cpp", str(second)),
            ("src/third.cpp", str(third)),
        ],
    )

    with zipfile.ZipFile(bundle_path) as archive:
        # Two different digests, the manifest and the two source members.
        assert len(archive.namelist()) == 3  # noqa: PLR2004

    with SourceBundleReader(str(bundle_path)) as reader:
        assert "src/first.cpp" in reader
        assert reader.read_source("src/first.cpp") == b"first content\n"
        assert reader.read_source("src/second.cpp") == b"second content\n"
        with pytest.raises(KeyError):
            reader.read_source("src/missing.cpp")

        # The binding check is skipped without recorded coverage inputs.
        reader.verify_coverage_inputs([])


def test_source_bundle_writer_missing_file(tmp_path: Path) -> None:
    """An unreadable source file stops the bundle export."""
    with pytest.raises(RuntimeError, match="could not be read"):
        write_source_bundle(
            str(tmp_path / "sources.zip"),
            [("missing.cpp", str(tmp_path / "missing.cpp"))],
        )
    assert not (tmp_path / "sources.zip").exists()


def test_source_bundle_reader_validation(tmp_path: Path) -> None:
    """Invalid bundles are rejected."""
    not_a_bundle = tmp_path / "broken.zip"
    not_a_bundle.write_text("not a zip file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Can't open source bundle"):
        SourceBundleReader(str(not_a_bundle))

    with pytest.raises(RuntimeError):
        SourceBundleReader(str(tmp_path / "does_not_exist.zip"))

    # Coverage data which was not recorded is rejected if the bundle
    # contains coverage information.
    source = tmp_path / "main.cpp"
    source.write_text("content\n", encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text("coverage\n", encoding="utf-8")
    bundle_path = tmp_path / "sources.zip"
    write_source_bundle(
        str(bundle_path),
        [("main.cpp", str(source))],
        coverage_inputs=[str(coverage)],
    )
    foreign_coverage = tmp_path / "foreign.json"
    foreign_coverage.write_text("other coverage\n", encoding="utf-8")
    with SourceBundleReader(str(bundle_path)) as reader:
        reader.verify_coverage_inputs([str(coverage)])
        with pytest.raises(RuntimeError, match="doesn't match the source bundle"):
            reader.verify_coverage_inputs([str(foreign_coverage)])
