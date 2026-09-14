"""Typed bytes payloads shared by senders and receivers; importing stays offline."""

from oldman.serializers import MsgspecModel


class ProjectStatusRequest(MsgspecModel):
    """Read an existing project, without enqueuing a task or changing its data."""

    project_id: int


class ProjectStatusReply(MsgspecModel):
    """A missing record is a business result, not a network failure."""

    project_id: int
    found: bool
    peer_id: str
    pid: int
    name: str = ""
    status: str = ""
    progress: int = 0
    task_count: int = 0


class ExampleEvent(MsgspecModel):
    """One of ten manually sent observations; never stored as a database record."""

    sequence: int


class ObservationRequest(MsgspecModel):
    """An empty, typed request for this receiver's current counters."""


class ObservationReply(MsgspecModel):
    """A bounded snapshot, shared by all staff users since this process started."""

    peer_id: str
    pid: int
    compete: int
    broadcast: int
    reports: int
    compete_sender: str
    broadcast_sender: str
    report_sender: str
