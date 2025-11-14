from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Deque, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator


MAX_DURATION = 60.0
MAX_FILE_SIZE_MB = 100


class JobRequest(BaseModel):
    telegram_file_id: str = Field(..., description="Original Telegram file identifier")
    start: float = Field(..., ge=0.0, description="Start position in seconds")
    end: float = Field(..., gt=0.0, description="End position in seconds")
    mute: bool = Field(False, description="If true the resulting video note will be muted")
    audio_only: bool = Field(False, description="If true the worker produces an audio-only message")

    @validator("end")
    def validate_range(cls, end: float, values: Dict[str, float]) -> float:
        start = values.get("start", 0.0)
        if end <= start:
            raise ValueError("end must be greater than start")
        if end - start > MAX_DURATION:
            raise ValueError("Requested fragment is longer than 60 seconds")
        return end


class JobProgressUpdate(BaseModel):
    stage: str = Field(..., regex=r"^(queued|processing|done|failed)$")
    position: Optional[int] = Field(None, ge=1)
    result_file_id: Optional[str] = None
    detail: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    stage: str
    position: Optional[int]
    result_file_id: Optional[str]
    detail: Optional[str]
    created_at: datetime
    updated_at: datetime
    payload: JobRequest


@dataclass
class Job:
    job_id: str
    payload: JobRequest
    stage: str = "accepted"
    position: Optional[int] = None
    result_file_id: Optional[str] = None
    detail: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def snapshot(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id,
            stage=self.stage,
            position=self.position,
            result_file_id=self.result_file_id,
            detail=self.detail,
            created_at=self.created_at,
            updated_at=self.updated_at,
            payload=self.payload,
        )


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._queue: Deque[str] = deque()
        self._lock = Lock()

    def create(self, payload: JobRequest) -> JobStatus:
        with self._lock:
            job_id = str(uuid4())
            job = Job(job_id=job_id, payload=payload, stage="accepted")
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._recalculate_queue_positions()
            return self._jobs[job_id].snapshot()

    def get(self, job_id: str) -> JobStatus:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return job.snapshot()

    def dequeue(self) -> Optional[JobStatus]:
        with self._lock:
            if not self._queue:
                return None
            job_id = self._queue.popleft()
            job = self._jobs[job_id]
            job.stage = "processing"
            job.position = None
            job.updated_at = datetime.utcnow()
            self._recalculate_queue_positions()
            return job.snapshot()

    def update(self, job_id: str, update: JobProgressUpdate) -> JobStatus:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if update.stage == "queued":
                if job_id not in self._queue:
                    self._queue.append(job_id)
                job.stage = "queued"
                job.position = update.position
            else:
                job.stage = update.stage
                job.position = update.position
            if update.result_file_id is not None:
                job.result_file_id = update.result_file_id
            if update.detail is not None:
                job.detail = update.detail
            job.updated_at = datetime.utcnow()
            if update.stage == "queued":
                self._recalculate_queue_positions()
            return job.snapshot()

    def all(self) -> List[JobStatus]:
        with self._lock:
            return [job.snapshot() for job in self._jobs.values()]

    def _recalculate_queue_positions(self) -> None:
        for index, job_id in enumerate(self._queue):
            job = self._jobs[job_id]
            job.stage = "queued"
            job.position = index + 1
            job.updated_at = datetime.utcnow()


store = JobStore()
app = FastAPI(title="Video Note Factory")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"]
    ,
    allow_headers=["*"]
)
app.mount("/webapp", StaticFiles(directory="webapp/static", html=True), name="webapp")


@app.get("/")
def read_root() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", response_model=JobStatus)
def create_job(request: JobRequest) -> JobStatus:
    return store.create(request)


@app.get("/jobs", response_model=List[JobStatus])
def list_jobs() -> List[JobStatus]:
    return store.all()


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    try:
        return store.get(job_id)
    except KeyError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.get("/jobs/next", response_model=JobStatus, responses={204: {"description": "No jobs in queue"}})
def next_job() -> JobStatus | Response:
    job = store.dequeue()
    if job is None:
        return Response(status_code=204)
    return job


@app.post("/jobs/{job_id}/progress", response_model=JobStatus)
def update_job(job_id: str, update: JobProgressUpdate) -> JobStatus:
    try:
        return store.update(job_id, update)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
