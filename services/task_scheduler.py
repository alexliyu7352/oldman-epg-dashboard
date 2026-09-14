"""Publish due Demo schedules; deploy one Scheduler for this namespace."""

from oldman.runtime import TaskiqSchedulerApplication


class TaskSchedulerService(TaskiqSchedulerApplication):
    """Use native label schedules and the framework's Redis schedule source."""

