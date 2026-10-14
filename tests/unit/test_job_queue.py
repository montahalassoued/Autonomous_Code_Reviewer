from types import SimpleNamespace

from app.jobs.models import ReviewJob
from app.jobs.queue import AzureStorageJobQueue, InMemoryJobQueue


class FakeClock:
    """A controllable clock: tests move time forward with advance()."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_job(sha: str = "abc123") -> ReviewJob:
    """Builds a job with fixed values (job_id and enqueued_at are generated)."""
    return ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha=sha, delivery_id="delivery-1")


def test_review_job_json_round_trip():
    job = make_job()

    restored = ReviewJob.from_json(job.to_json())

    assert restored == job, "A job must survive to_json/from_json unchanged"
    assert len(job.job_id) == 32, "job_id should be a uuid4 hex string"
    print("OK - ReviewJob round-trips through JSON")


def test_review_job_from_json_rejects_garbage():
    for bad in ("not json", '{"unexpected": 1}'):
        try:
            ReviewJob.from_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"from_json should raise ValueError for {bad!r}")
    print("OK - invalid job text raises ValueError")


def test_enqueue_receive_delete():
    queue = InMemoryJobQueue(clock=FakeClock())
    job = make_job()
    queue.enqueue(job)

    messages = queue.receive(max_messages=1, visibility_timeout_s=60)

    assert len(messages) == 1
    assert messages[0].job == job
    assert messages[0].dequeue_count == 1
    queue.delete(messages[0])
    assert queue.receive(max_messages=1, visibility_timeout_s=60) == [], "Deleted message must be gone"
    print("OK - enqueue, receive and delete work")


def test_leased_message_is_invisible():
    queue = InMemoryJobQueue(clock=FakeClock())
    queue.enqueue(make_job())

    queue.receive(max_messages=1, visibility_timeout_s=60)

    assert queue.receive(max_messages=1, visibility_timeout_s=60) == [], "Leased message must be hidden"
    print("OK - a leased message is invisible to other receivers")


def test_message_reappears_after_timeout_with_higher_dequeue_count():
    clock = FakeClock()
    queue = InMemoryJobQueue(clock=clock)
    queue.enqueue(make_job())
    first = queue.receive(max_messages=1, visibility_timeout_s=60)[0]

    clock.advance(61)
    second = queue.receive(max_messages=1, visibility_timeout_s=60)[0]

    assert second.message_id == first.message_id
    assert second.dequeue_count == 2, "Redelivery must increment dequeue_count"
    queue.delete(first)  # stale receipt from the first lease
    clock.advance(61)
    assert len(queue.receive(max_messages=1, visibility_timeout_s=60)) == 1, (
        "A delete with a stale pop_receipt must not remove the message"
    )
    print("OK - message reappears after the visibility timeout, stale delete ignored")


def test_dead_letter_moves_message_to_poison():
    queue = InMemoryJobQueue(clock=FakeClock())
    job = make_job()
    queue.enqueue(job)
    message = queue.receive(max_messages=1, visibility_timeout_s=60)[0]

    queue.dead_letter(message)

    assert queue.poison == [job]
    assert queue.receive(max_messages=1, visibility_timeout_s=60) == []
    print("OK - dead_letter moves the job to the poison list")


class FakeAzureQueueClient:
    """Mimics the QueueClient methods AzureStorageJobQueue uses."""

    def __init__(self, contents: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.deleted: list[tuple[str, str]] = []
        self._pending = [
            SimpleNamespace(id=f"m{index}", pop_receipt=f"r{index}", content=content, dequeue_count=1)
            for index, content in enumerate(contents or [])
        ]

    def send_message(self, content: str) -> None:
        self.sent.append(content)

    def receive_messages(self, messages_per_page: int, visibility_timeout: int):
        return iter(self._pending)

    def delete_message(self, message_id: str, pop_receipt: str) -> None:
        self.deleted.append((message_id, pop_receipt))


def test_azure_queue_sends_plain_json_and_maps_received_messages():
    job = make_job()
    queue_client = FakeAzureQueueClient([job.to_json(), job.to_json()])
    queue = AzureStorageJobQueue(queue_client, FakeAzureQueueClient())

    queue.enqueue(job)
    messages = queue.receive(max_messages=1, visibility_timeout_s=60)

    assert queue_client.sent == [job.to_json()], "Message body must be plain JSON text"
    assert len(messages) == 1, "receive must honour max_messages"
    assert messages[0].job == job and messages[0].pop_receipt == "r0"
    queue.delete(messages[0])
    assert queue_client.deleted == [("m0", "r0")]
    print("OK - Azure queue backend maps messages correctly")


def test_azure_queue_poisons_unparseable_messages_and_dead_letters():
    poison = FakeAzureQueueClient()
    queue_client = FakeAzureQueueClient(["not a job"])
    queue = AzureStorageJobQueue(queue_client, poison)

    assert queue.receive(max_messages=1, visibility_timeout_s=60) == []
    assert poison.sent == ["not a job"], "Unparseable body must be copied to the poison queue"
    assert queue_client.deleted == [("m0", "r0")]

    job = make_job()
    queue.dead_letter(SimpleNamespace(job=job, message_id="m9", pop_receipt="r9", dequeue_count=3))
    assert poison.sent[-1] == job.to_json()
    assert queue_client.deleted[-1] == ("m9", "r9")
    print("OK - Azure queue backend poisons bad messages and dead-letters jobs")


if __name__ == "__main__":
    test_review_job_json_round_trip()
    test_review_job_from_json_rejects_garbage()
    test_enqueue_receive_delete()
    test_leased_message_is_invisible()
    test_message_reappears_after_timeout_with_higher_dequeue_count()
    test_dead_letter_moves_message_to_poison()
    test_azure_queue_sends_plain_json_and_maps_received_messages()
    test_azure_queue_poisons_unparseable_messages_and_dead_letters()
    print("\nAll job queue tests passed.")
