"""Admin task management endpoints for Taskiq workloads."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.routers.user.auth import require_role
from api.task_queue import (
    get_registered_queue_names,
    get_task_record,
    get_taskiq_queue_name,
    list_running_task_ids,
    list_task_records,
    request_task_cancellation,
    retry_task_by_id,
    TaskRetryError,
)
from db.enums import UserRole
from db.models import User

router = APIRouter(prefix="/api/v1/admin/tasks", tags=["Admin - Task Management"])

TERMINAL_TASK_STATUSES = {"success", "error", "cancelled", "skipped", "enqueue_failed"}
RETRYABLE_TASK_STATUSES = {"error", "cancelled", "skipped", "enqueue_failed"}


class TaskRecordResponse(BaseModel):
    task_id: str
    actor_name: str | None = None
    queue_name: str | None = None
    status: str = "unknown"
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
    duration_ms: float | None = None
    priority: int | None = None
    max_retries: int | None = None
    time_limit_ms: int | float | None = None
    worker_pid: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    cancellation_requested: bool = False
    cancellation_reason: str | None = None
    cancellation_requested_at: str | None = None
    args_preview: list[Any] = Field(default_factory=list)
    kwargs_preview: dict[str, Any] = Field(default_factory=dict)
    is_running_now: bool = False


class TaskListResponse(BaseModel):
    tasks: list[TaskRecordResponse]
    total: int
    offset: int
    limit: int
    status_counts: dict[str, int]
    running_task_ids: list[str]


class QueueSummaryResponse(BaseModel):
    queue_name: str
    stream_name: str
    recent_total: int
    status_counts: dict[str, int]
    currently_running: int


class TaskOverviewResponse(BaseModel):
    timestamp: str
    total_recent_tasks: int
    running_task_ids: list[str]
    queue_summaries: list[QueueSummaryResponse]
    global_status_counts: dict[str, int]


class CancelTaskRequest(BaseModel):
    reason: str = Field(default="manual-admin")


class CancelTaskResponse(BaseModel):
    success: bool
    task_id: str
    message: str


class RetryTaskResponse(BaseModel):
    success: bool
    source_task_id: str
    new_task_id: str | None = None
    message: str


class BulkActionRequest(BaseModel):
    task_ids: list[str] | None = None
    status: str | None = None
    queue_name: str | None = None
    actor_name: str | None = None
    search: str | None = None
    limit: int = Field(default=200, ge=1, le=2000)
    reason: str = Field(default="bulk-admin")


class BulkActionResponse(BaseModel):
    success: bool
    message: str
    requested: int
    applied: int
    skipped: int
    task_ids: list[str] = Field(default_factory=list)
    new_task_ids: list[str] = Field(default_factory=list)


def _coerce_task_record(record: dict[str, Any], running_task_ids: set[str]) -> TaskRecordResponse:
    task_id = str(record.get("task_id", ""))
    args_preview = record.get("args_preview", [])
    kwargs_preview = record.get("kwargs_preview", {})
    if not isinstance(args_preview, list):
        args_preview = []
    if not isinstance(kwargs_preview, dict):
        kwargs_preview = {}

    return TaskRecordResponse(
        task_id=task_id,
        actor_name=record.get("actor_name"),
        queue_name=record.get("queue_name"),
        status=str(record.get("status", "unknown")),
        created_at=record.get("created_at"),
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        updated_at=record.get("updated_at"),
        duration_ms=record.get("duration_ms"),
        priority=record.get("priority"),
        max_retries=record.get("max_retries"),
        time_limit_ms=record.get("time_limit_ms"),
        worker_pid=record.get("worker_pid"),
        error_type=record.get("error_type"),
        error_message=record.get("error_message"),
        cancellation_requested=bool(record.get("cancellation_requested", False)),
        cancellation_reason=record.get("cancellation_reason"),
        cancellation_requested_at=record.get("cancellation_requested_at"),
        args_preview=args_preview,
        kwargs_preview=kwargs_preview,
        is_running_now=task_id in running_task_ids,
    )


def _matches_filter(
    record: TaskRecordResponse,
    *,
    status_filter: str | None,
    queue_name: str | None,
    actor_name: str | None,
    search: str | None,
) -> bool:
    if status_filter and record.status != status_filter:
        return False
    if queue_name and record.queue_name != queue_name:
        return False
    if actor_name and record.actor_name != actor_name:
        return False
    if search:
        normalized = search.lower()
        searchable = " ".join(
            [
                record.task_id,
                record.actor_name or "",
                record.queue_name or "",
                record.status,
                record.error_type or "",
                record.error_message or "",
            ]
        ).lower()
        if normalized not in searchable:
            return False
    return True


async def _load_recent_records(limit: int) -> tuple[list[TaskRecordResponse], list[str]]:
    running_task_ids = await list_running_task_ids()
    running_set = set(running_task_ids)
    records_raw = await list_task_records(limit=limit, offset=0)
    records = [_coerce_task_record(record, running_set) for record in records_raw]
    return records, running_task_ids


async def _build_task_list_payload(
    *,
    limit: int,
    offset: int,
    status_filter: str | None = None,
    queue_name: str | None = None,
    actor_name: str | None = None,
    search: str | None = None,
) -> TaskListResponse:
    fetch_size = min(max(limit * 5 + offset, 200), 2000)
    records, running_task_ids = await _load_recent_records(fetch_size)

    filtered = [
        record
        for record in records
        if _matches_filter(
            record,
            status_filter=status_filter,
            queue_name=queue_name,
            actor_name=actor_name,
            search=search,
        )
    ]
    paginated = filtered[offset : offset + limit]

    status_counts: dict[str, int] = {}
    for record in filtered:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1

    return TaskListResponse(
        tasks=paginated,
        total=len(filtered),
        offset=offset,
        limit=limit,
        status_counts=status_counts,
        running_task_ids=running_task_ids,
    )


async def _build_task_overview_payload(*, sample_size: int) -> TaskOverviewResponse:
    records, running_task_ids = await _load_recent_records(sample_size)

    global_status_counts: dict[str, int] = {}
    for record in records:
        global_status_counts[record.status] = global_status_counts.get(record.status, 0) + 1

    queue_summaries: list[QueueSummaryResponse] = []
    queue_names = get_registered_queue_names()
    for queue_name in queue_names:
        queue_records = [record for record in records if record.queue_name == queue_name]
        status_counts: dict[str, int] = {}
        for record in queue_records:
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
        currently_running = sum(1 for record in queue_records if record.is_running_now)
        queue_summaries.append(
            QueueSummaryResponse(
                queue_name=queue_name,
                stream_name=get_taskiq_queue_name(queue_name),
                recent_total=len(queue_records),
                status_counts=status_counts,
                currently_running=currently_running,
            )
        )

    return TaskOverviewResponse(
        timestamp=datetime.now(tz=UTC).isoformat(),
        total_recent_tasks=len(records),
        running_task_ids=running_task_ids,
        queue_summaries=queue_summaries,
        global_status_counts=global_status_counts,
    )


async def _resolve_bulk_targets(payload: BulkActionRequest) -> list[TaskRecordResponse]:
    running_task_ids = await list_running_task_ids()
    running_set = set(running_task_ids)
    records: list[TaskRecordResponse] = []

    if payload.task_ids:
        for task_id in payload.task_ids:
            record = await get_task_record(task_id)
            if record:
                records.append(_coerce_task_record(record, running_set))
    else:
        raw_records = await list_task_records(limit=payload.limit, offset=0)
        records = [_coerce_task_record(record, running_set) for record in raw_records]
        records = [
            record
            for record in records
            if _matches_filter(
                record,
                status_filter=payload.status,
                queue_name=payload.queue_name,
                actor_name=payload.actor_name,
                search=payload.search,
            )
        ]

    return records


@router.get("/overview", response_model=TaskOverviewResponse)
async def get_task_overview(
    sample_size: int = Query(500, ge=50, le=2000, description="Number of recent tasks to include in summary"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    return await _build_task_overview_payload(sample_size=sample_size)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    queue_name: str | None = Query(None),
    actor_name: str | None = Query(None),
    search: str | None = Query(None),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    return await _build_task_list_payload(
        limit=limit,
        offset=offset,
        status_filter=status_filter,
        queue_name=queue_name,
        actor_name=actor_name,
        search=search,
    )


@router.get("/stream")
async def stream_task_snapshots(
    request: Request,
    sample_size: int = Query(500, ge=50, le=2000),
    list_limit: int = Query(100, ge=1, le=200),
    list_offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    queue_name: str | None = Query(None),
    actor_name: str | None = Query(None),
    search: str | None = Query(None),
    interval_ms: int = Query(3000, ge=1000, le=30000),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            overview = await _build_task_overview_payload(sample_size=sample_size)
            task_list = await _build_task_list_payload(
                limit=list_limit,
                offset=list_offset,
                status_filter=status_filter,
                queue_name=queue_name,
                actor_name=actor_name,
                search=search,
            )
            payload = {
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "overview": overview.model_dump(),
                "list": task_list.model_dump(),
            }
            yield f"event: snapshot\ndata: {json.dumps(payload, default=str)}\n\n"
            await asyncio.sleep(interval_ms / 1000)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/bulk-cancel", response_model=BulkActionResponse)
async def bulk_cancel_tasks(
    payload: BulkActionRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    targets = await _resolve_bulk_targets(payload)
    requested = len(targets)
    applied = 0
    skipped = 0
    task_ids: list[str] = []

    for task in targets:
        if task.status in TERMINAL_TASK_STATUSES:
            skipped += 1
            continue
        await request_task_cancellation(task_id=task.task_id, reason=payload.reason)
        applied += 1
        task_ids.append(task.task_id)

    return BulkActionResponse(
        success=True,
        message=f"Requested cancellation for {applied} task(s); skipped {skipped}.",
        requested=requested,
        applied=applied,
        skipped=skipped,
        task_ids=task_ids,
    )


@router.post("/bulk-retry", response_model=BulkActionResponse)
async def bulk_retry_tasks(
    payload: BulkActionRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    targets = await _resolve_bulk_targets(payload)
    requested = len(targets)
    applied = 0
    skipped = 0
    source_task_ids: list[str] = []
    new_task_ids: list[str] = []

    for task in targets:
        if task.status not in RETRYABLE_TASK_STATUSES:
            skipped += 1
            continue
        try:
            new_task_id = await retry_task_by_id(task.task_id)
        except TaskRetryError:
            skipped += 1
            continue
        applied += 1
        source_task_ids.append(task.task_id)
        new_task_ids.append(new_task_id)

    return BulkActionResponse(
        success=True,
        message=f"Retried {applied} task(s); skipped {skipped}.",
        requested=requested,
        applied=applied,
        skipped=skipped,
        task_ids=source_task_ids,
        new_task_ids=new_task_ids,
    )


@router.get("/{task_id}", response_model=TaskRecordResponse)
async def get_task_detail(
    task_id: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    running_task_ids = await list_running_task_ids()
    running_set = set(running_task_ids)
    record = await get_task_record(task_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' was not found in recent history.",
        )
    return _coerce_task_record(record, running_set)


@router.post("/{task_id}/retry", response_model=RetryTaskResponse)
async def retry_task(
    task_id: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    record = await get_task_record(task_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' was not found in recent history.",
        )

    task_status = str(record.get("status", "unknown"))
    if task_status not in RETRYABLE_TASK_STATUSES:
        return RetryTaskResponse(
            success=False,
            source_task_id=task_id,
            message=f"Task status '{task_status}' is not retryable.",
        )

    try:
        new_task_id = await retry_task_by_id(task_id)
    except TaskRetryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return RetryTaskResponse(
        success=True,
        source_task_id=task_id,
        new_task_id=new_task_id,
        message="Task was retried and queued successfully.",
    )


@router.post("/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: str,
    payload: CancelTaskRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    record = await get_task_record(task_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' was not found in recent history.",
        )

    task_status = str(record.get("status", "unknown"))
    if task_status in TERMINAL_TASK_STATUSES:
        return CancelTaskResponse(
            success=False,
            task_id=task_id,
            message=f"Task is already '{task_status}' and cannot be cancelled.",
        )

    await request_task_cancellation(task_id=task_id, reason=payload.reason)
    if task_status in {"queued", "scheduled"}:
        message = (
            "Cancellation requested for queued task. "
            "It remains in the broker stream until claimed by a worker, then it is skipped/cancelled."
        )
    else:
        message = "Cancellation requested. The worker will stop the task cooperatively."
    return CancelTaskResponse(
        success=True,
        task_id=task_id,
        message=message,
    )
