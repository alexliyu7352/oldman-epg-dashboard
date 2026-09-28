"""Permissions the example App checks.

One example shows how a project gives its own pages permissions: reading the example projects
(their table, an edit window's contents) asks for ``examples.view_projects``; creating, saving
and deleting one asks for ``examples.change_projects``. Superusers hold both anyway; a staff
account needs a role that grants them (roles are edited in the built-in Admin at /admin).
The framework presets none of this: a dashboard decides its own permissions.
"""

from __future__ import annotations

from oldman.auth import Permission, PermissionSet
from oldman.i18n import gettext_lazy as _


class ExamplePermissions(PermissionSet, namespace="examples"):
    """What the example pages check beyond "signed in as staff"."""

    view_projects = Permission(_("View example projects"))
    change_projects = Permission(_("Change example projects"))


__all__ = ["ExamplePermissions"]
