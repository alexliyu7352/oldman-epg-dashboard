"""Execute the Demo's reports and exports queues in a shared process pool."""

from oldman.runtime import TaskiqWorkerApplication


class TaskWorkerService(TaskiqWorkerApplication):
    """All process, connection and shutdown handling belongs to the framework."""

