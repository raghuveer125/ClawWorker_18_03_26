"""Work artifacts endpoints (~4 routes)."""

import random
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..deps import DATA_PATH

router = APIRouter(prefix="/api", tags=["artifacts"])

ARTIFACT_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.pptx'}
ARTIFACT_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


def _parse_artifact_work_date(raw_value: str) -> Optional[date]:
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _list_artifact_entries() -> List[Tuple[float, dict]]:
    if not DATA_PATH.exists():
        return []

    artifacts: List[Tuple[float, dict]] = []
    today = datetime.now().date()
    for agent_dir in DATA_PATH.iterdir():
        if not agent_dir.is_dir():
            continue
        sandbox_dir = agent_dir / "sandbox"
        if not sandbox_dir.exists():
            continue
        signature = agent_dir.name
        for date_dir in sandbox_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for file_path in date_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                rel_parts = file_path.relative_to(date_dir).parts
                if any(p in ('code_exec', 'videos', 'reference_files') for p in rel_parts):
                    continue
                ext = file_path.suffix.lower()
                if ext not in ARTIFACT_EXTENSIONS:
                    continue
                stat = file_path.stat()
                work_date = date_dir.name
                parsed_work_date = _parse_artifact_work_date(work_date)
                rel_path = str(file_path.relative_to(DATA_PATH))
                artifacts.append((
                    stat.st_mtime,
                    {
                        "agent": signature,
                        "work_date": work_date,
                        "work_date_is_future": bool(parsed_work_date and parsed_work_date > today),
                        "filename": file_path.name,
                        "extension": ext,
                        "size_bytes": stat.st_size,
                        "path": rel_path,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    },
                ))

    return artifacts


@router.get("/artifacts")
async def get_artifacts(
    count: int = Query(default=30, ge=1, le=100),
    sort: str = Query(default="recent"),
):
    """Get agent-produced artifacts ordered by recency or sampled randomly."""
    sort_key = (sort or "recent").strip().lower()
    if sort_key not in {"recent", "random"}:
        raise HTTPException(status_code=400, detail="sort must be one of: recent, random")

    artifacts = _list_artifact_entries()
    total = len(artifacts)

    if sort_key == "recent":
        artifacts.sort(key=lambda item: (item[0], item[1]["path"]), reverse=True)
        selected = [entry for _, entry in artifacts[:count]]
    else:
        if len(artifacts) > count:
            artifacts = random.sample(artifacts, count)
        selected = [entry for _, entry in artifacts]

    return {
        "artifacts": selected,
        "count": len(selected),
        "total": total,
        "sort": sort_key,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/artifacts/random")
async def get_random_artifacts(count: int = Query(default=30, ge=1, le=100)):
    """Backwards-compatible random artifact sample endpoint."""
    return await get_artifacts(count=count, sort="random")


@router.get("/artifacts/file")
async def get_artifact_file(path: str = Query(...)):
    """Serve an artifact file for preview/download"""
    if ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = (DATA_PATH / path).resolve()
    if not str(file_path).startswith(str(DATA_PATH.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    media_type = ARTIFACT_MIME_TYPES.get(ext, 'application/octet-stream')
    return FileResponse(file_path, media_type=media_type)
