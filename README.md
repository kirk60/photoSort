# photoSelect

A simple photo viewer that recursively scans a directory for photos and displays them one at a time in chronological (date) order.

## Features

- **Sequential viewing**: Browse all photos from all subdirectories in date order
- **EXIF date sorting**: Uses EXIF `DateTimeOriginal`, `CreateDate`, or `MediaCreateDate` for accurate chronological ordering; falls back to file modification time if no EXIF data is available
- **Wide format support**: Handles standard JPEG and GIF images, plus NEF, NRW, CR2, CR3, DNG, ARW, RAF, ORF, RW2, RAW, PEF, and SRW RAW formats
- **Keyboard navigation**: Fast, simple controls with arrow keys
- **Auto-resizing**: Images are scaled to fit the window while maintaining aspect ratio
- **RAW handling**: Uses Pillow when possible, falls back to ImageMagick (`convert`) for RAW formats

## Requirements

- Python 3
- [Pillow](https://python-pillow.org/) - for image loading and manipulation
- [Tkinter](https://docs.python.org/3/library/tkinter.html) - GUI (usually included with Python)
- [exiftool](https://exiftool.org/) - for reading EXIF dates
- [ImageMagick](https://imagemagick.org/) - fallback for RAW file support

On Ubuntu/Debian, these can be installed with:
```bash
sudo apt install python3-tk libimage-exiftool-perl imagemagick
pip install Pillow
```

## Usage

### Running the program

From the photoSort directory:
```bash
./photoSelect
```

Or directly with Python:
```bash
python3 src/photoSelect.py
```

By default, it scans the current directory (`.`) recursively on startup.

To start the scan in a specific subdirectory, use `--in-dir` (path relative to the current directory):
```bash
./photoSelect --in-dir photos/2024
python3 src/photoSelect.py --in-dir photos/2024
```

### Controls

| Key | Action |
|---|---|
| `←` / `→` | Previous / Next photo |
| `Home` | Jump to first photo |
| `End` | Jump to last photo |
| `r` | Rescan the current directory |
| `Esc` / `q` | Quit |

### Menu Options

- **File → Rescan Directory (r)** - Rescan the current working directory
- **File → Select Directory...** - Choose a different directory to scan
- **Actions → Populate GPS & Location Cache** - Populate GPS coordinates and look up cached place names
- **Help → Controls** - Show control reference

## How it works

1. Recursively walks through all subdirectories starting from the scan directory
2. Collects all supported image files
3. For each file, reads the capture date from EXIF metadata (preferring `DateTimeOriginal`)
4. Sorts the entire collection chronologically by date
5. Displays photos one at a time in that sorted order

## Notes

- The `path` column in `photos.db` is stored relative to the selected scan directory (`--in-dir`).
- RAW files are converted on-the-fly using ImageMagick for display
- If a photo fails to load, an error message is shown while still allowing navigation
- The window can be resized; images will scale to fit
- All photos across all subdirectories are treated as one continuous sequence

## License

This is a personal utility script.
