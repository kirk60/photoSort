"""Tkinter display components and rendering for photoSelect."""

import tkinter as tk

from PIL import Image, ImageTk


class DisplayManager:
    """Owns the photo display, information panel, and display controls."""

    def __init__(self, parent, toggle_favorite, save_comment):
        self.right_frame = tk.Frame(parent, bg='black')
        parent.add(self.right_frame, weight=1)

        self.img_loc_frame = tk.Frame(self.right_frame, bg='black')
        self.img_loc_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas_frame = tk.Frame(self.img_loc_frame, bg='black')
        self.canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(self.canvas_frame, bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.tk_image = None

        self.location_frame = tk.Frame(self.img_loc_frame, bg='#1e1e1e', width=220)
        self.location_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.location_frame.pack_propagate(False)

        self._create_information_panel()

        self.info_label = tk.Label(
            self.right_frame, text="", bg='black', fg='white', font=('Arial', 10)
        )
        self.info_label.pack(side=tk.BOTTOM, fill=tk.X)

        control_frame = tk.Frame(self.right_frame, bg='black')
        control_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.favorite_btn = tk.Button(
            control_frame, text="☆ Favorite (f)", command=toggle_favorite,
            bg='#333333', fg='white', activebackground='#555555',
            activeforeground='white',
        )
        self.favorite_btn.pack(side=tk.RIGHT, padx=5, pady=2)
        self.favorite_label = tk.Label(
            control_frame, text="", bg='black', fg='yellow', font=('Arial', 9)
        )
        self.favorite_label.pack(side=tk.RIGHT, padx=5)

        comment_frame = tk.Frame(self.right_frame, bg='#1e1e1e')
        comment_frame.pack(side=tk.BOTTOM, fill=tk.X)
        comment_label = tk.Label(
            comment_frame, text="Comment:", bg='#1e1e1e', fg='white',
            font=('Arial', 9),
        )
        comment_label.pack(side=tk.LEFT, padx=(5, 2), pady=2)
        self.comment_text = tk.Text(
            comment_frame, height=3, bg='#252526', fg='white',
            insertbackground='white', font=('Arial', 9), wrap=tk.WORD,
        )
        self.comment_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=2)
        comment_scroll = tk.Scrollbar(comment_frame, command=self.comment_text.yview)
        comment_scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.comment_text.config(yscrollcommand=comment_scroll.set)
        save_comment_btn = tk.Button(
            comment_frame, text="Save Comment", command=save_comment,
            bg='#333333', fg='white', activebackground='#555555',
            activeforeground='white',
        )
        save_comment_btn.pack(side=tk.LEFT, padx=5, pady=2)

    def _create_information_panel(self):
        frame = self.location_frame
        title = tk.Label(
            frame, text="Info", bg='#1e1e1e', fg='white',
            font=('Arial', 9, 'bold'),
        )
        title.pack(fill=tk.X, padx=5, pady=(5, 3))

        self.date_from_name_label = self._add_info_field(
            frame, "User Supplied Data", "Date", section=True,
        )
        self.city_from_name_label = self._add_info_field(frame, "City")
        self.loc_details_from_name_label = self._add_info_field(frame, "Details")

        self.model_label = self._add_info_field(
            frame, "Camera Information", "Camera Model", section=True,
        )
        self.date_from_file_label = self._add_info_field(frame, "Date")
        self.gps_label = self._add_info_field(frame, "GPS Position")
        self.city_from_gps_label = self._add_info_field(frame, "City")
        self.location_from_gps_label = self._add_info_field(frame, "Location")

    @staticmethod
    def _add_info_field(frame, section_title, field_title=None, section=False):
        if section:
            title = tk.Label(
                frame, text=section_title, bg='#1e1e1e', fg='white',
                font=('Arial', 9, 'bold'),
            )
            title.pack(anchor='w', padx=5, pady=(5, 3))
            label_text = field_title
        else:
            label_text = section_title

        field_title_label = tk.Label(
            frame, text=f"{label_text}:", bg='#1e1e1e', fg='white',
            font=('Arial', 8, 'bold'),
        )
        field_title_label.pack(anchor='w', padx=(15, 5), pady=(2, 0))
        value_label = tk.Label(
            frame, text="N/A", bg='#1e1e1e', fg='#cccccc',
            font=('Arial', 8), wraplength=180, justify='left',
        )
        value_label.pack(anchor='w', padx=(25, 5), pady=(0, 5))
        return value_label

    def set_status(self, text):
        self.info_label.config(text=text)

    def set_metadata(
        self, model, date_from_file, gps, gps_city, gps_location,
        date_from_name, city_from_name, location_details_from_name,
    ):
        self.model_label.config(text=model or 'N/A')
        self.date_from_file_label.config(text=date_from_file or 'N/A')
        self.gps_label.config(text=gps or 'N/A')
        self.city_from_gps_label.config(text=gps_city or 'N/A')
        self.location_from_gps_label.config(text=gps_location or 'N/A')
        self.date_from_name_label.config(text=date_from_name or 'N/A')
        self.city_from_name_label.config(text=city_from_name or 'N/A')
        self.loc_details_from_name_label.config(
            text=location_details_from_name or 'N/A'
        )

    def reset_metadata(self):
        self.set_metadata(None, None, None, None, None, None, None, None)

    def set_favorite(self, is_favorite):
        if is_favorite:
            self.favorite_label.config(text="★ FAVORITE")
            self.favorite_btn.config(text="★ Unfavorite (f)")
        else:
            self.favorite_label.config(text="")
            self.favorite_btn.config(text="☆ Favorite (f)")

    def clear_favorite(self):
        self.favorite_label.config(text="")
        self.favorite_btn.config(text="☆ Favorite (f)")

    def get_comment(self):
        return self.comment_text.get('1.0', tk.END).strip()

    def set_comment(self, comment):
        self.comment_text.delete('1.0', tk.END)
        if comment:
            self.comment_text.insert('1.0', comment)

    def show_image(self, image):
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            canvas_width, canvas_height = 1200, 750

        image_width, image_height = image.size
        ratio = min(canvas_width / image_width, canvas_height / image_height)
        resized = image.resize(
            (int(image_width * ratio), int(image_height * ratio)),
            Image.Resampling.LANCZOS,
        )
        self.tk_image = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(
            canvas_width // 2, canvas_height // 2,
            anchor=tk.CENTER, image=self.tk_image,
        )

    def show_image_error(self, error):
        self.canvas.delete("all")
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            text=f"Error loading image:\n{error}",
            fill='red', font=('Arial', 12), justify=tk.CENTER,
        )
