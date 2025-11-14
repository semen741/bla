from fastapi.testclient import TestClient

from app.main import app, store

client = TestClient(app)


def reset_store() -> None:
    store._jobs.clear()  # type: ignore[attr-defined]
    store._queue.clear()  # type: ignore[attr-defined]


def test_create_and_queue_jobs() -> None:
    reset_store()
    payload = {
        "telegram_file_id": "file-1",
        "start": 0,
        "end": 10,
        "mute": False,
        "audio_only": False,
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 200
    first_job = response.json()
    assert first_job["stage"] == "queued"
    assert first_job["position"] == 1

    response = client.post(
        "/jobs",
        json={**payload, "telegram_file_id": "file-2", "start": 5, "end": 20},
    )
    second_job = response.json()
    assert second_job["position"] == 2

    next_response = client.get("/jobs/next")
    assert next_response.status_code == 200
    processing_job = next_response.json()
    assert processing_job["stage"] == "processing"

    remaining = client.get(f"/jobs/{second_job['job_id']}").json()
    assert remaining["stage"] == "queued"
    assert remaining["position"] == 1

    final = client.post(
        f"/jobs/{processing_job['job_id']}/progress",
        json={"stage": "done", "result_file_id": "local://out.mp4"},
    )
    assert final.status_code == 200
    assert final.json()["stage"] == "done"


def test_validation_rejects_long_fragment() -> None:
    reset_store()
    response = client.post(
        "/jobs",
        json={
            "telegram_file_id": "file-3",
            "start": 0,
            "end": 120,
            "mute": False,
            "audio_only": False,
        },
    )
    assert response.status_code == 422
