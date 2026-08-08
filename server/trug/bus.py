from __future__ import annotations

import asyncio

_MAX_QUEUE = 100


def _safe_put(queue: asyncio.Queue, message: dict) -> None:
    """Enqueue without blocking; drop for a full (slow) subscriber."""
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        pass


class EventBus:
    """Fan-out event bus backed by per-subscriber asyncio queues.

    Publishing is non-blocking: if a subscriber's queue is full the event is
    dropped for that subscriber rather than blocking the publisher.

    ``publish`` is safe to call from any thread. Routes run in FastAPI's
    threadpool while SSE subscribers await ``queue.get()`` in the event loop,
    so cross-thread delivery is scheduled onto the loop via
    ``loop.call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue:
        # Capture the running loop so cross-thread publishes can reach it.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, data: dict) -> None:
        message = {"event": event, "data": data}
        loop = self._loop
        # Deliver directly when we have no loop yet or we're already on the
        # loop thread; otherwise hop onto the loop thread to touch the queue.
        on_loop = False
        if loop is not None:
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False

        for queue in list(self._subscribers):
            if loop is None or on_loop:
                _safe_put(queue, message)
            else:
                loop.call_soon_threadsafe(_safe_put, queue, message)
