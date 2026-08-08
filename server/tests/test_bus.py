import asyncio
import threading

from trug.bus import EventBus


def test_publish_from_worker_thread_wakes_subscriber():
    """publish() from a non-loop thread must wake a subscriber awaiting get()."""

    async def scenario():
        bus = EventBus()
        queue = bus.subscribe()

        def worker():
            bus.publish("item_added", {"id": "x"})

        threading.Thread(target=worker).start()
        return await asyncio.wait_for(queue.get(), timeout=1.0)

    message = asyncio.run(scenario())
    assert message == {"event": "item_added", "data": {"id": "x"}}


def test_publish_drops_when_queue_full():
    """A full subscriber queue is dropped, not raised, and does not block."""

    async def scenario():
        bus = EventBus()
        queue = bus.subscribe()
        for _ in range(200):
            bus.publish("noise", {})
        return queue.qsize()

    assert asyncio.run(scenario()) == 100
