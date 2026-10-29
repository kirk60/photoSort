"""Directory scanning helpers for photoSelect."""

import os
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
import subprocess
from typing import TypedDict


SUPPORTED_EXTS = {
    '.jpg', '.jpeg', '.gif', '.nef', '.nrw', '.cr2', '.cr3', '.dng',
    '.arw', '.raf', '.orf', '.rw2', '.raw', '.pef', '.srw',
}

class PhotoRecord(TypedDict):
    path: str
    date: datetime
    name: str
    dir: str


def get_file_date(filepath: str) -> datetime:
    """Return a photo's EXIF date, falling back to its modification time."""
    try:
        result = subprocess.run(
            [
                'exiftool', '-DateTimeOriginal', '-CreateDate',
                '-MediaCreateDate', '-s3', '-d', '%Y:%m:%d %H:%M:%S',
                filepath,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if result.returncode == 0:
            output = result.stdout.decode().strip()
            for line in output.splitlines():
                line = line.strip()
                if line and not line.startswith('===='):
                    try:
                        return datetime.strptime(line, '%Y:%m:%d %H:%M:%S')
                    except ValueError:
                        pass
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(os.stat(filepath).st_mtime)
    except Exception:
        return datetime.fromtimestamp(0)


def scan_directory(
    start_dir: str,
    is_running: Callable[[], bool],
    on_photo_found: Callable[[str], None],
) -> Iterator[tuple[str, list[PhotoRecord]]]:
    """Yield photos grouped by directory, stopping when the scan is cancelled."""
    start_path = Path(start_dir).resolve()
    for root_dir, _dirs, files in os.walk(start_path):
        if not is_running():
            break
        dir_photos = []
        for filename in files:
            if not is_running():
                break
            if Path(filename).suffix.lower() not in SUPPORTED_EXTS:
                continue
            filepath = os.path.join(root_dir, filename)
            dir_photos.append({
                'path': filepath,
                'date': get_file_date(filepath),
                'name': filename,
                'dir': root_dir,
            })
            on_photo_found(filepath)
        if dir_photos and is_running():
            yield root_dir, dir_photos
