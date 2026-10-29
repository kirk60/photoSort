"""Photo metadata and reverse-geocoding helpers."""

import os
import re
import subprocess
from pathlib import Path


GPS_UNAVAILABLE = "N/A"


def get_comment(filepath):
    try:
        result = subprocess.run(
            [
                'exiftool', '-Comment', '-Description', '-Caption-Abstract',
                '-XMP:Description', '-s3', filepath,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if result.returncode == 0:
            output = result.stdout.decode('utf-8', errors='ignore').strip()
            for line in output.splitlines():
                line = line.strip()
                if line and not line.startswith('===='):
                    return line
        return ""
    except Exception:
        return ""


def set_comment(filepath, comment):
    result = subprocess.run(
        [
            'exiftool', '-overwrite_original', f'-Comment={comment}',
            f'-Description={comment}', f'-Caption-Abstract={comment}', filepath,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return result.returncode == 0


def get_gps_position(filepath):
    try:
        return _read_gps_position(filepath) or GPS_UNAVAILABLE
    except Exception:
        return GPS_UNAVAILABLE


def _read_gps_position(filepath):
    """Return the file's GPS position, or None when it has no GPS metadata."""
    result = subprocess.run(
        ['exiftool', '-GPSPosition', '-s3', filepath],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    if result.returncode != 0:
        error = result.stderr.decode('utf-8', errors='ignore').strip()
        raise RuntimeError(error or f"exiftool exited with status {result.returncode}")

    output = result.stdout.decode('utf-8', errors='ignore').strip()
    for line in output.splitlines():
        line = line.strip()
        if line and not line.startswith('Warning') and not line.startswith('Error') and ':' not in line:
            return line
        elif ':' in line and not line.startswith('Warning'):
            parts = line.split(':', 1)
            if len(parts) == 2:
                value = parts[1].strip()
                if value:
                    return value
    if output and ':' in output and not output.startswith('Warning'):
        parts = output.split(':', 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
    if output and not output.startswith('Warning'):
        return output
    return None


def get_camera_model(filepath):
    try:
        result = subprocess.run(
            ['exiftool', '-Model', '-CameraModelName', '-Make', '-s3', filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if result.returncode == 0:
            output = result.stdout.decode('utf-8', errors='ignore').strip()
            models = []
            for line in output.splitlines():
                line = line.strip()
                if line and not line.startswith('Warning') and not line.startswith('Error'):
                    if ':' in line:
                        value = line.split(':', 1)[1].strip()
                        if value and value not in models:
                            models.append(value)
                    elif line not in models:
                        models.append(line)
            if models:
                return ' '.join(models)
        return "N/A"
    except Exception:
        return "N/A"


def format_location(address, fallback=None):
    """Format a reverse-geocoded address as a locality and country."""
    locality = next(
        (address.get(key) for key in ('city', 'town', 'village', 'municipality', 'suburb')
         if address.get(key)),
        None,
    )
    if not locality:
        locality = address.get('county') or address.get('state')
    country = address.get('country')
    parts = [part for part in (locality, country) if part]
    return ', '.join(parts) if parts else fallback


def gps_coordinates(gps):
    """Parse an ExifTool GPS position into decimal latitude and longitude."""
    dms_pattern = (
        r"(\d+(?:\.\d+)?)\s*(?:deg|°)\s*(\d+(?:\.\d+)?)\s*['′]\s*"
        r"(\d+(?:\.\d+)?)\s*[\"″]\s*([NSEWnsew])"
    )
    dms_matches = re.findall(dms_pattern, gps)
    if len(dms_matches) >= 2:
        coordinates = []
        for degrees, minutes, seconds, hemisphere in dms_matches[:2]:
            decimal = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
            if hemisphere.upper() in ('S', 'W'):
                decimal = -decimal
            coordinates.append(decimal)
        latitude, longitude = coordinates
    else:
        numbers = re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', gps)
        if len(numbers) < 2:
            raise ValueError(f"Could not parse GPS coordinates: {gps}")
        latitude, longitude = float(numbers[0]), float(numbers[1])
        if re.search(r'\bS\b', gps, re.I):
            latitude = -abs(latitude)
        if re.search(r'\bN\b', gps, re.I):
            latitude = abs(latitude)
        if re.search(r'\bW\b', gps, re.I):
            longitude = -abs(longitude)
        if re.search(r'\bE\b', gps, re.I):
            longitude = abs(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError(f"GPS coordinates out of range: {gps}")
    return latitude, longitude


def reverse_geocode_gps(gps, requests_module):
    """Look up a GPS position and return its locality and country."""
    latitude, longitude = gps_coordinates(gps)
    response = requests_module.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={'lat': latitude, 'lon': longitude, 'format': 'jsonv2'},
        headers={'User-Agent': 'photoSelect/1.0'},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Geocoding service returned an invalid response")
    address = data.get('address')
    if not isinstance(address, dict) or not address.get('country'):
        raise ValueError("Geocoding response did not include a country")
    location = format_location(address)
    if not location or ',' not in location:
        raise ValueError("Geocoding response did not include a locality")
    return location


def has_duplicate_location_parts(location):
    parts = [part.strip() for part in (location or '').split(',')]
    return len(parts) == 2 and parts[0] and parts[0] == parts[1]


def prepare_cache_population(rows, populate_locations):
    """Filter cached-photo rows and count entries that need no processing."""
    to_process = []
    already = 0
    skipped_nef = 0
    missing_gps = 0
    for path, gps_position, location in rows:
        if not os.path.exists(path):
            continue
        if Path(path).suffix.lower() == '.nef':
            skipped_nef += 1
            continue
        has_gps = gps_position and gps_position not in (GPS_UNAVAILABLE, "Not Available")
        if populate_locations:
            if location and location.strip() and not has_duplicate_location_parts(location):
                already += 1
                continue
            if not has_gps:
                missing_gps += 1
                continue
        elif has_gps:
            already += 1
            continue
        to_process.append((path, gps_position))
    return to_process, already, skipped_nef, missing_gps
