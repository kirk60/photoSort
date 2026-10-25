#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import tempfile
import sqlite3
import threading
import queue
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
import numpy as np

try:
    import rawpy
    HAS_RAWPY = True
except ImportError:
    rawpy = None
    HAS_RAWPY = False

SUPPORTED_EXTS = {'.jpg', '.jpeg', '.gif', '.nef', '.nrw', '.cr2', '.cr3', '.dng', '.arw', '.raf', '.orf', '.rw2', '.raw', '.pef', '.srw'}
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'photos.db')

class PhotoSelect:
    def __init__(self, root):
        self.root = root
        self.root.title("photoSelect")
        self.root.geometry("1400x800")
        self.root.configure(bg='black')
        
        self.photos = []
        self.photos_lock = threading.Lock()
        self.current_index = 0
        self.photo_image = None
        self.tk_image = None
        self.db_conn = None
        self.scan_root = '.'
        self.scan_thread = None
        self.scan_running = False
        self.update_queue = queue.Queue()
        self.init_db()
        
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
        
        # Style the treeview
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview', background='#252526', foreground='white', fieldbackground='#252526', borderwidth=0)
        style.map('Treeview', background=[('selected', '#094771')])
        
        # Right panel - image display
        self.right_frame = tk.Frame(self.main_paned, bg='black')
        self.main_paned.add(self.right_frame, weight=1)
        
        # Image and location container
        self.img_loc_frame = tk.Frame(self.right_frame, bg='black')
        self.img_loc_frame.pack(fill=tk.BOTH, expand=True)
        
        # Canvas frame
        self.canvas_frame = tk.Frame(self.img_loc_frame, bg='black')
        self.canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Create canvas for image
        self.canvas = tk.Canvas(self.canvas_frame, bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Location panel (right side)
        self.location_frame = tk.Frame(self.img_loc_frame, bg='#1e1e1e', width=220)
        self.location_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.location_frame.pack_propagate(False)
        
        loc_title = tk.Label(self.location_frame, text="Info", bg='#1e1e1e', fg='white', font=('Arial', 9, 'bold'))
        loc_title.pack(fill=tk.X, padx=5, pady=(5,3))
        
        # GPS Position
        gps_label = tk.Label(self.location_frame, text="GPS Position:", bg='#1e1e1e', fg='white', font=('Arial', 8, 'bold'))
        gps_label.pack(anchor='w', padx=5, pady=(5,0))
        self.gps_label = tk.Label(self.location_frame, text="N/A", bg='#1e1e1e', fg='#cccccc', font=('Arial', 8), wraplength=200, justify='left')
        self.gps_label.pack(anchor='w', padx=5, pady=(2,5))
        
        # Model
        model_label = tk.Label(self.location_frame, text="Camera Model:", bg='#1e1e1e', fg='white', font=('Arial', 8, 'bold'))
        model_label.pack(anchor='w', padx=5, pady=(5,0))
        self.model_label = tk.Label(self.location_frame, text="N/A", bg='#1e1e1e', fg='#cccccc', font=('Arial', 8), wraplength=200, justify='left')
        self.model_label.pack(anchor='w', padx=5, pady=(2,5))
        
        # Info label
        self.info_label = tk.Label(self.right_frame, text="", bg='black', fg='white', font=('Arial', 10))
        self.info_label.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Control frame for favorite button
        self.control_frame = tk.Frame(self.right_frame, bg='black')
        self.control_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.favorite_btn = tk.Button(self.control_frame, text="☆ Favorite (f)", command=self.toggle_favorite,
                                     bg='#333333', fg='white', activebackground='#555555', activeforeground='white')
        self.favorite_btn.pack(side=tk.RIGHT, padx=5, pady=2)
        self.favorite_label = tk.Label(self.control_frame, text="", bg='black', fg='yellow', font=('Arial', 9))
        self.favorite_label.pack(side=tk.RIGHT, padx=5)
        
        # Comment frame
        self.comment_frame = tk.Frame(self.right_frame, bg='#1e1e1e')
        self.comment_frame.pack(side=tk.BOTTOM, fill=tk.X)
        comment_label = tk.Label(self.comment_frame, text="Comment:", bg='#1e1e1e', fg='white', font=('Arial', 9))
        comment_label.pack(side=tk.LEFT, padx=(5,2), pady=2)
        self.comment_text = tk.Text(self.comment_frame, height=3, bg='#252526', fg='white', insertbackground='white',
                                    font=('Arial', 9), wrap=tk.WORD)
        self.comment_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=2)
        self.comment_scroll = tk.Scrollbar(self.comment_frame, command=self.comment_text.yview)
        self.comment_scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.comment_text.config(yscrollcommand=self.comment_scroll.set)
        self.save_comment_btn = tk.Button(self.comment_frame, text="Save Comment", command=self.save_comment,
                                         bg='#333333', fg='white', activebackground='#555555', activeforeground='white')
        self.save_comment_btn.pack(side=tk.LEFT, padx=5, pady=2)
        
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
        file_menu.add_separator()
        file_menu.add_command(label="Exit (Esc/q)", command=self.quit_app)
        
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Controls", command=self.show_controls)
        
        # Start scanning current directory
        self.scan_directory('.')
    
    def init_db(self):
        try:
            self.db_conn = sqlite3.connect(DB_PATH)
            cur = self.db_conn.cursor()
            # Always use INTEGER PRIMARY KEY without AUTOINCREMENT
            cur.execute('''CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                favorite INTEGER DEFAULT 0
            )''')
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='photos'")
            row = cur.fetchone()
            if row and row[0] and 'AUTOINCREMENT' in row[0].upper():
                # Migrate away from AUTOINCREMENT
                cur.execute('''CREATE TABLE photos_new (
                    id INTEGER PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL,
                    favorite INTEGER DEFAULT 0
                )''')
                cur.execute('INSERT OR REPLACE INTO photos_new (id, path, favorite) SELECT id, path, favorite FROM photos')
                cur.execute('DROP TABLE photos')
                cur.execute('ALTER TABLE photos_new RENAME TO photos')
                cur.execute('DROP TABLE IF EXISTS sqlite_sequence')
            self.db_conn.commit()
        except Exception as e:
            print(f"DB init error: {e}", file=sys.stderr)
            self.db_conn = None
    
    def get_favorite(self, filepath):
        if not self.db_conn:
            return False
        try:
            cur = self.db_conn.cursor()
            cur.execute('SELECT favorite FROM photos WHERE path = ?', (filepath,))
            row = cur.fetchone()
            if row:
                return bool(row[0])
            return False
        except Exception:
            return False
    
    def get_comment(self, filepath):
        try:
            result = subprocess.run(['exiftool', '-Comment', '-Description', '-Caption-Abstract', 
                                   '-XMP:Description', '-s3', filepath], 
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore').strip()
                if output:
                    # Get first non-empty line
                    for line in output.splitlines():
                        line = line.strip()
                        if line and not line.startswith('===='):
                            return line
            return ""
        except Exception:
            return ""
    
    def get_gps_position(self, filepath):
        try:
            result = subprocess.run(['exiftool', '-GPSPosition', '-s3', filepath],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore').strip()
                for line in output.splitlines():
                    line = line.strip()
                    if line and not line.startswith('Warning') and not line.startswith('Error') and ':' not in line:
                        return line
                    elif ':' in line and not line.startswith('Warning'):
                        # Sometimes format is "GPS Position : ..."
                        parts = line.split(':', 1)
                        if len(parts) == 2:
                            val = parts[1].strip()
                            if val:
                                return val
                if output and ':' in output and not output.startswith('Warning'):
                    parts = output.split(':', 1)
                    if len(parts) == 2 and parts[1].strip():
                        return parts[1].strip()
                if output and not output.startswith('Warning'):
                    return output
            return "N/A"
        except Exception:
            return "N/A"
    
    def get_camera_model(self, filepath):
        try:
            result = subprocess.run(['exiftool', '-Model', '-CameraModelName', '-Make', '-s3', filepath],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore').strip()
                models = []
                for line in output.splitlines():
                    line = line.strip()
                    if line and not line.startswith('Warning') and not line.startswith('Error'):
                        if ':' in line:
                            parts = line.split(':', 1)
                            val = parts[1].strip()
                            if val and val not in models:
                                models.append(val)
                        elif line and line not in models:
                            models.append(line)
                if models:
                    return ' '.join(models)
            return "N/A"
        except Exception:
            return "N/A"
    
    def load_location(self, filepath):
        try:
            gps = self.get_gps_position(filepath)
            model = self.get_camera_model(filepath)
            self.gps_label.config(text=gps if gps else "N/A")
            self.model_label.config(text=model if model else "N/A")
        except Exception:
            self.gps_label.config(text="N/A")
            self.model_label.config(text="N/A")
    
    def save_comment(self):
        if not self.photos:
            return
        filepath = self.photos[self.current_index]['path']
        comment = self.comment_text.get('1.0', tk.END).strip()
        try:
            # Try to write Comment, also write Description for compatibility
            result = subprocess.run(['exiftool', '-overwrite_original', 
                                   f'-Comment={comment}', f'-Description={comment}', 
                                   f'-Caption-Abstract={comment}', filepath],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if result.returncode == 0:
                self.info_label.config(text="Comment saved")
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
        self.info_label.config(text=info_text)
    
    def load_comment(self, filepath):
        try:
            self.comment_text.delete('1.0', tk.END)
            comment = self.get_comment(filepath)
            if comment:
                self.comment_text.insert('1.0', comment)
        except Exception:
            pass
    
    def set_favorite(self, filepath, fav):
        if not self.db_conn:
            return
        try:
            cur = self.db_conn.cursor()
            val = 1 if fav else 0
            cur.execute('INSERT OR REPLACE INTO photos (path, favorite) VALUES (?, ?)',
                        (filepath, val))
            self.db_conn.commit()
        except Exception as e:
            print(f"DB update error: {e}", file=sys.stderr)
    
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
            self.favorite_label.config(text="")
            self.favorite_btn.config(text="☆ Favorite (f)")
            return
        if filepath is None:
            filepath = self.photos[self.current_index]['path']
        fav = self.get_favorite(filepath)
        if fav:
            self.favorite_label.config(text="★ FAVORITE")
            self.favorite_btn.config(text="★ Unfavorite (f)")
        else:
            self.favorite_label.config(text="")
            self.favorite_btn.config(text="☆ Favorite (f)")
    
    def get_exif_date(self, filepath):
        try:
            result = subprocess.run(['exiftool', '-DateTimeOriginal', '-CreateDate', '-MediaCreateDate', 
                                   '-s3', '-d', '%Y:%m:%d %H:%M:%S', filepath], 
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            if result.returncode == 0:
                output = result.stdout.decode().strip()
                if output:
                    # Get first non-empty line
                    for line in output.splitlines():
                        line = line.strip()
                        if line and not line.startswith('===='):
                            try:
                                return datetime.strptime(line, '%Y:%m:%d %H:%M:%S')
                            except:
                                pass
        except Exception as e:
            pass
        return None
    
    def get_file_date(self, filepath):
        # Try EXIF first
        exif_date = self.get_exif_date(filepath)
        if exif_date:
            return exif_date
        # Fall back to file modification time
        try:
            stat = os.stat(filepath)
            return datetime.fromtimestamp(stat.st_mtime)
        except:
            return datetime.fromtimestamp(0)
    
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
        
        # Sort directories
        sorted_dirs = sorted(dirs_dict.keys())
        
        # Rebuild for simplicity
        for item in self.tree.get_children():
            self.tree.delete(item)
        for d in sorted_dirs:
            label = self.format_dir_name(d)
            node = self.tree.insert('', 'end', text=label, open=True)
            for idx, photo in dirs_dict[d]:
                fav = self.get_favorite(photo['path'])
                disp = f"{'★ ' if fav else ''}{photo['name']}"
                self.tree.insert(node, 'end', text=disp, values=(idx, photo['path']))
    
    def on_tree_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        values = self.tree.item(item, 'values')
        if values:
            try:
                idx = int(values[0])
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
                    self.info_label.config(text=f"Scanning... Found {count} photos so far")
                elif msg[0] == 'scan_complete':
                    with self.photos_lock:
                        photos_snapshot = list(self.photos)
                    photos_snapshot.sort(key=lambda x: x['date'])
                    with self.photos_lock:
                        self.photos = photos_snapshot
                    self.populate_tree(rebuild_all=True)
                    self.current_index = 0
                    if self.photos:
                        self.info_label.config(text=f"Found {len(self.photos)} photos. Loading...")
                        self.root.update()
                        self.show_photo(0)
                    else:
                        self.info_label.config(text="No photos found. Press 'r' to rescan or select a directory.")
                    self.scan_running = False
                elif msg[0] == 'status':
                    self.info_label.config(text=msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)
    
    def scan_directory(self, start_dir):
        # Stop any existing scan
        self.scan_running = False
        if self.scan_thread and self.scan_thread.is_alive():
            pass  # Let it finish naturally
        
        self.scan_root = os.path.abspath(start_dir)
        with self.photos_lock:
            self.photos = []
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.info_label.config(text="Scanning for photos in background...")
        self.root.update()
        
        self.scan_running = True
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(start_dir,), daemon=True)
        self.scan_thread.start()
        self.root.after(100, self.process_queue)
    
    def _scan_worker(self, start_dir):
        try:
            start_path = Path(start_dir).resolve()
            # Walk directory tree
            for root_dir, dirs, files in os.walk(start_path):
                if not self.scan_running:
                    break
                dir_photos = []
                for filename in files:
                    if not self.scan_running:
                        break
                    ext = Path(filename).suffix.lower()
                    if ext in SUPPORTED_EXTS:
                        filepath = os.path.join(root_dir, filename)
                        file_date = self.get_file_date(filepath)
                        dir_photos.append({
                            'path': filepath,
                            'date': file_date,
                            'name': filename,
                            'dir': root_dir
                        })
                if dir_photos and self.scan_running:
                    with self.photos_lock:
                        self.photos.extend(dir_photos)
                    self.update_queue.put(('dir_complete', root_dir))
            if self.scan_running:
                self.update_queue.put(('scan_complete', None))
        except Exception as e:
            self.update_queue.put(('status', f"Scan error: {e}"))
            self.scan_running = False
    
    def rescan(self, event=None):
        self.scan_directory('.')
    
    def select_directory(self):
        directory = filedialog.askdirectory(title="Select Directory to Scan")
        if directory:
            self.scan_directory(directory)
    
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
        self.load_location(filepath)
        try:
            self.select_tree_item(index)
        except Exception:
            pass
        
        try:
            self.info_label.config(text=f"Loading {photo_info['name']}...")
            self.root.update()
            
            img = self.load_image(filepath)
            
            # Resize to fit canvas
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            if canvas_width <= 1 or canvas_height <= 1:
                canvas_width = 1200
                canvas_height = 750
            
            img_width, img_height = img.size
            ratio = min(canvas_width / img_width, canvas_height / img_height)
            new_width = int(img_width * ratio)
            new_height = int(img_height * ratio)
            
            img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self.tk_image = ImageTk.PhotoImage(img_resized)
            
            # Clear canvas and display
            self.canvas.delete("all")
            x = canvas_width // 2
            y = canvas_height // 2
            self.canvas.create_image(x, y, anchor=tk.CENTER, image=self.tk_image)
            
            # Update info
            date_str = photo_info['date'].strftime('%Y-%m-%d %H:%M:%S')
            rel_path = os.path.relpath(filepath)
            fav_status = "★ " if self.get_favorite(filepath) else ""
            info_text = f"[{index + 1}/{len(self.photos)}] {fav_status}{rel_path} | {date_str}"
            self.info_label.config(text=info_text)
            
        except Exception as e:
            self.canvas.delete("all")
            self.canvas.create_text(self.canvas.winfo_width()//2, self.canvas.winfo_height()//2, 
                                  text=f"Error loading image:\n{str(e)}", fill='red', 
                                  font=('Arial', 12), justify=tk.CENTER)
            date_str = photo_info['date'].strftime('%Y-%m-%d %H:%M:%S')
            rel_path = os.path.relpath(filepath)
            fav_status = "★ " if self.get_favorite(filepath) else ""
            info_text = f"[{index + 1}/{len(self.photos)}] {fav_status}{rel_path} | {date_str} | ERROR"
            self.info_label.config(text=info_text)
    
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
        if not self.db_conn:
            return
        try:
            cur = self.db_conn.cursor()
            cur.execute('SELECT path FROM photos WHERE favorite = 1 ORDER BY path')
            favorites = cur.fetchall()
            if not favorites:
                print("\n=== No favorites ===")
                return
            print(f"\n=== Favorites ({len(favorites)}) ===")
            for i, (path,) in enumerate(favorites, 1):
                print(f"{i}. {path}")
            print("=" * 40)
        except Exception as e:
            print(f"Error listing favorites: {e}", file=sys.stderr)
    
    def quit_app(self):
        self.list_favorites()
        if self.db_conn:
            self.db_conn.close()
        self.root.quit()

def main():
    root = tk.Tk()
    app = PhotoSelect(root)
    root.mainloop()

if __name__ == '__main__':
    main()
