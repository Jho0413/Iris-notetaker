import asyncio

from iris.aio import cancel_and_wait


async def test_running_tasks_are_cancelled_and_have_ended_when_it_returns():
    cleaned_up = []

    async def runs_until_cancelled() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.append(True)

    tasks = [asyncio.create_task(runs_until_cancelled()) for _ in range(2)]
    await asyncio.sleep(0)
    await cancel_and_wait(tasks)
    assert all(task.cancelled() for task in tasks)
    assert cleaned_up == [True, True]


async def test_a_task_that_already_failed_does_not_raise():
    async def fails() -> None:
        raise RuntimeError("boom")

    failed = asyncio.create_task(fails())
    await asyncio.sleep(0)
    await cancel_and_wait([failed])
    assert isinstance(failed.exception(), RuntimeError)


async def test_no_tasks_returns_at_once():
    await cancel_and_wait([])
