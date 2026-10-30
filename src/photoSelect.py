#!/usr/bin/env python3
import argparse
import os
import sys
import subprocess
import json
import tempfile
import threading
import queue
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from lib.db_access import PhotoDB
from pathlib import Path
from PIL import Image
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
import numpy as np
from lib.display_mgr import DisplayManager
from lib.dir_scan import scan_directory
from lib.exports import export_favorites
from lib import photo_metadata

try:
    import requests
except ImportError:
    requests = None

try:
    import rawpy
    HAS_RAWPY = True
except ImportError:
    rawpy = None
    HAS_RAWPY = False

GPS_UNAVAILABLE = "Not Available"


class PhotoSelect:
    def __init__(self, root, scan_threads=1):
        self.root = root
        self.root.title("photoSelect")
        self.root.geometry("1400x800")
        self.root.configure(bg='black')
        
        self.photos = []
        self.photos_lock = threading.Lock()
        self.current_index = 0
        self.photo_image = None
        self.db = None
        self.scan_root = '.'
        self.scan_threads = scan_threads
        self.scan_thread = None
        self.scan_running = False
        self.update_queue = queue.Queue()
        self._skip_tree_select = False
        
        # Main container
        self.main_paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True)
        
        # Left panel - directory tree
        self.left_frame = tk.Frame(self.main_paned, bg='#1e1e1e', width=300)
        self.main_paned.add(self.left_frame, weight=0)
        
        tree_label = tk.Label(self.left_frame, text="Images", bg='#1e1e1e', fg='white', font=('Arial', 9, 'bold'))
        tree_label.pack(fill=tk.X, padx=5, pady=2)
        
        # Treeview with scrollbar
        tree_frame = tk.Frame(self.left_frame, bg='#1e1e1e')
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.tree_scroll = tk.Scrollbar(tree_frame)
        self.tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree = ttk.Treeview(tree_frame, yscrollcommand=self.tree_scroll.set, show='tree')
        self.tree_scroll.config(command=self.tree.yview)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<Double-1>', self.on_tree_select)
        self.tree.bind('<Return>', self.on_tree_select)
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select_from_tree)
        
        # Style the treeview
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview', background='#252526', foreground='white', fieldbackground='#252526', borderwidth=0)
        style.map('Treeview', background=[('selected', '#094771')])
        
        self.display = DisplayManager(
            self.main_paned, self.toggle_favorite, self.save_comment
        )
        
        # Bind keys
        self.root.bind('<Left>', self.prev_photo)
        self.root.bind('<Right>', self.next_photo)
        self.root.bind('<Escape>', lambda e: self.quit_app())
        self.root.bind('<q>', lambda e: self.quit_app())
        self.root.bind('<Home>', self.first_photo)
        self.root.bind('<End>', self.last_photo)
        self.root.bind('<r>', self.rescan)
        self.root.bind('<f>', self.toggle_favorite)
        self.root.bind('<F>', self.toggle_favorite)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        
        # Menu
        menubar = tk.Menu(root)
        root.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Toggle Favorite (f)", command=self.toggle_favorite)
        file_menu.add_command(label="Rescan Directory (r)", command=self.rescan)
        file_menu.add_command(label="Select Directory...", command=self.select_directory)
        file_menu.add_command(label="Export", command=self.export)
        file_menu.add_separator()
        file_menu.add_command(label="Exit (Esc/q)", command=self.quit_app)
        
        actions_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Actions", menu=actions_menu)
        actions_menu.add_command(
            label="Populate GPS Cache", command=self.populate_gps_cache
        )
        actions_menu.add_command(
            label="Populate Location Cache", command=self.populate_location_cache
        )
        
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Controls", command=self.show_controls)
        
        # Start scan is triggered by main() with the correct directory
    
    def init_db(self):
        if self.db:
            self.db.close()
        self.db = PhotoDB(self.scan_root)
    
    def get_favorite(self, filepath):
        return self.db.get_favorite(filepath) if self.db else False
    
    def get_location_cache(self, filepath):
        return self.db.get_location_cache(filepath) if self.db else None

    def get_gps_cache(self, filepath):
        return self.db.get_gps_cache(filepath) if self.db else None

    def set_gps_cache(self, filepath, gps):
        if self.db:
            self.db.set_gps_cache(filepath, gps)

    def set_location_cache(self, filepath, location):
        if self.db:
            self.db.set_location_cache(filepath, location)

    def store_photo_data(self, filepath):
        if not self.db:
            return
        gps = photo_metadata.get_gps_position(filepath)
        self.db.store_photo_gps(filepath, gps)

    def load_location(self, filepath):
        try:
            self.store_photo_data(filepath)
            gps_cached = self.get_gps_cache(filepath)
            if gps_cached:
                gps = gps_cached
            else:
                gps = photo_metadata.get_gps_position(filepath)
                if gps and gps not in ("N/A", GPS_UNAVAILABLE):
                    self.set_gps_cache(filepath, gps)
            model = photo_metadata.get_camera_model(filepath)
            gps = gps if gps and gps not in ("N/A", GPS_UNAVAILABLE) else (
                GPS_UNAVAILABLE if gps == GPS_UNAVAILABLE else None
            )
            date_from_file = self.db.get_date_from_file(filepath) if self.db else None
            gps_city, gps_location = (
                self.db.get_gps_location(filepath) if self.db else (None, None)
            )
            user_date, user_city, user_details = (
                self.db.get_directory_fields(filepath)
                if self.db else (None, None, None)
            )
            self.display.set_metadata(
                model, date_from_file, gps, gps_city, gps_location,
                user_date, user_city, user_details,
            )
        except Exception:
            self.display.reset_metadata()
    
    def save_comment(self):
        if not self.photos:
            return
        filepath = self.photos[self.current_index]['path']
        comment = self.display.get_comment()
        try:
            # Try to write Comment, also write Description for compatibility
            if photo_metadata.set_comment(filepath, comment):
                self.display.set_status("Comment saved")
                self.root.after(2000, lambda: self.update_info_label())
            else:
                messagebox.showerror("Error", "Failed to save comment")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save comment: {e}")
    
    def update_info_label(self):
        if not self.photos:
            return
        photo_info = self.photos[self.current_index]
        filepath = photo_info['path']
        date_str = photo_info['date'].strftime('%Y-%m-%d %H:%M:%S')
        rel_path = os.path.relpath(filepath)
        fav_status = "★ " if self.get_favorite(filepath) else ""
        info_text = f"[{self.current_index + 1}/{len(self.photos)}] {fav_status}{rel_path} | {date_str}"
        self.display.set_status(info_text)
    
    def load_comment(self, filepath):
        try:
            self.display.set_comment(photo_metadata.get_comment(filepath))
        except Exception:
            pass
    
    def set_favorite(self, filepath, fav):
        if self.db:
            self.db.set_favorite(filepath, fav)
    
    def toggle_favorite(self, event=None):
        if not self.photos:
            return
        filepath = self.photos[self.current_index]['path']
        current = self.get_favorite(filepath)
        new_val = not current
        self.set_favorite(filepath, new_val)
        self.update_favorite_display(filepath)
        self.populate_tree()
        # Try to reselect current item
        try:
            self.select_tree_item(self.current_index)
        except Exception:
            pass
    
    def select_tree_item(self, idx):
        if getattr(self, '_skip_tree_select', False):
            return
        # Find and select the tree item for this photo index
        def search(node):
            for child in self.tree.get_children(node):
                values = self.tree.item(child, 'values')
                if values:
                    try:
                        if int(values[0]) == idx:
                            self.tree.selection_set(child)
                            self.tree.see(child)
                            return True
                    except:
                        pass
                if search(child):
                    return True
            return False
        search('')
    
    def update_favorite_display(self, filepath=None):
        if not self.photos:
            self.display.clear_favorite()
            return
        if filepath is None:
            filepath = self.photos[self.current_index]['path']
        self.display.set_favorite(self.get_favorite(filepath))
    
    def format_dir_name(self, dir_path):
        basename = os.path.basename(dir_path)
        # Try to parse format YY_MM_DD_Location or YY_MM_DD-Location
        # Handle both _ and - as separators
        normalized = basename.replace('-', '_')
        parts = normalized.split('_')
        if len(parts) >= 4 and len(parts[0]) == 2 and parts[0].isdigit() and len(parts[1]) == 2 and parts[1].isdigit() and len(parts[2]) == 2 and parts[2].isdigit():
            try:
                yy = int(parts[0])
                mm = parts[1]
                dd = parts[2]
                year = 2000 + yy
                location = '_'.join(parts[3:]).replace('_', ' ')
                return f"{year}-{mm}-{dd} - {location}"
            except ValueError:
                pass
        # Fallback
        return basename.replace('_', ' ')
    
    def populate_tree(self, rebuild_all=False):
        # If rebuilding all, clear tree
        if rebuild_all:
            for item in self.tree.get_children():
                self.tree.delete(item)
        
        with self.photos_lock:
            photos_snapshot = list(self.photos)
        
        if not photos_snapshot:
            if rebuild_all:
                pass
            return
        
        # Group by directory
        dirs_dict = {}
        for i, photo in enumerate(photos_snapshot):
            d = photo['dir']
            if d not in dirs_dict:
                dirs_dict[d] = []
            dirs_dict[d].append((i, photo))
        
        # Sort directories by name, images within each directory by filename
        sorted_dirs = sorted(dirs_dict.keys(), key=lambda d: os.path.basename(d).lower())
        
        # Rebuild for simplicity
        for item in self.tree.get_children():
            self.tree.delete(item)
        for d in sorted_dirs:
            label = self.format_dir_name(d)
            node = self.tree.insert('', 'end', text=label, open=True)
            for idx, photo in sorted(dirs_dict[d], key=lambda x: x[1]['name'].lower()):
                fav = self.get_favorite(photo['path'])
                disp = f"{'★ ' if fav else ''}{photo['name']}"
                self.tree.insert(node, 'end', text=disp, values=(idx, photo['path']))
    
    def on_tree_select_from_tree(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        values = self.tree.item(item, 'values')
        if values:
            try:
                idx = int(values[0])
                if idx != self.current_index:
                    self._skip_tree_select = True
                    try:
                        self.show_photo(idx)
                    finally:
                        self._skip_tree_select = False
            except Exception:
                pass

    def on_tree_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        values = self.tree.item(item, 'values')
        if values:
            try:
                idx = int(values[0])
                if idx != self.current_index:
                    self.show_photo(idx)
            except Exception:
                pass
    
    def process_queue(self):
        try:
            while True:
                msg = self.update_queue.get_nowait()
                if msg[0] == 'dir_complete':
                    # Update tree with newly found photos
                    self.populate_tree(rebuild_all=False)
                    with self.photos_lock:
                        count = len(self.photos)
                    self.display.set_status(f"Scanning... Found {count} photos so far")
                elif msg[0] == 'scan_complete':
                    with self.photos_lock:
                        photos_snapshot = list(self.photos)
                    photos_snapshot.sort(key=lambda x: x['date'])
                    with self.photos_lock:
                        self.photos = photos_snapshot
                    self.populate_tree(rebuild_all=True)
                    self.current_index = 0
                    if self.photos:
                        self.display.set_status(f"Found {len(self.photos)} photos. Loading...")
                        self.root.update()
                        self.show_photo(0)
                    else:
                        self.display.set_status(
                            "No photos found. Press 'r' to rescan or select a directory."
                        )
                    self.scan_running = False
                elif msg[0] == 'status':
                    self.display.set_status(msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)
    
    def scan_directory(self, start_dir):
        # Stop any existing scan
        self.scan_running = False
        if self.scan_thread and self.scan_thread.is_alive():
            pass  # Let it finish naturally
        
        self.scan_root = os.path.abspath(start_dir)
        if self.db:
            self.db.close()
        self.init_db()
        with self.photos_lock:
            self.photos = []
        for item in self.tree.get_children():
            self.tree.delete(item)
        # Clear or prepare DB entries for this scan? Just ensure we insert
        self.display.set_status("Scanning for photos in background...")
        self.root.update()
        
        self.scan_running = True
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(start_dir,), daemon=True)
        self.scan_thread.start()
        self.root.after(100, self.process_queue)
    
    def _scan_worker(self, start_dir):
        try:
            # Use a dedicated DB connection for this thread
            db_conn = self.db.open_thread_connection() if self.db else None
            for root_dir, dir_photos in scan_directory(
                start_dir,
                lambda: self.scan_running,
                threads=self.scan_threads,
            ):
                with self.photos_lock:
                    self.photos.extend(dir_photos)
                if self.db:
                    self.db.insert_photos_bulk(
                        db_conn,
                        [p['path'] for p in dir_photos],
                        update_sequences=False,
                    )
                self.update_queue.put(('dir_complete', root_dir))
            if self.scan_running:
                if self.db and db_conn:
                    self.db.refresh_directory_order_and_names(db_conn)
                self.update_queue.put(('scan_complete', None))
            if db_conn:
                try:
                    db_conn.close()
                except Exception:
                    pass
        except Exception as e:
            self.update_queue.put(('status', f"Scan error: {e}"))
            self.scan_running = False
    
    def rescan(self, event=None):
        self.scan_directory('.')
    
    def select_directory(self):
        directory = filedialog.askdirectory(title="Select Directory to Scan")
        if directory:
            self.scan_directory(directory)

    def export(self):
        if not self.db:
            messagebox.showerror("Export failed", "The favorites database is not available.")
            return
        try:
            destination_dir, exported_count = export_favorites(self.db, self.scan_root)
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("Export failed", str(error))
            return

        if exported_count == 0:
            messagebox.showinfo("Export", "No favorite photos to export.")
            return
        messagebox.showinfo(
            "Export complete",
            f"Exported {exported_count} favorite photos to:\n{destination_dir}",
        )
    
    def show_controls(self):
        messagebox.showinfo("Controls", 
                          "Navigation:\n"
                          "← / → - Previous / Next photo\n"
                          "Home - First photo\n"
                          "End - Last photo\n"
                          "f - Toggle favorite\n"
                          "Double-click or Enter on tree item to jump to photo\n"
                          "r - Rescan current directory\n"
                          "Esc / q - Quit\n\n"
                          "Comments are read/written via exiftool to the image file.\n"
                          "Favorites are saved to photos.db\n\n"
                          "Supported: JPG, JPEG, GIF, NEF, NRW, CR2, CR3, DNG, ARW, RAF, ORF, RW2, RAW, PEF, SRW")
    
    def load_image(self, filepath):
        try:
            ext = Path(filepath).suffix.lower()
            # For RAW files, use rawpy for best quality if available
            if ext in {'.nef', '.nrw', '.cr2', '.cr3', '.dng', '.arw', '.raf', '.orf', '.rw2', '.raw', '.pef', '.srw'}:
                if HAS_RAWPY:
                    try:
                        with rawpy.imread(filepath) as raw:
                            # Use better default processing for display
                            rgb = raw.postprocess(
                                use_camera_wb=True,
                                half_size=False,
                                no_auto_bright=False,
                                output_color=rawpy.ColorSpace.sRGB,
                                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                                gamma=(2.222, 4.5),
                                user_sat=None,
                                bright=1.0
                            )
                        img = Image.fromarray(rgb)
                        return img
                    except Exception as e:
                        print(f"rawpy failed for {filepath}: {e}, falling back", file=sys.stderr)
                # Fallback to ImageMagick with better quality settings
                try:
                    temp_file = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
                    temp_file.close()
                    # Use magick with better quality and auto-orient
                    subprocess.run(['magick', filepath, '-auto-orient', '-quality', '95', 
                                  '-colorspace', 'sRGB', temp_file.name], 
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                    img = Image.open(temp_file.name)
                    img.load()
                    os.unlink(temp_file.name)
                    return img
                except Exception:
                    try:
                        subprocess.run(['convert', filepath, '-auto-orient', '-quality', '95', temp_file.name], 
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                        img = Image.open(temp_file.name)
                        img.load()
                        os.unlink(temp_file.name)
                        return img
                    except Exception as e:
                        raise e
            else:
                img = Image.open(filepath)
                return img
        except Exception as e:
            raise Exception(f"Failed to load {filepath}: {e}")
    
    def show_photo(self, index):
        if not self.photos or index < 0 or index >= len(self.photos):
            return
        
        self.current_index = index
        photo_info = self.photos[index]
        filepath = photo_info['path']
        
        self.update_favorite_display(filepath)
        self.load_comment(filepath)
        try:
            self.store_photo_data(filepath)
        except Exception:
            pass
        self.load_location(filepath)
        if not getattr(self, '_skip_tree_select', False):
            try:
                self._skip_tree_select = True
                self.select_tree_item(index)
            finally:
                self._skip_tree_select = False
        
        try:
            self.display.set_status(f"Loading {photo_info['name']}...")
            self.root.update()
            
            img = self.load_image(filepath)
            self.display.show_image(img)
            
            # Update info
            date_str = photo_info['date'].strftime('%Y-%m-%d %H:%M:%S')
            rel_path = os.path.relpath(filepath)
            fav_status = "★ " if self.get_favorite(filepath) else ""
            info_text = f"[{index + 1}/{len(self.photos)}] {fav_status}{rel_path} | {date_str}"
            self.display.set_status(info_text)
            
        except Exception as e:
            self.display.show_image_error(e)
            date_str = photo_info['date'].strftime('%Y-%m-%d %H:%M:%S')
            rel_path = os.path.relpath(filepath)
            fav_status = "★ " if self.get_favorite(filepath) else ""
            info_text = f"[{index + 1}/{len(self.photos)}] {fav_status}{rel_path} | {date_str} | ERROR"
            self.display.set_status(info_text)
    
    def next_photo(self, event=None):
        if self.photos:
            self.show_photo((self.current_index + 1) % len(self.photos))
    
    def prev_photo(self, event=None):
        if self.photos:
            self.show_photo((self.current_index - 1) % len(self.photos))
    
    def first_photo(self, event=None):
        if self.photos:
            self.show_photo(0)
    
    def last_photo(self, event=None):
        if self.photos:
            self.show_photo(len(self.photos) - 1)
    
    def list_favorites(self):
        favorites = self.db.list_favorites() if self.db else []
        if not favorites:
            print("\n=== No favorites ===")
            return
        print(f"\n=== Favorites ({len(favorites)}) ===")
        for i, path in enumerate(favorites, 1):
            print(f"{i}. {path}")
        print("=" * 40)

    def populate_gps_cache(self):
        self._populate_cache(populate_locations=False)

    def populate_location_cache(self):
        self._populate_cache(populate_locations=True)

    def _populate_cache(self, populate_locations):
        if not self.db:
            messagebox.showerror("Error", "No database connection")
            return
        if populate_locations and requests is None:
            messagebox.showerror("Error", "requests not available")
            return
        self._cancel_populate = False
        (
            to_process,
            already,
            skipped_nef,
            missing_gps,
        ) = photo_metadata.prepare_cache_population(
            self.db.get_all_for_cache_population(), populate_locations
        )
        if not to_process:
            status = "Nothing to populate."
            if skipped_nef:
                status += f" Skipped {skipped_nef} NEF files."
            if populate_locations and missing_gps:
                status += f" Skipped {missing_gps} files without cached GPS data."
            self.display.set_status(status)
            self.root.after(2000, lambda: self.update_info_label())
            return
        total = len(to_process) + already + skipped_nef + (
            missing_gps if populate_locations else 0
        )
        popup = tk.Toplevel(self.root)
        cache_name = "Location" if populate_locations else "GPS"
        popup.title(f"Populating {cache_name} Cache")
        popup.geometry("400x120")
        popup.transient(self.root)
        popup.grab_set()
        label = tk.Label(
            popup,
            text=(f"To populate: {len(to_process)}, Already populated: {already}, "
                  f"Skipped NEF: {skipped_nef}, "
                  f"Missing GPS: {missing_gps if populate_locations else 0}, "
                  f"Total: {total}"),
        )
        label.pack(pady=(10,5))
        count_label = tk.Label(popup, text=f"Processed: 0 / {len(to_process)}")
        count_label.pack(pady=(0,5))
        progress = ttk.Progressbar(popup, length=350, mode='determinate', maximum=total)
        progress.pack(pady=5)
        progress['value'] = already + skipped_nef + (
            missing_gps if populate_locations else 0
        )
        def cancel():
            self._cancel_populate = True
            btn.config(state='disabled')
        btn = tk.Button(popup, text="Cancel", command=cancel)
        btn.pack(pady=5)
        popup.update()
        count_done = 0
        failures = []
        last_request = None
        idx = 0
        gps_executor = None
        if populate_locations:
            location_lock = threading.Lock()

            def reverse_geocode(gps):
                nonlocal last_request
                with location_lock:
                    if self._cancel_populate:
                        return None
                    if last_request is not None:
                        delay = 1.0 - (time.monotonic() - last_request)
                        if delay > 0:
                            time.sleep(delay)
                    last_request = time.monotonic()
                return photo_metadata.reverse_geocode_gps(gps, requests)

            paths_by_gps = {}
            for path, gps in to_process:
                paths_by_gps.setdefault(gps, []).append(path)
            executor = ThreadPoolExecutor(max_workers=self.scan_threads)
            pending = {
                executor.submit(reverse_geocode, gps): gps
                for gps in paths_by_gps
            }
            while pending and not self._cancel_populate:
                count_label.config(
                    text=f"Processed: {idx} / {len(to_process)}"
                )
                self.display.set_status(
                    f"Populating location cache... {idx}/{len(to_process)}"
                )
                popup.update()
                completed, _ = wait(
                    pending, timeout=0.1, return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    gps = pending.pop(future)
                    paths = paths_by_gps[gps]
                    try:
                        display = future.result()
                        if display is None:
                            idx += len(paths)
                            progress['value'] = already + skipped_nef + missing_gps + idx
                            popup.update()
                            continue
                        for path in paths:
                            if self.db.update_gps_and_location(path, gps, display):
                                count_done += 1
                            else:
                                failures.append(f"{path}: database update failed")
                    except ValueError as error:
                        failures.extend(f"{path}: {error}" for path in paths)
                    except Exception as error:
                        if requests is None or not isinstance(error, requests.RequestException):
                            executor.shutdown(wait=True, cancel_futures=True)
                            raise
                        status_code = error.response.status_code if error.response is not None else "unknown"
                        failures.extend(
                            f"{path}: Reverse geocoding failed (HTTP {status_code})"
                            for path in paths
                        )
                    idx += len(paths)
                    progress['value'] = already + skipped_nef + missing_gps + idx
                    popup.update()
            if self._cancel_populate:
                for future in pending:
                    future.cancel()
            executor.shutdown(
                wait=True,
                cancel_futures=self._cancel_populate,
            )

        else:
            gps_executor = ThreadPoolExecutor(max_workers=self.scan_threads)
            gps_futures = {
                path: gps_executor.submit(photo_metadata.get_gps_position, path)
                for path, gps_pos in to_process
                if not gps_pos or gps_pos in ("N/A", GPS_UNAVAILABLE)
            }
        for path, gps_pos in (() if populate_locations else to_process):
            if self._cancel_populate:
                break
            idx += 1
            count_label.config(text=f"Processed: {idx} / {len(to_process)}")
            self.display.set_status(
                f"Populating {cache_name.lower()} cache... {idx}/{len(to_process)}"
            )
            popup.update()
            gps = gps_pos
            if not populate_locations and (not gps or gps in ("N/A", GPS_UNAVAILABLE)):
                try:
                    gps = gps_futures[path].result()
                except Exception as error:
                    failures.append(f"{path}: GPS metadata read failed: {error}")
                    progress['value'] = already + skipped_nef + idx
                    popup.update()
                    continue
            if not gps or gps in ("N/A", GPS_UNAVAILABLE):
                missing_gps += 1
                progress['value'] = already + skipped_nef + idx
                popup.update()
                continue
            if not populate_locations:
                self.db.set_gps_cache(path, gps)
                count_done += 1
                progress['value'] = already + skipped_nef + idx
                popup.update()
                continue
            progress['value'] = already + skipped_nef + missing_gps + idx
            popup.update()
        if gps_executor:
            gps_executor.shutdown(
                wait=True,
                cancel_futures=self._cancel_populate,
            )
        try:
            popup.destroy()
        except Exception:
            pass
        if populate_locations and not self.db.refresh_export_names():
            messagebox.showerror(
                "Export names not updated",
                "Location cache processing finished, but export names could not be refreshed.",
            )
        if populate_locations:
            status = (
                f"Done. Cached locations for {count_done} photos. "
                f"Skipped {skipped_nef} NEF files and {missing_gps} files "
                "without cached GPS data."
            )
        else:
            status = (
                f"Done. Cached GPS for {count_done} photos. "
                f"Skipped {skipped_nef} NEF files and {missing_gps} files without GPS data."
            )
        if failures:
            status += f" {len(failures)} locations failed."
        self.display.set_status(status)
        if failures:
            messagebox.showwarning(
                f"{cache_name} cache incomplete",
                f"Could not populate locations for {len(failures)} photos.\n\n{failures[0]}",
            )
        self.root.after(3000, lambda: self.update_info_label())
    
    def quit_app(self):
        self.list_favorites()
        if self.db:
            self.db.close()
        self.root.quit()

def main():
    parser = argparse.ArgumentParser(description='photoSelect - browse photos chronologically')
    parser.add_argument('--in-dir', metavar='DIR', default='.',
                        help='directory to start the photo scan in (relative to cwd, default: current directory)')
    parser.add_argument(
        '--threads', metavar='N', type=int, default=1,
        help='number of parallel directory-scanning threads (default: 1)',
    )
    args = parser.parse_args()

    if args.threads < 1:
        parser.error('--threads must be at least 1')

    scan_dir = os.path.normpath(os.path.join(os.getcwd(), args.in_dir))
    if not os.path.isdir(scan_dir):
        print(f"Error: '{args.in_dir}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    root = tk.Tk()
    app = PhotoSelect(root, scan_threads=args.threads)
    app.scan_directory(scan_dir)
    root.mainloop()

if __name__ == '__main__':
    main()
