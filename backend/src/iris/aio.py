import asyncio
from collections.abc import Iterable


async def cancel_and_wait(tasks: Iterable[asyncio.Future]) -> None:
    tasks = list(tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
