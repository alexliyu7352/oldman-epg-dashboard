"""Project User model."""

from oldman.auth import AbstractUser
from oldman.i18n import gettext_lazy as _


class OldmanUser(AbstractUser):
    """Extend the framework User contract as the project's concrete table."""

    __tablename__ = "oldman_user"  # pyright: ignore[reportAssignmentType] -- SQLAlchemy declared_attr override

    class Meta:
        """Provide request-time labels for automatic Admin presentation."""

        verbose_name = _("User")
        verbose_name_plural = _("Users")
