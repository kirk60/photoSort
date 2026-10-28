"""db_access.py - Database access layer for photoSelect.

Provides PhotoDB, a thin wrapper around the SQLite photos.db that is stored
inside each scan directory.  All SQL lives here; photoSelect.py should not
import sqlite3 directly.
"""

import os
import sqlite3
import sys


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
            location_cache TEXT,
            gps_position TEXT
        )''')

        # Migrate away from AUTOINCREMENT if present
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='photos'")
        row = cur.fetchone()
        if row and row[0] and 'AUTOINCREMENT' in row[0].upper():
            cur.execute('''CREATE TABLE photos_new (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                favorite INTEGER DEFAULT 0,
                location_cache TEXT,
                gps_position TEXT
            )''')
            cur.execute('INSERT OR REPLACE INTO photos_new (id, path, favorite) '
                        'SELECT id, path, favorite FROM photos')
            cur.execute('DROP TABLE photos')
            cur.execute('ALTER TABLE photos_new RENAME TO photos')
            cur.execute('DROP TABLE IF EXISTS sqlite_sequence')

        # Add columns introduced after initial release
        cur.execute("PRAGMA table_info(photos)")
        cols = {r[1] for r in cur.fetchall()}
        for col in ('location_cache', 'gps_position'):
            if col not in cols:
                try:
                    cur.execute(f'ALTER TABLE photos ADD COLUMN {col} TEXT')
                except Exception:
                    pass

        cur.execute('SELECT id, path, favorite, location_cache, gps_position FROM photos')
        for photo_id, filepath, favorite, location, gps in cur.fetchall():
            relative_path = self._normalize_path(filepath)
            if relative_path == filepath:
                continue
            cur.execute('SELECT id, favorite, location_cache, gps_position '
                        'FROM photos WHERE path = ?', (relative_path,))
            existing = cur.fetchone()
            if existing:
                existing_id, existing_favorite, existing_location, existing_gps = existing
                cur.execute(
                    'UPDATE photos SET favorite = ?, location_cache = ?, gps_position = ? '
                    'WHERE id = ?',
                    (max(favorite or 0, existing_favorite or 0),
                     existing_location or location, existing_gps or gps, existing_id),
                )
                cur.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
            else:
                cur.execute('UPDATE photos SET path = ? WHERE id = ?',
                            (relative_path, photo_id))

        self.conn.commit()

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
            cur.execute('INSERT OR IGNORE INTO photos (path) VALUES (?)',
                        (self._normalize_path(filepath),))
            self.conn.commit()
        except Exception as e:
            print(f"DB insert error: {e}", file=sys.stderr)

    def insert_photos_bulk(self, conn: 'sqlite3.Connection', filepaths: 'list[str]'):
        """Insert multiple paths via an existing (thread-local) *conn*."""
        if not conn:
            return
        try:
            cur = conn.cursor()
            for path in filepaths:
                cur.execute('INSERT OR IGNORE INTO photos (path) VALUES (?)',
                            (self._normalize_path(path),))
            conn.commit()
        except Exception:
            pass

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
            cur.execute('INSERT OR REPLACE INTO photos (path, favorite) VALUES (?, ?)',
                        (self._normalize_path(filepath), val))
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

    # ------------------------------------------------------------------
    # GPS / location cache
    # ------------------------------------------------------------------

    def get_gps_cache(self, filepath: str) -> 'str | None':
        if not self.conn:
            return None
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT gps_position FROM photos WHERE path = ?',
                        (self._normalize_path(filepath),))
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def set_gps_cache(self, filepath: str, gps: str):
        if not self.conn or not gps:
            return
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            cur.execute('INSERT OR IGNORE INTO photos (path) VALUES (?)', (stored_path,))
            cur.execute('UPDATE photos SET gps_position = ? WHERE path = ?', (gps, stored_path))
            self.conn.commit()
        except Exception as e:
            print(f"DB gps cache error: {e}", file=sys.stderr)

    def get_location_cache(self, filepath: str) -> 'str | None':
        if not self.conn:
            return None
        try:
            cur = self.conn.cursor()
            cur.execute('SELECT location_cache FROM photos WHERE path = ?',
                        (self._normalize_path(filepath),))
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def set_location_cache(self, filepath: str, location: str):
        if not self.conn:
            return
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            cur.execute('INSERT OR IGNORE INTO photos (path) VALUES (?)', (stored_path,))
            cur.execute('UPDATE photos SET location_cache = ? WHERE path = ?',
                        (location, stored_path))
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
            cur.execute('INSERT OR IGNORE INTO photos (path) VALUES (?)', (stored_path,))
            if gps and gps != 'N/A':
                cur.execute('UPDATE photos SET gps_position = ? WHERE path = ?',
                            (gps, stored_path))
            self.conn.commit()
        except Exception as e:
            print(f"DB store error: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Bulk population helpers (used by populate_gps_location_cache)
    # ------------------------------------------------------------------

    def get_all_for_cache_population(self) -> 'list[tuple]':
        """Return (path, gps_position, location_cache) for every known photo."""
        if not self.conn:
            return []
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT path, gps_position, location_cache FROM photos")
            return [
                (self._resolve_path(path), gps, location)
                for path, gps, location in cur.fetchall()
            ]
        except Exception:
            return []

    def update_gps_and_location(self, filepath: str, gps: str, location: str) -> bool:
        """Write both gps_position and location_cache for *filepath*."""
        if not self.conn:
            return False
        try:
            cur = self.conn.cursor()
            stored_path = self._normalize_path(filepath)
            cur.execute('INSERT OR IGNORE INTO photos (path) VALUES (?)', (stored_path,))
            if gps:
                cur.execute('UPDATE photos SET gps_position = ? WHERE path = ?',
                            (gps, stored_path))
            if location:
                cur.execute('UPDATE photos SET location_cache = ? WHERE path = ?',
                            (location, stored_path))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"DB GPS/location update error: {e}", file=sys.stderr)
            return False
