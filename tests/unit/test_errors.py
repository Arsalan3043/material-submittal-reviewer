"""core/errors.py — the retryable/component/error_code contract Phase 1's SQS worker will
branch on directly. No DB, no network: pure class-attribute assertions."""
from __future__ import annotations

import pytest

from core.errors import (
    AppError,
    DatabaseConstraintError,
    DatabaseTransientError,
    ErrorCode,
    IdentityNotProvisionedError,
    IdentityTokenInvalidError,
    IllegalStateTransitionError,
    InvalidInputError,
    NotFoundError,
    PipelineInvalidInputError,
    PipelineRateLimitedError,
    PipelineTransientError,
    QueueMessageMalformedError,
    QueueTransientError,
    StorageObjectMissingError,
    StoragePermissionError,
    StorageTransientError,
)

RETRYABLE_LEAVES = [
    StorageTransientError,
    PipelineTransientError,
    PipelineRateLimitedError,
    QueueTransientError,
    DatabaseTransientError,
]

NON_RETRYABLE_LEAVES = [
    StorageObjectMissingError,
    StoragePermissionError,
    PipelineInvalidInputError,
    QueueMessageMalformedError,
    DatabaseConstraintError,
    IdentityTokenInvalidError,
    IdentityNotProvisionedError,
    InvalidInputError,
    NotFoundError,
    IllegalStateTransitionError,
]


@pytest.mark.parametrize("leaf_cls", RETRYABLE_LEAVES)
def test_retryable_leaves_are_retryable(leaf_cls: type[AppError]) -> None:
    assert leaf_cls("boom").retryable is True


@pytest.mark.parametrize("leaf_cls", NON_RETRYABLE_LEAVES)
def test_non_retryable_leaves_are_not_retryable(leaf_cls: type[AppError]) -> None:
    assert leaf_cls("boom").retryable is False


@pytest.mark.parametrize("leaf_cls", RETRYABLE_LEAVES + NON_RETRYABLE_LEAVES)
def test_every_leaf_sets_a_distinct_error_code(leaf_cls: type[AppError]) -> None:
    assert isinstance(leaf_cls.error_code, ErrorCode)


def test_storage_errors_set_storage_component() -> None:
    assert StorageObjectMissingError("boom").component == "storage"
    assert StorageTransientError("boom").component == "storage"


def test_pipeline_errors_set_pipeline_component() -> None:
    assert PipelineTransientError("boom").component == "pipeline"
    assert PipelineInvalidInputError("boom").component == "pipeline"


def test_edge_errors_set_edge_component() -> None:
    assert InvalidInputError("boom").component == "edge"
    assert NotFoundError("boom").component == "edge"
    assert IllegalStateTransitionError("boom").component == "edge"


def test_str_includes_cause_when_present() -> None:
    cause = ValueError("underlying")
    err = StorageTransientError("wrapper message", cause=cause)
    assert "wrapper message" in str(err)
    assert "ValueError" in str(err)
    assert "underlying" in str(err)


def test_str_omits_cause_clause_when_absent() -> None:
    err = StorageObjectMissingError("no cause here")
    assert str(err) == "no cause here"


def test_app_error_default_retryable_is_false() -> None:
    class UnclassifiedError(AppError):
        error_code = ErrorCode.INVALID_INPUT
        component = "test"

    assert UnclassifiedError("boom").retryable is False
