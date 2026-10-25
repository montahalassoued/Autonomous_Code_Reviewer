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


# The reviews.target of a pull request: one review per commit per target.
def pr_target(pr_number: int) -> str:
    return f"pr:{pr_number}"


# The reviews.target of a push to a branch: one review per pushed commit per branch.
def push_target(ref: str) -> str:
    return f"push:{ref}"


# One request to review one commit: the head of a pull request, or the commit a push moved a branch to.
@dataclass
class ReviewJob:

    repo_full_name: str
    pr_number: int
    head_sha: str
    delivery_id: str = ""
    job_id: str = field(default_factory=_new_job_id)
    event: str = "pull_request"
    enqueued_at: str = field(default_factory=_utc_now_iso)
    # The GitHub App installation that sent the event: its token reads this repo (None with a personal token).
    installation_id: int | None = None
    # A push only: the branch, and the commit before it (the diff is base_sha..head_sha).
    ref: str = ""
    base_sha: str = ""

    @property
    def is_push(self) -> bool:
        return self.event == "push"

    # The key of this job's row in the reviews table.
    @property
    def dedupe_key(self) -> "DedupeKey":
        target = push_target(self.ref) if self.is_push else pr_target(self.pr_number)
        return DedupeKey(self.repo_full_name, self.head_sha, target)

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
