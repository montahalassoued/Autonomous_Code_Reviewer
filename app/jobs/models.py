import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


# Returns a fresh random job id (uuid4 hex, 32 chars).
def _new_job_id() -> str:
    return uuid.uuid4().hex


# Returns the current UTC time as an ISO-8601 string.
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# One request to review one commit of one pull request.
@dataclass
class ReviewJob:

    repo_full_name: str
    pr_number: int
    head_sha: str
    delivery_id: str = ""
    job_id: str = field(default_factory=_new_job_id)
    event: str = "pull_request"
    enqueued_at: str = field(default_factory=_utc_now_iso)

    # Serializes the job to the JSON text stored in the queue.
    def to_json(self) -> str:
        return json.dumps(asdict(self))

    # Parses a job from queue text.
    @classmethod
    def from_json(cls, text: str) -> "ReviewJob":
        try:
            return cls(**json.loads(text))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid review job message: {exc}") from exc


# Identifies a unit of work that must happen at most once.
@dataclass(frozen=True)
class DedupeKey:

    repo_full_name: str
    head_sha: str
    target: str
