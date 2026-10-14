import logging
import threading
import time
import uuid
from dataclasses import dataclass
from itertools import islice
from typing import Callable, Protocol

from app.jobs.models import ReviewJob


logger = logging.getLogger(__name__)


@dataclass
class ReceivedMessage:
    """
    A leased queue message.

    Attributes:
        job: the parsed review job.
        message_id: queue-assigned id.
        pop_receipt: lease token, required to delete the message.
        dequeue_count: how many times the message has been received (1 on first try).
    """

    job: ReviewJob
    message_id: str
    pop_receipt: str
    dequeue_count: int


class JobQueue(Protocol):
    """What the webhook and the worker need from a queue."""

    def enqueue(self, job: ReviewJob) -> None:
        """Adds a job to the queue. Raises on failure."""

    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
        """Leases up to max_messages visible messages, hiding them for visibility_timeout_s."""

    def delete(self, message: ReceivedMessage) -> None:
        """Permanently removes a leased message (the job succeeded)."""

    def dead_letter(self, message: ReceivedMessage) -> None:
        """Copies the message to the poison queue and removes the original."""


@dataclass
class _Entry:
    """Internal bookkeeping for one in-memory message."""

    message_id: str
    job: ReviewJob
    visible_at: float
    dequeue_count: int = 0
    pop_receipt: str = ""


class InMemoryJobQueue:
    """
    Thread-safe queue for local development and tests. NOT shared between
    processes, so the API and worker only see each other's jobs in one process.

    Args:
        clock: returns "now" in seconds; tests inject a fake clock to
               move time forward without sleeping.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[_Entry] = []
        self.poison: list[ReviewJob] = []

    def enqueue(self, job: ReviewJob) -> None:
        """Adds a job, immediately visible."""
        with self._lock:
            self._entries.append(_Entry(uuid.uuid4().hex, job, visible_at=self._clock()))

    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
        """Leases visible messages and increments their dequeue_count."""
        received: list[ReceivedMessage] = []
        with self._lock:
            now = self._clock()
            for entry in self._entries:
                if len(received) >= max_messages:
                    break
                if entry.visible_at > now:
                    continue
                entry.dequeue_count += 1
                entry.visible_at = now + visibility_timeout_s
                entry.pop_receipt = uuid.uuid4().hex  # a new lease invalidates the old receipt
                received.append(
                    ReceivedMessage(entry.job, entry.message_id, entry.pop_receipt, entry.dequeue_count)
                )
        return received

    def delete(self, message: ReceivedMessage) -> None:
        """Removes the message if the lease token is still current; otherwise a no-op."""
        with self._lock:
            self._remove_if_leased(message)

    def dead_letter(self, message: ReceivedMessage) -> None:
        """Moves the message to the poison list if the lease token is still current."""
        with self._lock:
            if self._remove_if_leased(message):
                self.poison.append(message.job)

    def _remove_if_leased(self, message: ReceivedMessage) -> bool:
        """Removes the entry matching id + pop_receipt. Returns True if one was removed."""
        for entry in self._entries:
            if entry.message_id == message.message_id and entry.pop_receipt == message.pop_receipt:
                self._entries.remove(entry)
                return True
        return False


class AzureStorageJobQueue:
    """
    Azure Storage Queue backend.

    Takes already-built QueueClients (see app/jobs/factory.py) so this class
    has no Azure imports and can be tested with fakes.

    Args:
        queue_client: client for the main queue.
        poison_client: client for the poison queue.
    """

    def __init__(self, queue_client, poison_client) -> None:
        self._queue = queue_client
        self._poison = poison_client

    def enqueue(self, job: ReviewJob) -> None:
        """Sends the job as plain JSON text."""
        self._queue.send_message(job.to_json())

    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
        """
        Leases up to max_messages messages. A message whose body is not a
        valid job is moved to the poison queue right away, because retrying
        it could never succeed.
        """
        raw_messages = self._queue.receive_messages(
            messages_per_page=max_messages, visibility_timeout=visibility_timeout_s
        )
        received: list[ReceivedMessage] = []
        # islice: the SDK pager would otherwise keep leasing further pages.
        for raw in islice(raw_messages, max_messages):
            try:
                job = ReviewJob.from_json(raw.content)
            except ValueError:
                logger.error("Unparseable queue message %s moved to poison queue", raw.id)
                self._poison.send_message(raw.content)
                self._queue.delete_message(raw.id, raw.pop_receipt)
                continue
            received.append(ReceivedMessage(job, raw.id, raw.pop_receipt, raw.dequeue_count))
        return received

    def delete(self, message: ReceivedMessage) -> None:
        """Deletes the message using its pop receipt."""
        self._queue.delete_message(message.message_id, message.pop_receipt)

    def dead_letter(self, message: ReceivedMessage) -> None:
        """Copies the job to the poison queue, then deletes the original (copy first so nothing is lost)."""
        self._poison.send_message(message.job.to_json())
        self._queue.delete_message(message.message_id, message.pop_receipt)
