import asyncio
from typing import Callable, Dict, List
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class Event:
    event_type: str
    return_request_id: str
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


# Event types
RETURN_INITIATED = "RETURN_INITIATED"
NEGOTIATION_COMPLETE = "NEGOTIATION_COMPLETE"
RETURN_PREVENTED = "RETURN_PREVENTED"
ROUTING_DECIDED = "ROUTING_DECIDED"
REROUTE_IMPOSSIBLE = "REROUTE_IMPOSSIBLE"
PATTERN_DETECTED = "PATTERN_DETECTED"
RECOVERY_DECIDED = "RECOVERY_DECIDED"
PROPHET_ALERT = "PROPHET_ALERT"


class EventBus:
    """Lightweight in-process event bus for agent communication."""

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._running = False
        self._queue: asyncio.Queue = None
        self._task: asyncio.Task = None
        self._history: List[Event] = []

    def start(self):
        self._running = True
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._process_events())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe a handler to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        """Unsubscribe a handler from an event type."""
        if event_type in self._handlers:
            self._handlers[event_type].remove(handler)

    async def emit(self, event: Event):
        """Emit an event to be processed by subscribed handlers."""
        self._history.append(event)
        if self._queue:
            await self._queue.put(event)

    async def _process_events(self):
        """Process events from the queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                handlers = self._handlers.get(event.event_type, [])
                for handler in handlers:
                    try:
                        await handler(event)
                    except Exception as e:
                        print(f"Error in handler for {event.event_type}: {e}")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_history(self, return_request_id: str = None) -> List[Event]:
        """Get event history, optionally filtered by return request."""
        if return_request_id:
            return [e for e in self._history if e.return_request_id == return_request_id]
        return self._history


# Singleton event bus
event_bus = EventBus()
