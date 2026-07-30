"""
Typed error hierarchy — architecture-kit/references/error-model.md, adapted to this
project's real components (storage, pipeline, queue, database, identity).

`retryable` is the field that earns its keep: Phase 1's SQS worker branches on it directly —
retryable errors leave the message for SQS to redeliver (real retry, not simulated);
non-retryable errors delete the message immediately so it never wastes a redelivery slot.
Two of the three real worker failures hit during this project's own testing session
(`Connection error.` from a sleeping laptop, `IncompleteRead` from a flaky connection) were
retryable and were permanently failed anyway, because nothing distinguished them from a
genuinely unrecoverable one (`NoSuchKey` — a file that will never exist no matter how many
times you ask). That gap is exactly what this file exists to close.

Three levels, per the kit — no deeper:
    AppError            -> retryable = False by default
      CategoryError      -> sets `component`
        LeafError        -> sets `error_code`, optionally retryable = True
"""
from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    # Storage (S3)
    STORAGE_NOT_FOUND = "STORAGE_NOT_FOUND"
    STORAGE_TRANSIENT = "STORAGE_TRANSIENT"
    STORAGE_PERMISSION = "STORAGE_PERMISSION"

    # Review pipeline (the src/ seam — OpenAI/Chroma/Cohere calls happen inside it)
    PIPELINE_TRANSIENT = "PIPELINE_TRANSIENT"
    PIPELINE_INVALID_INPUT = "PIPELINE_INVALID_INPUT"
    PIPELINE_RATE_LIMITED = "PIPELINE_RATE_LIMITED"

    # Job queue
    QUEUE_TRANSIENT = "QUEUE_TRANSIENT"
    QUEUE_MESSAGE_MALFORMED = "QUEUE_MESSAGE_MALFORMED"

    # Database
    DATABASE_TRANSIENT = "DATABASE_TRANSIENT"
    DATABASE_CONSTRAINT = "DATABASE_CONSTRAINT"

    # Identity
    IDENTITY_TOKEN_INVALID = "IDENTITY_TOKEN_INVALID"
    IDENTITY_NOT_PROVISIONED = "IDENTITY_NOT_PROVISIONED"

    # Generic / edge
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    ILLEGAL_STATE_TRANSITION = "ILLEGAL_STATE_TRANSITION"


class AppError(Exception):
    """Base for every error this application raises deliberately.

    Subclasses set class-level attributes:
      error_code — the specific failure (metric labels, alert routing)
      component  — which subsystem (dashboards, ownership)
      retryable  — True means waiting and retrying may succeed
    """

    error_code: ErrorCode
    component: str
    retryable: bool = False

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause:
            return f"{self.message} | caused by {type(self.cause).__name__}: {self.cause}"
        return self.message


# ── Storage ──────────────────────────────────────────────────────────────────────────


class StorageError(AppError):
    component = "storage"


class StorageObjectMissingError(StorageError):
    """The S3 key genuinely doesn't exist. Retrying changes nothing — e.g. a file that
    was never actually uploaded (the exact NoSuchKey case hit during real testing)."""

    error_code = ErrorCode.STORAGE_NOT_FOUND
    retryable = False


class StorageTransientError(StorageError):
    """Connection dropped, timed out, or was reset mid-transfer — e.g. the real
    `IncompleteRead(...)` failure hit during testing. Waiting and retrying may well work."""

    error_code = ErrorCode.STORAGE_TRANSIENT
    retryable = True


class StoragePermissionError(StorageError):
    """Credentials/policy problem. Retrying without fixing the permission changes nothing."""

    error_code = ErrorCode.STORAGE_PERMISSION
    retryable = False


# ── Review pipeline (wraps the frozen src/ seam) ────────────────────────────────────


class PipelineError(AppError):
    component = "pipeline"


class PipelineTransientError(PipelineError):
    """A network-level failure inside the pipeline's LLM/vector-store calls — e.g. the
    real `Connection error.` hit when a laptop went to sleep mid-review."""

    error_code = ErrorCode.PIPELINE_TRANSIENT
    retryable = True


class PipelineRateLimitedError(PipelineError):
    error_code = ErrorCode.PIPELINE_RATE_LIMITED
    retryable = True


class PipelineInvalidInputError(PipelineError):
    """The submittal itself is the problem (corrupt PDF, unreadable file). Same input,
    same failure, every time — retrying wastes a redelivery slot for nothing."""

    error_code = ErrorCode.PIPELINE_INVALID_INPUT
    retryable = False


# ── Job queue ────────────────────────────────────────────────────────────────────────


class QueueError(AppError):
    component = "queue"


class QueueTransientError(QueueError):
    error_code = ErrorCode.QUEUE_TRANSIENT
    retryable = True


class QueueMessageMalformedError(QueueError):
    """A message that doesn't deserialize into a valid job will never deserialize on
    redelivery either."""

    error_code = ErrorCode.QUEUE_MESSAGE_MALFORMED
    retryable = False


# ── Database ─────────────────────────────────────────────────────────────────────────


class DatabaseError(AppError):
    component = "database"


class DatabaseTransientError(DatabaseError):
    """Connection drop, pool exhaustion, deadlock — worth a retry."""

    error_code = ErrorCode.DATABASE_TRANSIENT
    retryable = True


class DatabaseConstraintError(DatabaseError):
    """A constraint violation (unique, FK, check). The same write will fail identically
    every time until the underlying data conflict is resolved."""

    error_code = ErrorCode.DATABASE_CONSTRAINT
    retryable = False


# ── Identity ─────────────────────────────────────────────────────────────────────────


class IdentityError(AppError):
    component = "identity"


class IdentityTokenInvalidError(IdentityError):
    error_code = ErrorCode.IDENTITY_TOKEN_INVALID
    retryable = False


class IdentityNotProvisionedError(IdentityError):
    """A valid token for a user with no matching `users` row — accounts are
    admin-provisioned, not self-registered (see PROJECT_STATE.md §13.3)."""

    error_code = ErrorCode.IDENTITY_NOT_PROVISIONED
    retryable = False


# ── Generic / edge-facing ────────────────────────────────────────────────────────────


class InvalidInputError(AppError):
    component = "edge"
    error_code = ErrorCode.INVALID_INPUT
    retryable = False


class NotFoundError(AppError):
    component = "edge"
    error_code = ErrorCode.NOT_FOUND
    retryable = False


class IllegalStateTransitionError(AppError):
    """E.g. calling /start on a submittal that isn't CREATED. See
    core/policies/submittal_lifecycle.py (Phase 5) for the single owner of these rules."""

    component = "edge"
    error_code = ErrorCode.ILLEGAL_STATE_TRANSITION
    retryable = False
