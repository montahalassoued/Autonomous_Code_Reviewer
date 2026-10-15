import logging
import threading
import time
import uuid
from dataclasses import dataclass
from itertools import islice
from typing import Callable, Protocol

from app.jobs.models import ReviewJob


logger = logging.getLogger(__name__)


# A leased queue message.
@dataclass
class ReceivedMessage:

    job: ReviewJob
    message_id: str
    pop_receipt: str
    dequeue_count: int


# What the webhook and the worker need from a queue.
class JobQueue(Protocol):

    # Adds a job to the queue.
    def enqueue(self, job: ReviewJob) -> None:
        ...

    # Leases up to max_messages visible messages, hiding them for visibility_timeout_s.
    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
        ...

    # Permanently removes a leased message (the job succeeded).
    def delete(self, message: ReceivedMessage) -> None:
        ...

    # Copies the message to the poison queue and removes the original.
    def dead_letter(self, message: ReceivedMessage) -> None:
        ...


# Internal bookkeeping for one in-memory message.
@dataclass
class _Entry:

    message_id: str
    job: ReviewJob
    visible_at: float
    dequeue_count: int = 0
    pop_receipt: str = ""


# Thread-safe queue for local development and tests.
class InMemoryJobQueue:

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[_Entry] = []
        self.poison: list[ReviewJob] = []

    # Adds a job, immediately visible.
    def enqueue(self, job: ReviewJob) -> None:
        with self._lock:
            self._entries.append(_Entry(uuid.uuid4().hex, job, visible_at=self._clock()))

    # Leases visible messages and increments their dequeue_count.
    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
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

    # Removes the message if the lease token is still current; otherwise a no-op.
    def delete(self, message: ReceivedMessage) -> None:
        with self._lock:
            self._remove_if_leased(message)

    # Move the message to the poison list if its lease is still current.
    def dead_letter(self, message: ReceivedMessage) -> None:
        with self._lock:
            if self._remove_if_leased(message):
                self.poison.append(message.job)

    # Removes the entry matching id + pop_receipt.
    def _remove_if_leased(self, message: ReceivedMessage) -> bool:
        for entry in self._entries:
            if entry.message_id == message.message_id and entry.pop_receipt == message.pop_receipt:
                self._entries.remove(entry)
                return True
        return False


# Azure Storage Queue backend.
class AzureStorageJobQueue:

    def __init__(self, queue_client, poison_client) -> None:
        self._queue = queue_client
        self._poison = poison_client

    # Sends the job as plain JSON text.
    def enqueue(self, job: ReviewJob) -> None:
        self._queue.send_message(job.to_json())

    # Leases up to max_messages messages.
    def receive(self, max_messages: int, visibility_timeout_s: int) -> list[ReceivedMessage]:
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

    # Deletes the message using its pop receipt.
    def delete(self, message: ReceivedMessage) -> None:
        self._queue.delete_message(message.message_id, message.pop_receipt)

    # Copy the job to the poison queue, then delete the original.
    def dead_letter(self, message: ReceivedMessage) -> None:
        self._poison.send_message(message.job.to_json())
        self._queue.delete_message(message.message_id, message.pop_receipt)
