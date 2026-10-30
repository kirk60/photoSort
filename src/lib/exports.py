"""Export favorited photos from a scan directory."""

import os
import subprocess
import shutil
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from lib.db_access import PhotoDB


RAW_EXTENSIONS = {
    ".arw", ".cr2", ".cr3", ".dng", ".nef", ".nrw", ".orf", ".pef",
    ".raf", ".raw", ".rw2", ".srw",
}


def _empty_output_directory(directory: Path) -> None:
    """Create the export directory and remove its previous contents."""
    for path in (directory.parent, directory):
        if path.is_symlink():
            raise OSError(f"Export path must not be a symbolic link: {path}")
    directory.mkdir(parents=True, exist_ok=True)
    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink()
        else:
            shutil.rmtree(entry)


def _save_jpeg(image: Image.Image, destination: Path) -> None:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.save(destination, format="JPEG", quality=95)


def _convert_raw_to_jpeg(source: Path, destination: Path) -> bool:
    try:
        import rawpy
    except ImportError:
        return False

    try:
        with rawpy.imread(str(source)) as raw:
            pixels = raw.postprocess(
                use_camera_wb=True,
                output_color=rawpy.ColorSpace.sRGB,
            )
        _save_jpeg(Image.fromarray(pixels), destination)
        return True
    except Exception:
        return False


def _convert_with_imagemagick(source: Path, destination: Path) -> None:
    executable = shutil.which("magick") or shutil.which("convert")
    if not executable:
        raise RuntimeError(
            f"Cannot convert {source}: install rawpy or ImageMagick to read this format."
        )

    command = [executable, str(source), "-auto-orient", "-quality", "95", str(destination)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace").strip()
        raise RuntimeError(f"Could not convert {source} to JPEG: {detail or error}") from error


def _export_photo(source: Path, destination: Path) -> Path:
    """Copy JPEGs unchanged and convert other supported formats to JPEG."""
    if source.suffix.lower() in RAW_EXTENSIONS:
        if _convert_raw_to_jpeg(source, destination):
            return destination
        _convert_with_imagemagick(source, destination)
        return destination

    try:
        image = Image.open(source)
    except (UnidentifiedImageError, OSError):
        pass
    else:
        with image:
            if image.format == "JPEG":
                shutil.copy2(source, destination)
                return destination
            _save_jpeg(image, destination)
            return destination

    _convert_with_imagemagick(source, destination)
    return destination


def export_favorites(photo_db: PhotoDB, scan_dir: str) -> tuple[Path, int]:
    """Export favorites to exports/<scan-directory-name>, converting to JPEG."""
    if photo_db.conn is None:
        raise RuntimeError("The favorites database is not available.")

    favorites = photo_db.list_favorites_for_export()
    scan_path = Path(os.path.abspath(scan_dir))
    destination_dir = Path.cwd() / "exports" / (scan_path.name or "root")

    sources = []
    destinations = []
    for filepath, export_name in favorites:
        source = Path(filepath)
        if not export_name or export_name in {".", ".."}:
            raise ValueError(f"Favorite has no valid export name: {source}")
        if Path(export_name).name != export_name:
            raise ValueError(f"Favorite has an invalid export name: {export_name}")
        extension = (
            source.suffix.lower()
            if source.suffix.lower() in {".jpg", ".jpeg"}
            else ".jpg"
        )
        sources.append(source)
        destinations.append(destination_dir / f"{export_name}{extension}")

    filenames = [destination.name.casefold() for destination in destinations]
    if len(set(filenames)) != len(filenames):
        raise ValueError("Favorites contain duplicate export names and cannot be exported.")

    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"Favorite photo does not exist: {source}")

    _empty_output_directory(destination_dir)
    if not favorites:
        return destination_dir, 0

    for source, destination in zip(sources, destinations):
        _export_photo(source, destination)

    return destination_dir, len(sources)
