.. _weighted_coverage:

Risk Weighted Coverage
======================

.. warning::
    Risk weighted coverage is an experimental feature
    and may be subject to change without prior notice.

By default, every coverage obligation (a line, a branch outcome, a
condition outcome, a decision, a function or a call) contributes
equally to the coverage statistics.  As a consequence, a few uncovered
obligations in security critical code can be hidden by a large amount
of well tested ordinary code.

The :option:`--add-weights` option loads a TOML configuration file with
weight rules.  Each matching obligation is multiplied with its weight
when the **weighted** statistics and the **weighted quality gates** are
calculated.  The standard statistics and reports are never changed:
without :option:`--add-weights`, gcovr behaves exactly as before.

The option can be given multiple times.  Rules from several files are
concatenated in the order of the command line (or configuration file).

.. contents::
   :local:
   :depth: 2

Weight file format
------------------

The weight file is a TOML file that contains an array of ``[[rules]]``
tables.  Every rule has a mandatory ``weight`` and three optional
selectors:

``weight`` (mandatory)
    A non negative number.  A weight of ``0`` removes the matching
    obligations from the weighted statistics (this is different from an
    :ref:`exclusion marker <exclusion markers>` which also removes them
    from the standard statistics).

``metric`` (optional)
    One of ``all`` (the default), ``line``, ``branch``, ``condition``,
    ``decision``, ``function`` or ``call``.  The rule only applies to
    obligations of this metric.

``path`` (optional)
    A regular expression that is searched in the root relative file
    path (forward slashes are used, even on Windows).  If omitted, the
    rule matches all files.

``lines`` (optional)
    A line number or a comma separated list of line ranges, e.g.
    ``7`` or ``"10-20,42"``.  TOML arrays such as ``[10, 12, "20-30"]``
    are also accepted.  The selector is evaluated against the line the
    obligation belongs to (for functions this is the line of the
    function definition).

Rules are evaluated in order and the **first matching rule wins**.
Obligations that do not match any rule get the default weight of
``1.0``, which makes the weighted statistics identical to the standard
statistics.

Example
-------

.. code-block:: toml

    # Everything in the crypto package is ten times as important.
    [[rules]]
    weight = 10.0
    path = '.*src/crypto/.*'

    # The authentication state machine: only line coverage, lines 40-120.
    [[rules]]
    weight = 20.0
    metric = "line"
    path = '.*src/auth/state_machine\\.c$'
    lines = "40-120"

    # Generated code does not contribute at all.
    [[rules]]
    weight = 0.0
    path = '.*(generated|third_party)/.*'

Weighted statistics in the reports
----------------------------------

When weights are loaded, the following reports contain weighted values
in addition to the standard values:

- The summary printed by :option:`--txt-summary`
  shows a ``weighted lines:`` (and functions, branches, ...) line right
  after each standard line.
- The text report (the default report or :option:`--txt`) shows an
  additional ``WEIGHTED TOTAL`` row in the footer.
- The JSON summary (:option:`--json-summary`) contains additional keys
  such as ``line_weighted_total``, ``line_weighted_covered`` and
  ``line_weighted_percent``, both per file and in the global summary.

The percentage is calculated with the same rules as for the standard
statistics (one decimal place, capped at 99.9% unless everything is
covered).

Weighted quality gates
----------------------

The weighted statistics can be enforced with their own quality gates:

- :option:`--fail-under-weighted-line`
- :option:`--fail-under-weighted-branch`
- :option:`--fail-under-weighted-condition-or-decision`
- :option:`--fail-under-weighted-function`

All weighted gates add the exit status ``32`` if the weighted
coverage is below the given minimum.  The error messages state which
weighted metric failed.  The weighted gates are independent of the
standard :option:`--fail-under-line` family of options and their exit
statuses can be combined (ORed).

Weighted thresholds require a weight file loaded with
:option:`--add-weights`.

Configuration files
-------------------

The weight rules themselves are always loaded from a dedicated TOML
file.  The file name can also be set in a :ref:`gcovr configuration
file <configuration>`::

    add-weights = gcovr.weights.toml
    fail-under-weighted-line = 95

Relative paths in a configuration file are resolved relative to the
directory of that configuration file.
