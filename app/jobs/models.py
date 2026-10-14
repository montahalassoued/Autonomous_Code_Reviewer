import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _new_job_id() -> str:
    """Returns a fresh random job id (uuid4 hex, 32 chars)."""
    return uuid.uuid4().hex


def _utc_now_iso() -> str:
    """Returns the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReviewJob:
    """
    One request to review one commit of one pull request.

    Attributes:
        repo_full_name: repository in "owner/repo" form.
        pr_number: pull request number.
        head_sha: the PR head commit being reviewed (the idempotency anchor).
        delivery_id: GitHub's X-GitHub-Delivery GUID, kept for log correlation.
        job_id: unique id of this job, used in every log line.
        event: the GitHub event type that produced the job.
        enqueued_at: ISO-8601 UTC timestamp of when the job was queued.
    """

    repo_full_name: str
    pr_number: int
    head_sha: str
    delivery_id: str = ""
    job_id: str = field(default_factory=_new_job_id)
    event: str = "pull_request"
    enqueued_at: str = field(default_factory=_utc_now_iso)

    def to_json(self) -> str:
        """Serializes the job to the JSON text stored in the queue."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "ReviewJob":
        """
        Parses a job from queue text.

        Args:
            text: JSON produced by to_json().

        Returns:
            the ReviewJob.

        Raises:
            ValueError: if the text is not valid JSON or has missing or
            unexpected fields. Callers treat this as a poison message.
        """
        try:
            return cls(**json.loads(text))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid review job message: {exc}") from exc


@dataclass(frozen=True)
class DedupeKey:
    """
    Identifies a unit of work that must happen at most once.

    Attributes:
        repo_full_name: repository in "owner/repo" form.
        head_sha: commit being reviewed.
        target: what is being reviewed, e.g. "pr:42".
    """

    repo_full_name: str
    head_sha: str
    target: str
