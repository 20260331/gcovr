.. program is needed to resolve :option: references
.. program:: gcovr

.. _components:

Component Groups
================

The standard gcovr reports aggregate coverage by directory.
This cannot express quality boundaries that span multiple directories,
such as shared libraries that are used by several components.

With component groups, files can be assigned to named components,
independent of their location in the source tree.
Each component gets its own coverage numbers in the
:ref:`text summary <txt_output>` report and in the
:ref:`JSON summary <json_output>` report,
and each component can enforce its own coverage threshold.

.. note::

    Components only add an additional view of the coverage data.
    Which files are processed at all is still determined by the
    :ref:`filters <filters>`.

Simple components
-----------------

On the command line, a component is defined with
:option:`--component` as ``NAME`` or ``NAME:FILTER``.
The filter uses the same syntax as :option:`--filter`.
The option can be repeated; using the same name multiple times
adds further filters to the component:

.. code-block:: bash

    gcovr \
        --component 'core:src/core/' \
        --component 'network:src/network/' \
        --txt-summary

The same form can be used in an ini style configuration file
with the ``component`` key.

Hierarchical and overlapping components
---------------------------------------

The full feature set is available with the ``[[component]]``
tables in a :ref:`TOML configuration file <configuration>`:

.. code-block:: toml

    [[component]]
    name = "utils"
    # Filter patterns have the same semantics as for --filter.
    filter = ['src/shared/']
    # Files matching an exclude filter are not part of the component.
    exclude = ['src/shared/generated/']
    # Each component can enforce its own coverage threshold.
    fail-under-line = 95.0

    [[component]]
    name = "core"
    filter = ['src/core/']
    components = ["utils"]

    [[component]]
    name = "network"
    filter = ['src/network/']
    components = ["utils"]

    # A parent component aggregates its child components.
    [[component]]
    name = "product"
    components = ["core", "network"]
    fail-under-line = 90.0

The file sets of different components are allowed to overlap:
the file ``src/shared/string.cpp`` can belong to both ``core`` and
``network``.

A component that lists other components as ``components`` is a
parent component. Its file set is the **union** of the files
matched by its own filters and the file sets of all its child
components. Therefore, a file that belongs to several children is
counted only once in the parent, never multiple times.

The component hierarchy must not contain cycles and all child
names must refer to defined components.

Per-component coverage thresholds
---------------------------------

The following keys can be added to each component and have the same
semantics as the corresponding global options:

- ``fail-under-line`` (see :option:`--fail-under-line`)
- ``fail-under-branch`` (see :option:`--fail-under-branch`)
- ``fail-under-condition-or-decision``
  (see :option:`--fail-under-condition-or-decision`)
- ``fail-under-function`` (see :option:`--fail-under-function`)

If the coverage of any component is below its threshold, gcovr exits
with the same status code as the corresponding global
``--fail-under-*`` option. The status codes of several failed metrics,
components and the global thresholds are combined with a bitwise OR.
