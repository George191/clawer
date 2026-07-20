Logger module
=============

The package keeps logging responsibilities separated in the same layout as the
reference logger:

* ``__init__.py`` exposes the public setup and logger factory API.
* ``adapter.py`` binds adapter type and template name to each record.
* ``fields.py`` defines structured fields, formats, and record filters.
* ``handlers.py`` owns asynchronous file output, daily rotation, and cleanup.

Adapter logs are written as ``logs/<kind>/<template>/YYYY-MM-DD.log``. File
output accepts exact ``INFO`` records only.
