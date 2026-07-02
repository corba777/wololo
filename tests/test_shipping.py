"""Shipping pipeline end-to-end — email to spreadsheet over taunts only."""

from __future__ import annotations

from wololo.codec import Message, split_frames
from wololo.orchestrator.scenarios import run_scenario
from wololo.orchestrator.shipping import (
    SHIPPING_KIND,
    FakeSheetSession,
    build_shipping_pipeline,
)

EXPECTED_ORDER = 1234567890
EXPECTED_TRACKING = 9400123456789


def test_pipeline_writes_the_shipping_row() -> None:
    world = build_shipping_pipeline()
    result = run_scenario(world.scenario)
    assert result.reached, f"no row written in {result.ticks} ticks"
    assert world.sheet.rows == [[f"order {EXPECTED_ORDER}", f"tracking {EXPECTED_TRACKING}"]]
    assert result.supervisor.restarts == {0: 0, 1: 0}


def test_noise_emails_produce_no_rows() -> None:
    world = build_shipping_pipeline()
    run_scenario(world.scenario)
    # Newsletter and GitHub mail were polled too, but only Amazon landed.
    assert len(world.sheet.rows) == 1


def test_payload_crossed_the_taunt_channel() -> None:
    """The data plane is the codec: the shipment facts are in the chat log."""
    world = build_shipping_pipeline()
    result = run_scenario(world.scenario)
    watcher_taunts = [e.taunt for e in result.kernel.taunt_log if e.sender == 0]
    messages, remainder = split_frames(watcher_taunts)
    assert messages == [Message(SHIPPING_KIND, (EXPECTED_ORDER, EXPECTED_TRACKING))]
    assert remainder == []


def test_pipeline_is_deterministic() -> None:
    def fingerprint() -> tuple:
        world = build_shipping_pipeline(seed=5)
        result = run_scenario(world.scenario)
        log = tuple((e.tick, e.sender, e.taunt) for e in result.kernel.taunt_log)
        return (result.reached, result.ticks, tuple(map(tuple, world.sheet.rows)), log)

    assert fingerprint() == fingerprint()


def test_sessions_are_injectable() -> None:
    """The seam for real MCP servers: pass your own sessions."""

    class OneEmailInbox:
        def list_tools(self):
            return [
                {
                    "name": "check_inbox",
                    "description": "poll",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]

        def __init__(self) -> None:
            self._sent = False

        def call_tool(self, name, arguments):
            if self._sent:
                return {"emails": []}
            self._sent = True
            return {
                "emails": [
                    {
                        "from": "ship-confirm@amazon.com",
                        "subject": "Shipped",
                        "body": "Order #42 has shipped. Tracking 777.",
                    }
                ]
            }

    sheet = FakeSheetSession()
    world = build_shipping_pipeline(email_session=OneEmailInbox(), sheet_session=sheet)
    result = run_scenario(world.scenario)
    assert result.reached
    assert sheet.rows == [["order 42", "tracking 777"]]
