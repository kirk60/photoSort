"""Directory scanning helpers for photoSelect."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _scan_subdirectory(
    root_dir: str,
    filenames: list[str],
    is_running: Callable[[], bool],
) -> tuple[str, list[PhotoRecord]]:
    """Scan all supported photos in one directory."""
    dir_photos = []
    for filename in filenames:
        if not is_running():
            break
        filepath = os.path.join(root_dir, filename)
        dir_photos.append({
            'path': filepath,
            'date': get_file_date(filepath),
            'name': filename,
            'dir': root_dir,
        })
    return root_dir, dir_photos


def scan_directory(
    start_dir: str,
    is_running: Callable[[], bool],
    threads: int = 1,
) -> Iterator[tuple[str, list[PhotoRecord]]]:
    """Yield scanned photo directories using up to *threads* workers."""
    if threads < 1:
        raise ValueError("threads must be at least 1")

    start_path = Path(start_dir).resolve()
    subdirectories: list[tuple[str, list[str]]] = []
    for root_dir, _dirs, files in os.walk(start_path):
        if not is_running():
            break
        supported_files = [
            filename for filename in files
            if Path(filename).suffix.lower() in SUPPORTED_EXTS
        ]
        if supported_files:
            subdirectories.append((root_dir, supported_files))

    if threads == 1:
        for root_dir, filenames in subdirectories:
            if not is_running():
                break
            _root_dir, dir_photos = _scan_subdirectory(
                root_dir, filenames, is_running
            )
            if dir_photos and is_running():
                yield root_dir, dir_photos
        return

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(_scan_subdirectory, root_dir, filenames, is_running):
            root_dir
            for root_dir, filenames in subdirectories
        }
        for future in as_completed(futures):
            if not is_running():
                break
            root_dir, dir_photos = future.result()
            if dir_photos and is_running():
                yield root_dir, dir_photos
