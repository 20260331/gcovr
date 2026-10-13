.. _source bundle:

Source Bundles for Offline Report Generation
=============================================

The annotated HTML reports (see :option:`gcovr --html-details` and
:option:`gcovr --html-nested`) embed the source code of every covered file.
The source code is read when the report is generated.  This is a problem in
a two step setup where the coverage data is collected on a build machine but
the reports are generated on a different machine that has no checkout of the
source code: the source pages of the HTML report then only contain error
placeholders instead of the annotated source code.

A *source bundle* solves this: it is a ZIP archive that contains the source
files of the report together with integrity information.

Exporting a source bundle
-------------------------

On the build machine, add :option:`gcovr --source-bundle` to the gcovr
invocation that generates the JSON intermediate report:

.. code-block:: bash

  gcovr --json-pretty --json coverage.json --source-bundle sources.zip

The bundle contains all source files which are part of the final report
(after all filters have been applied).  Its ``manifest.json`` records for
every source file:

- the root relative filename,
- the size in bytes,
- the SHA-256 digest of the content,
- the gcovr version and the format version of the bundle.

If the JSON report is generated in the same invocation, its size and digest
are recorded as well.  Equal file contents are stored only once.
If a source file can't be read, the export fails instead of writing an
incomplete bundle.

Generating the report on the reporting machine
----------------------------------------------

Copy the JSON tracefile and the source bundle to the reporting machine and
generate the report there with :option:`gcovr --use-source-bundle`:

.. code-block:: bash

  gcovr \
      --json-add-tracefile coverage.json \
      --html-details coverage.html \
      --use-source-bundle sources.zip

When this option is given, the source code is read exclusively from the
bundle.  Local files with the same name are never opened, so a different
checkout on the reporting machine can't accidentally be mixed into the
report.

Before the reports are written, the bundle is verified:

- the SHA-256 digest and the size of every bundled file are checked,
- the coverage input files are compared with the digests recorded in the
  bundle, if they were recorded during the export.

The report generation is aborted if a verification fails or if the bundle
was created for different coverage data.  A source file that is referenced by
the coverage data but is not part of the bundle is reported as not found,
just like a missing local source file.

The option can also be used together with
:option:`gcovr --cobertura-add-tracefile`, and
:option:`gcovr --source-bundle` and :option:`gcovr --use-source-bundle` can
not be used in the same invocation.
