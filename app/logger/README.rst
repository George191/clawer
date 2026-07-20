Logger module
=============

The package keeps logging responsibilities separated in the same layout as the
reference logger:

* ``__init__.py`` exposes the public setup and logger factory API.
* ``adapter.py`` binds adapter type and template name to each record.
* ``fields.py`` defines structured fields, formats, and record filters.
* ``handlers.py`` owns asynchronous file output, daily rotation, and cleanup.

Regular application modules should import ``get_logger`` from ``app.logger``.
Adapter modules should continue to use ``get_adapter_logger`` so their context
and dedicated files are preserved. Direct imports of the standard library
``logging`` module are confined to this package's implementation.

Adapter logs are written as ``logs/<kind>/<template>/YYYY-MM-DD.log``. File
output accepts exact ``INFO`` records only.
