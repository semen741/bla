from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import httpx
from PIL import Image, ImageDraw

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "2"))
MASK_SIZE = int(os.getenv("WORKER_MASK_SIZE", "512"))
RESULT_BUCKET = Path(os.getenv("WORKER_RESULT_DIR", tempfile.gettempdir()))
RESULT_BUCKET.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def ensure_source(file_id: str, destination: Path) -> Path:
    sample_path = os.getenv("WORKER_TEST_VIDEO_PATH")
    if sample_path and Path(sample_path).exists():
        shutil.copyfile(sample_path, destination)
        return destination

    logger.warning("WORKER_TEST_VIDEO_PATH не задан, генерирую заглушку")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x640:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000",
        "-shortest",
        "-t",
        "5",
        str(destination),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return destination


def fast_trim(source: Path, start: float, end: float, destination: Path) -> Path:
    duration = min(end - start, 60.0)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-i",
        str(source),
        "-t",
        str(duration),
        "-c",
        "copy",
        str(destination),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        logger.warning("Fast trim failed, falling back to re-encode")
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(duration),
            str(destination),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return destination


def build_mask(path: Path, size: int) -> Path:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, size, size), fill=(255, 255, 255, 255))
    image.save(path)
    return path


def render_video(trimmed: Path, output: Path, mute: bool, mask_path: Path) -> Path:
    filter_complex = (
        "[0:v]scale={s}:{s}:force_original_aspect_ratio=decrease,".format(s=MASK_SIZE)
        + "pad={s}:{s}:(ow-iw)/2:(oh-ih)/2,format=rgba[base];".format(s=MASK_SIZE)
        + "[base][1:v]alphamerge[outv]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(trimmed),
        "-loop",
        "1",
        "-i",
        str(mask_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    cmd.append(str(output))
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output


def render_audio(trimmed: Path, output: Path) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(trimmed),
        "-vn",
        "-c:a",
        "libopus",
        str(output),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output


def process_job(job: Dict[str, Any]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        source = tmpdir / "source.mp4"
        ensure_source(job["payload"]["telegram_file_id"], source)

        trimmed = tmpdir / "trimmed.mp4"
        fast_trim(source, job["payload"]["start"], job["payload"]["end"], trimmed)

        mask = tmpdir / "mask.png"
        build_mask(mask, MASK_SIZE)

        if job["payload"]["audio_only"]:
            output = RESULT_BUCKET / f"{job['job_id']}.ogg"
            render_audio(trimmed, output)
        else:
            output = RESULT_BUCKET / f"{job['job_id']}.mp4"
            render_video(trimmed, output, job["payload"].get("mute", False), mask)
        return f"local://{output.name}"


def update_stage(client: httpx.Client, job_id: str, stage: str, **extra: Any) -> None:
    payload = {"stage": stage}
    payload.update(extra)
    client.post(f"{API_BASE_URL}/jobs/{job_id}/progress", json=payload, timeout=10)


def main() -> None:
    logger.info("Worker started")
    with httpx.Client() as client:
        while True:
            response = client.get(f"{API_BASE_URL}/jobs/next", timeout=10)
            if response.status_code == 204:
                time.sleep(POLL_INTERVAL)
                continue
            response.raise_for_status()
            job = response.json()
            job_id = job["job_id"]
            logger.info("Processing job %s", job_id)
            update_stage(client, job_id, "processing")
            try:
                result_id = process_job(job)
                update_stage(client, job_id, "done", result_file_id=result_id)
            except Exception as exc:  # pragma: no cover - heavy operation
                logger.exception("Job %s failed", job_id)
                update_stage(client, job_id, "failed", detail=str(exc))


if __name__ == "__main__":
    main()
