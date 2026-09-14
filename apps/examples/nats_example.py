"""Shared sending functions: no views, subscribers or per-request connections."""

from typing import Literal

from apps.examples.nats_messages import (
    ExampleEvent,
    ObservationReply,
    ObservationRequest,
    ProjectStatusReply,
    ProjectStatusRequest,
)
from oldman.providers.nats import bus

PEERS = ("monitor_a", "monitor_b")


async def query_project_status(project_id: int, peer_id: str) -> ProjectStatusReply:
    """The selected receiving service, not this caller, queries the database."""
    return await bus.request(
        ProjectStatusRequest(project_id=project_id), "project.status", ProjectStatusReply,
        peer_id=peer_id, request_timeout=3,
    )


@bus.publisher("project.status.read")
async def query_and_publish_status(project_id: int, peer_id: str) -> ProjectStatusReply:
    """Publish the real RPC reply, then return that same reply to the caller."""
    return await query_project_status(project_id, peer_id)


async def send_example_events(kind: Literal["compete", "broadcast"]) -> None:
    """Send ten messages; returning does not mean subscribers have handled them."""
    subject = {"compete": "demo.events.compete", "broadcast": "demo.events.broadcast"}[kind]
    for sequence in range(1, 11):
        await bus.publish(ExampleEvent(sequence=sequence), subject)


async def query_observations(peer_id: str) -> ObservationReply:
    """Read one explicit node; there is no discovery, polling or group-RPC API."""
    return await bus.request(
        ObservationRequest(), "demo.observations", ObservationReply,
        peer_id=peer_id, request_timeout=3,
    )
