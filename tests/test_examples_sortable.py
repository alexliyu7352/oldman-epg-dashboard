"""Sortable workflow service contracts."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace


class _ScalarResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _Session:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    async def execute(self, statement):
        del statement
        return _ScalarResult(self.rows)


class ExampleSortableTests(unittest.TestCase):
    """Keep drag and keyboard moves on one tested domain service."""

    def test_move_reorders_same_board_and_updates_completion(self) -> None:
        from apps.examples.services import move_sortable_task

        tasks = [
            SimpleNamespace(id=1, position=0, priority="normal", status="todo", is_completed=False),
            SimpleNamespace(id=2, position=1, priority="normal", status="todo", is_completed=False),
            SimpleNamespace(id=3, position=0, priority="high", status="review", is_completed=False),
        ]

        asyncio.run(
            move_sortable_task(
                _Session(tasks),
                item_id=2,
                source_status="todo",
                target_status="review",
                target_position=0,
            )
        )

        self.assertEqual([0], [task.position for task in tasks if task.status == "todo"])
        self.assertEqual([2, 3], [task.id for task in sorted((task for task in tasks if task.status == "review"), key=lambda task: task.position)])
        self.assertEqual([0, 1], [task.position for task in tasks if task.status == "review"])

        asyncio.run(
            move_sortable_task(
                _Session(tasks),
                item_id=2,
                source_status="review",
                target_status="done",
                target_position=0,
            )
        )
        self.assertTrue(tasks[1].is_completed)

    def test_critical_task_move_to_done_is_rejected_without_mutation(self) -> None:
        from apps.examples.services import SortableTaskMoveRejected, move_sortable_task

        task = SimpleNamespace(id=4, position=0, priority="critical", status="review", is_completed=False)

        with self.assertRaises(SortableTaskMoveRejected):
            asyncio.run(
                move_sortable_task(
                    _Session([task]),
                    item_id=4,
                    source_status="review",
                    target_status="done",
                    target_position=0,
                )
            )

        self.assertEqual("review", task.status)
        self.assertEqual(0, task.position)


if __name__ == "__main__":
    unittest.main()
