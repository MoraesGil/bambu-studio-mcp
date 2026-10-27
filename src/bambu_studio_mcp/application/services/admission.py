"""Thread admission shared by synchronous callers and asynchronous transports."""
from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

P = ParamSpec('P')
T = TypeVar('T')


class NativeAdmission:
    def __init__(self) -> None:
        # Only the actual native stage takes this lock. A queued use case may
        # first acquire the revision repository, as native edits already do.
        # Holding this lock around that snapshot would invert their lock order.
        self._native = threading.RLock()
        self._queues: weakref.WeakValueDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakValueDictionary()
        self._queues_lock = threading.Lock()

    @contextmanager
    def session(self) -> Iterator[None]:
        self._native.acquire()
        try:
            yield
        finally:
            self._native.release()

    async def run(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        loop = asyncio.get_running_loop()
        with self._queues_lock:
            queue = self._queues.setdefault(loop, asyncio.Lock())

        async def admitted() -> T:
            # Queue coroutines before occupying executor workers, so read-only
            # requests retain executor capacity during a long native run.
            async with queue:
                return await asyncio.to_thread(function, *args, **kwargs)

        worker = asyncio.create_task(admitted())
        # Shielding keeps admission until the worker actually exits. Consuming
        # an abandoned exception avoids unhandled-task warnings after disconnect.
        def consume(completed: asyncio.Task[T]) -> None:
            if completed.done() and not completed.cancelled():
                completed.exception()
        worker.add_done_callback(consume)
        return await asyncio.shield(worker)
