.. _multiple output formats:

Multiple Output Formats
=======================

You can write multiple report formats with one gcovr invocation
by passing the output filename directly to the report format flag.
If no filename is specified for the format,
the value from :option:`-o/--output<gcovr --output>` is used by default,
which itself defaults to stdout.

The following report format flags can take an optional output file name:

- :option:`gcovr --txt`
- :option:`gcovr --html`
- :option:`gcovr --html-details`
- :option:`gcovr --html-nested`
- :option:`gcovr --csv`
- :option:`gcovr --json`
- :option:`gcovr --json-summary`
- :option:`gcovr --clover`
- :option:`gcovr --cobertura`
- :option:`gcovr --coveralls`
- :option:`gcovr --jacoco`
- :option:`gcovr --lcov`
- :option:`gcovr --sonarqube`

If the value given to the output option ends with a path separator (``/`` or ``\``)
it is used a directory which is created first and a default filename depending
on the format is used.

Note that :option:`--html-details<gcovr --html-details>` and
:option:`--html-nested<gcovr --html-nested>` override any value of
:option:`--html<gcovr --html>` if it is present.

Transactional publishing
-------------------------

By default, each report is written directly to its destination.
If one of several reports cannot be written, the output directory therefore
contains a mix of previous reports and new, possibly incomplete reports.

Use the :option:`gcovr --output-transaction` option to avoid this.
All reports are then generated in a private staging directory and are moved
to their destinations only after every report has been written successfully.
If a report fails or the process is interrupted, the reports of this run are
discarded and the output directories keep their state from before the run.
Reports destined for stdout are buffered and emitted only on success.
