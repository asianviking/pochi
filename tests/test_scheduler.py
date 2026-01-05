"""Tests for pochi.scheduler module."""

from __future__ import annotations

import anyio
import pytest

from pochi.model import EngineId, ResumeToken
from pochi.scheduler import ThreadJob, ThreadScheduler


@pytest.fixture
def engine_id() -> EngineId:
    """Create an engine ID for testing."""
    return EngineId("test-engine")


@pytest.fixture
def resume_token(engine_id: EngineId) -> ResumeToken:
    """Create a resume token for testing."""
    return ResumeToken(engine=engine_id, value="session-123")


class TestThreadJob:
    """Tests for ThreadJob dataclass."""

    def test_create_thread_job(self, resume_token: ResumeToken) -> None:
        """Test creating a ThreadJob."""
        job = ThreadJob(
            chat_id=12345,
            user_msg_id=67890,
            text="Hello world",
            resume_token=resume_token,
        )
        assert job.chat_id == 12345
        assert job.user_msg_id == 67890
        assert job.text == "Hello world"
        assert job.resume_token == resume_token

    def test_thread_job_is_frozen(self, resume_token: ResumeToken) -> None:
        """Test that ThreadJob is immutable (frozen)."""
        job = ThreadJob(
            chat_id=12345,
            user_msg_id=67890,
            text="Hello world",
            resume_token=resume_token,
        )
        with pytest.raises(AttributeError):
            job.chat_id = 99999  # type: ignore[misc]


class TestThreadScheduler:
    """Tests for ThreadScheduler class."""

    def test_thread_key(self, resume_token: ResumeToken) -> None:
        """Test thread_key static method."""
        key = ThreadScheduler.thread_key(resume_token)
        assert key == "test-engine:session-123"

    def test_thread_key_different_engines(self, engine_id: EngineId) -> None:
        """Test thread_key produces different keys for different engines."""
        token1 = ResumeToken(engine=EngineId("engine-a"), value="session-1")
        token2 = ResumeToken(engine=EngineId("engine-b"), value="session-1")
        key1 = ThreadScheduler.thread_key(token1)
        key2 = ThreadScheduler.thread_key(token2)
        assert key1 != key2
        assert key1 == "engine-a:session-1"
        assert key2 == "engine-b:session-1"

    @pytest.mark.anyio
    async def test_enqueue_and_run_single_job(self, resume_token: ResumeToken) -> None:
        """Test enqueueing and running a single job."""
        jobs_run: list[ThreadJob] = []

        async def run_job(job: ThreadJob) -> None:
            jobs_run.append(job)

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)
            job = ThreadJob(
                chat_id=1,
                user_msg_id=1,
                text="test",
                resume_token=resume_token,
            )
            await scheduler.enqueue(job)
            # Give time for the job to run
            await anyio.sleep(0.05)

        assert len(jobs_run) == 1
        assert jobs_run[0] == job

    @pytest.mark.anyio
    async def test_enqueue_resume_helper(self, resume_token: ResumeToken) -> None:
        """Test enqueue_resume helper method."""
        jobs_run: list[ThreadJob] = []

        async def run_job(job: ThreadJob) -> None:
            jobs_run.append(job)

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)
            await scheduler.enqueue_resume(
                chat_id=123,
                user_msg_id=456,
                text="hello",
                resume_token=resume_token,
            )
            await anyio.sleep(0.05)

        assert len(jobs_run) == 1
        assert jobs_run[0].chat_id == 123
        assert jobs_run[0].user_msg_id == 456
        assert jobs_run[0].text == "hello"

    @pytest.mark.anyio
    async def test_serializes_jobs_same_thread(self, resume_token: ResumeToken) -> None:
        """Test that jobs on the same thread run sequentially."""
        execution_order: list[int] = []

        async def run_job(job: ThreadJob) -> None:
            execution_order.append(job.user_msg_id)
            # Simulate some work
            await anyio.sleep(0.02)

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

            # Enqueue multiple jobs for the same thread
            for i in range(3):
                job = ThreadJob(
                    chat_id=1,
                    user_msg_id=i,
                    text=f"job-{i}",
                    resume_token=resume_token,
                )
                await scheduler.enqueue(job)

            # Wait for all jobs to complete
            await anyio.sleep(0.15)

        # Jobs should run in order
        assert execution_order == [0, 1, 2]

    @pytest.mark.anyio
    async def test_parallel_jobs_different_threads(self, engine_id: EngineId) -> None:
        """Test that jobs on different threads can run in parallel."""
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}

        async def run_job(job: ThreadJob) -> None:
            key = job.resume_token.value
            start_times[key] = anyio.current_time()
            await anyio.sleep(0.05)
            end_times[key] = anyio.current_time()

        token1 = ResumeToken(engine=engine_id, value="thread-1")
        token2 = ResumeToken(engine=engine_id, value="thread-2")

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

            # Enqueue jobs for different threads simultaneously
            await scheduler.enqueue(
                ThreadJob(chat_id=1, user_msg_id=1, text="t1", resume_token=token1)
            )
            await scheduler.enqueue(
                ThreadJob(chat_id=1, user_msg_id=2, text="t2", resume_token=token2)
            )

            await anyio.sleep(0.15)

        # Both threads should have started close together
        assert "thread-1" in start_times
        assert "thread-2" in start_times
        # The second job should start before the first one ends (parallel)
        assert start_times["thread-2"] < end_times["thread-1"]

    @pytest.mark.anyio
    async def test_note_thread_known_waits_for_busy(
        self, resume_token: ResumeToken
    ) -> None:
        """Test that note_thread_known causes jobs to wait for busy thread."""
        execution_log: list[str] = []

        async def run_job(job: ThreadJob) -> None:
            execution_log.append(f"start-{job.text}")
            await anyio.sleep(0.02)
            execution_log.append(f"end-{job.text}")

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

            # Create a "busy" event
            done = anyio.Event()
            await scheduler.note_thread_known(resume_token, done)

            # Enqueue a job while busy
            job = ThreadJob(
                chat_id=1,
                user_msg_id=1,
                text="waiting-job",
                resume_token=resume_token,
            )
            await scheduler.enqueue(job)

            # Let some time pass (job should be waiting)
            await anyio.sleep(0.05)

            # Signal that the busy thread is done
            done.set()

            # Now job should run
            await anyio.sleep(0.1)

        assert "start-waiting-job" in execution_log
        assert "end-waiting-job" in execution_log

    @pytest.mark.anyio
    async def test_note_thread_known_clears_when_done(
        self, resume_token: ResumeToken
    ) -> None:
        """Test that busy state is cleared when done event is set."""
        jobs_run: list[ThreadJob] = []

        async def run_job(job: ThreadJob) -> None:
            jobs_run.append(job)

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

            # Create and immediately complete a busy event
            done = anyio.Event()
            await scheduler.note_thread_known(resume_token, done)
            done.set()

            # Give time for _clear_busy to run
            await anyio.sleep(0.02)

            # Now enqueue a job - should run immediately
            job = ThreadJob(
                chat_id=1,
                user_msg_id=1,
                text="test",
                resume_token=resume_token,
            )
            await scheduler.enqueue(job)
            await anyio.sleep(0.05)

        assert len(jobs_run) == 1

    @pytest.mark.anyio
    async def test_worker_removes_thread_when_queue_empty(
        self, resume_token: ResumeToken
    ) -> None:
        """Test that worker removes itself when queue is empty."""
        run_count = 0

        async def run_job(job: ThreadJob) -> None:
            nonlocal run_count
            run_count += 1

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

            job = ThreadJob(
                chat_id=1,
                user_msg_id=1,
                text="test",
                resume_token=resume_token,
            )
            await scheduler.enqueue(job)
            await anyio.sleep(0.05)

            # After job completes and queue is empty, thread should be removed
            key = ThreadScheduler.thread_key(resume_token)
            assert key not in scheduler._active_threads

        assert run_count == 1

    @pytest.mark.anyio
    async def test_multiple_jobs_enqueued_while_running(
        self, resume_token: ResumeToken
    ) -> None:
        """Test enqueueing multiple jobs while one is running."""
        execution_order: list[int] = []
        first_job_started = anyio.Event()

        async def run_job(job: ThreadJob) -> None:
            if job.user_msg_id == 1:
                first_job_started.set()
            execution_order.append(job.user_msg_id)
            await anyio.sleep(0.02)

        async with anyio.create_task_group() as tg:
            scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

            # Enqueue first job
            await scheduler.enqueue(
                ThreadJob(
                    chat_id=1, user_msg_id=1, text="first", resume_token=resume_token
                )
            )

            # Wait for first job to start
            await first_job_started.wait()

            # Now enqueue more jobs while first is running
            await scheduler.enqueue(
                ThreadJob(
                    chat_id=1, user_msg_id=2, text="second", resume_token=resume_token
                )
            )
            await scheduler.enqueue(
                ThreadJob(
                    chat_id=1, user_msg_id=3, text="third", resume_token=resume_token
                )
            )

            await anyio.sleep(0.15)

        assert execution_order == [1, 2, 3]
