"""db_access.py - Database access layer for photoSelect.

Provides PhotoDB, a thin wrapper around the SQLite photos.db that is stored
inside each scan directory.  All SQL lives here; photoSelect.py should not
import sqlite3 directly.
"""

import os
import re
import sqlite3
import sys
from datetime import datetime, timezone


class PhotoDB:
    """Manages the photos.db SQLite database for a single scan directory."""

    DB_FILENAME = 'photos.db'

    def __init__(self, scan_dir: str):
        """Open (and migrate if necessary) the database in *scan_dir*."""
        self.scan_dir = os.path.abspath(scan_dir)
        self.db_path = os.path.join(self.scan_dir, self.DB_FILENAME)
        self.conn = None
        self._open()

    def _normalize_path(self, filepath: str) -> str:
        """Store paths inside the scan directory relative to that directory."""
        if not os.path.isabs(filepath):
            return os.path.normpath(filepath)

        absolute_path = os.path.abspath(filepath)
        try:
            if os.path.commonpath((self.scan_dir, absolute_path)) == self.scan_dir:
                return os.path.relpath(absolute_path, self.scan_dir)
        except ValueError:
            pass
        return absolute_path

    def _resolve_path(self, filepath: str) -> str:
        """Return an absolute filesystem path for a path stored in the database."""
        if os.path.isabs(filepath):
            return filepath
        return os.path.abspath(os.path.join(self.scan_dir, filepath))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _open(self):
        """Open the connection and ensure the schema is up to date."""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self._ensure_schema()
        except Exception as e:
            print(f"DB init error: {e}", file=sys.stderr)
            self.conn = None

    def _ensure_schema(self):
        """Create tables and apply migrations."""
        cur = self.conn.cursor()

        # Create table (idempotent)
        cur.execute('''CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            favorite INTEGER DEFAULT 0,
            gps_from_file TEXT,
            city_from_gps TEXT,
            country_from_gps TEXT,
            date_from_file TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            date_from_name TEXT,
            city_from_name TEXT,
            loc_details_from_name TEXT,
            export_name TEXT,
            dir_sequence INTEGER
        )''')

        cur.execute("PRAGMA table_info(photos)")
        cols = {r[1] for r in cur.fetchall()}
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='photos'")
        row = cur.fetchone()
        legacy_columns = {
            'location_cache', 'location_from_file', 'gps_location', 'gps_position',
            'image_date',
            'dir_location', 'dir_date', 'dir_location_city',
            'dir_location_details', 'location_from_name',
        }
        needs_rebuild = (
            bool(cols & legacy_columns)
            or 'date_from_file' not in cols
            or (row and row[0] and 'AUTOINCREMENT' in row[0].upper())
        )
        if needs_rebuild:
            cur.execute('''CREATE TABLE photos_new (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                favorite INTEGER DEFAULT 0,
                gps_from_file TEXT,
                city_from_gps TEXT,
                country_from_gps TEXT,
                date_from_file TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                date_from_name TEXT,
                city_from_name TEXT,
                loc_details_from_name TEXT,
                export_name TEXT,
                dir_sequence INTEGER
            )''')
            cur.execute('SELECT * FROM photos')
            old_columns = [column[0] for column in cur.description]
            for values in cur.fetchall():
                photo = dict(zip(old_columns, values))
                filepath = photo['path']
                date_from_file = next(
                    (photo[name] for name in ('date_from_file', 'image_date')
                     if photo.get(name)),
                    None,
                ) or self._get_image_date(filepath)
                city_from_gps = photo.get('city_from_gps')
                country_from_gps = photo.get('country_from_gps')
                if city_from_gps is None and country_from_gps is None:
                    old_location = (
                        photo.get('location_from_file') or photo.get('location_cache') or ''
                    )
                    city_from_gps, separator, country = old_location.partition(',')
                    city_from_gps = city_from_gps.strip() or None
                    country_from_gps = country.strip() or None if separator else None
                gps_from_file = next(
                    (photo[name] for name in (
                        'gps_from_file', 'gps_location', 'gps_position',
                    ) if photo.get(name)),
                    None,
                )
                cur.execute(
                    'INSERT INTO photos_new '
                    '(id, path, favorite, gps_from_file, city_from_gps, country_from_gps, '
                    'date_from_file, '
                    'date_from_name, city_from_name, loc_details_from_name) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (photo['id'], filepath, photo.get('favorite', 0) or 0,
                     gps_from_file, city_from_gps, country_from_gps, date_from_file,
                     photo.get('date_from_name') or photo.get('dir_date'),
                     photo.get('city_from_name') or photo.get('dir_location_city'),
                     photo.get('loc_details_from_name')
                     or photo.get('location_from_name')
                     or photo.get('dir_location_details')),
                )
            cur.execute('DROP TABLE photos')
            cur.execute('ALTER TABLE photos_new RENAME TO photos')

        cur.execute("PRAGMA table_info(photos)")
        cols = {r[1] for r in cur.fetchall()}
        for col in (
            'gps_from_file', 'city_from_gps', 'country_from_gps', 'date_from_name',
            'city_from_name', 'loc_details_from_name', 'export_name',
        ):
            if col not in cols:
                cur.execute(f'ALTER TABLE photos ADD COLUMN {col} TEXT')
        if 'dir_sequence' not in cols:
            cur.execute('ALTER TABLE photos ADD COLUMN dir_sequence INTEGER')

        cur.execute(
            'SELECT id, path, favorite, gps_from_file, city_from_gps, country_from_gps, '
            'date_from_file, '
            'date_from_name, city_from_name, loc_details_from_name '
            'FROM photos'
        )
        for (photo_id, filepath, favorite, gps_from_file, city_from_gps, country_from_gps, date_from_file,
             date_from_name, city_from_name, loc_details_from_name) in cur.fetchall():
            relative_path = self._normalize_path(filepath)
            (parsed_date_from_name, parsed_city_from_name,
             parsed_loc_details_from_name) = self._get_directory_fields(filepath)
            if (date_from_name, city_from_name, loc_details_from_name) != (
                parsed_date_from_name, parsed_city_from_name, parsed_loc_details_from_name
            ):
                cur.execute(
                    'UPDATE photos SET date_from_name = ?, city_from_name = ?, '
                    'loc_details_from_name = ? WHERE id = ?',
                    (parsed_date_from_name, parsed_city_from_name,
                     parsed_loc_details_from_name, photo_id),
                )
                (date_from_name, city_from_name,
                 loc_details_from_name) = (
                    parsed_date_from_name, parsed_city_from_name,
                    parsed_loc_details_from_name,
                )
            if relative_path == filepath:
                continue
            cur.execute('SELECT id, favorite, gps_from_file, city_from_gps, country_from_gps, '
                        'date_from_file, '
                        'date_from_name, city_from_name, loc_details_from_name '
                        'FROM photos WHERE path = ?', (relative_path,))
            existing = cur.fetchone()
            if existing:
                (existing_id, existing_favorite, existing_gps_from_file,
                 existing_city_from_gps, existing_country_from_gps,
                 existing_date_from_file, existing_date_from_name,
                 existing_city_from_name, existing_loc_details_from_name) = existing
                cur.execute(
                    'UPDATE photos SET favorite = ?, gps_from_file = ?, '
                    'city_from_gps = ?, country_from_gps = ?, date_from_file = ?, '
                    'date_from_name = ?, '
                    'city_from_name = ?, loc_details_from_name = ? WHERE id = ?',
                    (max(favorite or 0, existing_favorite or 0),
                     existing_gps_from_file or gps_from_file,
                     existing_city_from_gps or city_from_gps,
                     existing_country_from_gps or country_from_gps,
                     existing_date_from_file or date_from_file,
                     existing_date_from_name or date_from_name,
                     existing_city_from_name or city_from_name,
                     existing_loc_details_from_name or loc_details_from_name,
                     existing_id),
                )
                cur.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
            else:
                cur.execute('UPDATE photos SET path = ? WHERE id = ?',
                            (relative_path, photo_id))

        self.conn.commit()

    def _get_image_date(self, filepath: str) -> str:
        """Return the file modification time, or the current UTC time if unavailable."""
        try:
            modified = os.stat(self._resolve_path(filepath)).st_mtime
            value = datetime.fromtimestamp(modified, timezone.utc)
        except OSError:
            value = datetime.now(timezone.utc)
        return value.strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def _get_directory_fields(
        filepath: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Parse the date and location encoded in a photo's parent directory name."""
        dirname = os.path.basename(os.path.dirname(filepath))
        match = re.fullmatch(r'(\d{2}_\d{2}_\d{2})(?:\.\d+)*(?:[-_](.+))', dirname)
        if not match:
            return None, None, None

        try:
            year, month, day = map(int, match.group(1).split('_'))
            date = datetime(year + 2000, month, day)
        except ValueError:
            return None, None, None

        date_from_name = date.strftime('%Y-%m-%d')
        location_city, separator, location_details = match.group(2).partition('_')

        def spaced_camel_case(value: str) -> str:
            return re.sub(r'(?<!^)(?=[A-Z])', ' ', value)

        city_from_name = spaced_camel_case(location_city)
        loc_details_from_name = (
            spaced_camel_case(location_details.replace('_', ' '))
            if separator and location_details else None
        )
        return date_from_name, city_from_name, loc_details_from_name

    @staticmethod
    def _make_export_name(
        date: str | None,
        country: str | None,
        gps_city: str | None,
        name_city: str | None,
        location: str | None,
    ) -> str:
        """Build an underscore-separated name, omitting unavailable components."""
        city = gps_city or name_city
        return '_'.join(
            value.strip()
            for value in (date, country, city, location)
            if value and value.strip()
        )

    @classmethod
    def _refresh_export_name(cls, cur: 'sqlite3.Cursor', stored_path: str) -> None:
        cur.execute(
            'SELECT date_from_name, country_from_gps, city_from_gps, '
            'city_from_name, loc_details_from_name, dir_sequence '
            'FROM photos WHERE path = ?',
            (stored_path,),
        )
        row = cur.fetchone()
        if row:
            name = cls._make_export_name(*row[:5])
            if row[5] is not None:
                name = f'{name}_{row[5]:03d}'
            cur.execute(
                'UPDATE photos SET export_name = ? WHERE path = ?',
                (name, stored_path),
            )

    @classmethod
    def _make_sequenced_export_name(
        cls,
        date: str | None,
        country: str | None,
        gps_city: str | None,
        name_city: str | None,
        location: str | None,
        sequence: int | None,
    ) -> str:
        name = cls._make_export_name(date, country, gps_city, name_city, location)
        return f'{name}_{sequence:03d}' if sequence is not None else name

    def refresh_export_names(self) -> bool:
        """Rebuild export names from current location metadata and sequences."""
        if not self.conn:
            return False
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT id, date_from_name, country_from_gps, city_from_gps, '
                'city_from_name, loc_details_from_name, dir_sequence FROM photos'
            )
            updates = [
                (
                    self._make_sequenced_export_name(
                        date, country, gps_city, name_city, location, sequence,
                    ),
                    photo_id,
                )
                for (
                    photo_id, date, country, gps_city, name_city, location, sequence,
                ) in cur.fetchall()
            ]
            cur.executemany(
                'UPDATE photos SET export_name = ? WHERE id = ?',
                updates,
            )
            self.conn.commit()
            return True
        except Exception as error:
            self.conn.rollback()
            print(f"DB export name refresh error: {error}", file=sys.stderr)
            return False

    @staticmethod
    def _refresh_directory_order_and_names(
        cur: 'sqlite3.Cursor', directory: str | None = None,
    ) -> None:
        """Assign directory order and matching export names together."""
        if directory is None:
            cur.execute('SELECT id, path, date_from_file, date_from_name, '
                        'country_from_gps, city_from_gps, city_from_name, '
                        'loc_details_from_name FROM photos '
                        'ORDER BY date_from_file, path')
        elif directory:
            prefix = directory + os.sep
            cur.execute(
                'SELECT id, path, date_from_file, date_from_name, '
                'country_from_gps, city_from_gps, city_from_name, '
                'loc_details_from_name FROM photos '
                'WHERE substr(path, 1, ?) = ? ORDER BY date_from_file, path',
                (len(prefix), prefix),
            )
        else:
            cur.execute(
                'SELECT id, path, date_from_file, date_from_name, '
                'country_from_gps, city_from_gps, city_from_name, '
                'loc_details_from_name FROM photos '
                'WHERE instr(path, ?) = 0 ORDER BY date_from_file, path',
                (os.sep,),
            )

        sequence_by_dir = {}
        updates = []
        for (
            photo_id, filepath, _date_from_file, date, country,
            gps_city, name_city, location,
        ) in cur.fetchall():
            path_dir = os.path.dirname(filepath)
            sequence = sequence_by_dir.get(path_dir, 0) + 1
            sequence_by_dir[path_dir] = sequence
            export_name = PhotoDB._make_sequenced_export_name(
                date, country, gps_city, name_city, location, sequence,
            )
            updates.append((sequence, export_name, photo_id))
        cur.executemany(
            'UPDATE photos SET dir_sequence = ?, export_name = ? WHERE id = ?',
            updates,
        )

    def _insert_photo(
        self,
        cur: 'sqlite3.Cursor',
        filepath: str,
        refresh_directory_order: bool = True,
    ):
        """Register a photo and refresh its recorded modification time."""
        stored_path = self._normalize_path(filepath)
        date_from_name, city_from_name, loc_details_from_name = (
            self._get_directory_fields(filepath)
        )
        cur.execute(
            'INSERT INTO photos '
            '(path, date_from_file, date_from_name, city_from_name, loc_details_from_name) '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(path) DO UPDATE SET '
            'date_from_file = excluded.date_from_file, '
            'date_from_name = excluded.date_from_name, '
            'city_from_name = excluded.city_from_name, '
            'loc_details_from_name = excluded.loc_details_from_name',
            (stored_path, self._get_image_date(filepath), date_from_name,
             city_from_name, loc_details_from_name),
        )
        if refresh_directory_order:
            self._refresh_directory_order_and_names(cur, os.path.dirname(stored_path))

    def close(self):
        """Close the database connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def open_thread_connection(self) -> 'sqlite3.Connection | None':
        """Return a *new* connection for use on a background thread.

        The caller is responsible for closing it.
        """
        try:
            return sqlite3.connect(self.db_path)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Photo registration
    # ------------------------------------------------------------------

    def insert_photo(self, filepath: str):
        """Ensure *filepath* has a row in the photos table."""
        if not self.conn:
            return
        try:
            cur = self.conn.cursor()
            self._insert_photo(cur, filepath)
            self.conn.commit()
        except Exception as e:
            print(f"DB insert error: {e}", file=sys.stderr)

    def insert_photos_bulk(
        self,
        conn: 'sqlite3.Connection',
        filepaths: 'list[str]',
        update_sequences: bool = True,
    ):
        """Insert multiple paths via an existing (thread-local) *conn*."""
        if not conn:
            return
        try:
            cur = conn.cursor()
            directories = set()
            for path in filepaths:
                stored_path = self._normalize_path(path)
                directories.add(os.path.dirname(stored_path))
                self._insert_photo(cur, path, refresh_directory_order=False)
            if update_sequences:
                for directory in directories:
                    self._refresh_directory_order_and_names(cur, directory)
            conn.commit()
        except Exception:
            pass

    def refresh_directory_order_and_names(
        self, conn: 'sqlite3.Connection',
    ) -> None:
        """Refresh directory sequences and export names in one transaction."""
        cur = conn.cursor()
        self._refresh_directory_order_and_names(cur)
        conn.commit()

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------

    def get_favorite(self, filepath: str) -> bool:
        if not self.conn:
            return False
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT favorite FROM photos WHERE path = ?',
                        (self._normalize_path(filepath),))
            row = cur.fetchone()
            return bool(row[0]) if row else False
        except Exception:
            return False

    def set_favorite(self, filepath: str, fav: bool):
        if not self.conn:
            return
        try:
            cur = self.conn.cursor()
            val = 1 if fav else 0
            self._insert_photo(cur, filepath)
            cur.execute('UPDATE photos SET favorite = ? WHERE path = ?',
                        (val, self._normalize_path(filepath)))
            self.conn.commit()
        except Exception as e:
            print(f"DB update error: {e}", file=sys.stderr)

    def list_favorites(self) -> 'list[str]':
        """Return paths of all favorited photos, ordered by path."""
        if not self.conn:
            return []
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT path FROM photos WHERE favorite = 1 ORDER BY path')
            return [self._resolve_path(row[0]) for row in cur.fetchall()]
        except Exception as e:
            print(f"Error listing favorites: {e}", file=sys.stderr)
            return []

    def list_favorites_for_export(self) -> 'list[tuple[str, str | None]]':
        """Return each favorite's resolved path and stored export name."""
        if not self.conn:
            raise RuntimeError("The favorites database is not available.")
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT path, export_name FROM photos '
                'WHERE favorite = 1 ORDER BY path'
            )
            return [
                (self._resolve_path(path), export_name)
                for path, export_name in cur.fetchall()
            ]
        except sqlite3.Error as error:
            raise RuntimeError(f"Could not list favorite export names: {error}") from error

    # ------------------------------------------------------------------
    # GPS / location cache
    # ------------------------------------------------------------------

    def get_gps_cache(self, filepath: str) -> 'str | None':
        if not self.conn:
            return None
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT gps_from_file FROM photos WHERE path = ?',
                        (self._normalize_path(filepath),))
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def get_date_from_file(self, filepath: str) -> 'str | None':
        if not self.conn:
            return None
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT date_from_file FROM photos WHERE path = ?',
                        (self._normalize_path(filepath),))
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def get_directory_fields(self, filepath: str) -> 'tuple[str | None, str | None, str | None]':
        if not self.conn:
            return None, None, None
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT date_from_name, city_from_name, loc_details_from_name '
                'FROM photos WHERE path = ?',
                (self._normalize_path(filepath),),
            )
            row = cur.fetchone()
            return row if row else (None, None, None)
        except Exception:
            return None, None, None

    def set_gps_cache(self, filepath: str, gps: str):
        if not self.conn or not gps:
            return
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            self._insert_photo(cur, filepath)
            cur.execute('UPDATE photos SET gps_from_file = ? WHERE path = ?', (gps, stored_path))
            self.conn.commit()
        except Exception as e:
            print(f"DB gps cache error: {e}", file=sys.stderr)

    def get_location_cache(self, filepath: str) -> 'str | None':
        if not self.conn:
            return None
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT city_from_gps, country_from_gps FROM photos WHERE path = ?',
                        (self._normalize_path(filepath),))
            row = cur.fetchone()
            if not row:
                return None
            return ', '.join(part for part in row if part) or None
        except Exception:
            return None

    def get_gps_location(self, filepath: str) -> 'tuple[str | None, str | None]':
        if not self.conn:
            return None, None
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT city_from_gps, country_from_gps FROM photos WHERE path = ?',
                (self._normalize_path(filepath),),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, None)
        except Exception:
            return None, None

    def set_location_cache(self, filepath: str, location: str):
        if not self.conn:
            return
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            self._insert_photo(cur, filepath)
            city, separator, country = location.partition(',')
            cur.execute(
                'UPDATE photos SET city_from_gps = ?, country_from_gps = ? WHERE path = ?',
                (city.strip() or None, country.strip() or None if separator else None,
                 stored_path),
            )
            self._refresh_export_name(cur, stored_path)
            self.conn.commit()
        except Exception as e:
            print(f"DB location cache error: {e}", file=sys.stderr)

    def store_photo_gps(self, filepath: str, gps: str):
        """Insert photo row and store its GPS position in one call."""
        if not self.conn:
            return
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            self._insert_photo(cur, filepath)
            if gps and gps != 'N/A':
                cur.execute('UPDATE photos SET gps_from_file = ? WHERE path = ?',
                            (gps, stored_path))
            self.conn.commit()
        except Exception as e:
            print(f"DB store error: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Bulk population helpers (used by GPS and location cache actions)
    # ------------------------------------------------------------------

    def get_all_for_cache_population(self) -> 'list[tuple]':
        """Return (path, gps_from_file, formatted GPS location) for every known photo."""
        if not self.conn:
            return []
        try:
            cur = self.conn.cursor()
            cur.execute(
                'SELECT path, gps_from_file, city_from_gps, country_from_gps FROM photos'
            )
            return [
                (self._resolve_path(path), gps, ', '.join(
                    part for part in (city, country) if part
                ))
                for path, gps, city, country in cur.fetchall()
            ]
        except Exception:
            return []

    def update_gps_and_location(self, filepath: str, gps: str, location: str) -> bool:
        """Write GPS coordinates and split GPS-derived city/country for *filepath*."""
        if not self.conn:
            return False
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            self._insert_photo(cur, filepath)
            if gps:
                cur.execute('UPDATE photos SET gps_from_file = ? WHERE path = ?',
                            (gps, stored_path))
            if location:
                city, separator, country = location.partition(',')
                cur.execute(
                    'UPDATE photos SET city_from_gps = ?, country_from_gps = ? '
                    'WHERE path = ?',
                    (city.strip() or None, country.strip() or None if separator else None,
                     stored_path),
                )
            self._refresh_export_name(cur, stored_path)
            self.conn.commit()
            return True
        except Exception as e:
            print(f"DB GPS/location update error: {e}", file=sys.stderr)
            return False
