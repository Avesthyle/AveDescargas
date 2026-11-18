"""Interfaz Tkinter del editor multipista de AveDescargas."""

from __future__ import annotations

import os
import pathlib
import queue
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from media_editor import (
    Clip,
    EditorProject,
    MIN_CLIP_DURATION,
    Track,
    build_export_plan,
    clamp_fades,
    discover_ffmpeg_tools,
    format_editor_time,
    parse_editor_time,
    probe_media,
    run_export,
    split_clip as split_timeline_clip,
    unique_track_name,
)


BG = "#1a1a1a"
CARD = "#262626"
FIELD = "#1e1e1e"
TEXT = "#ffffff"
MUTED = "#8c8c8c"
BLUE = "#0078d4"
GREEN = "#2EA043"
YELLOW = "#F9C846"


class EditorPanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.project = EditorProject()
        self.selected_clip = None
        self.zoom = tk.DoubleVar(value=24.0)
        self.export_mode = tk.StringVar(value="Todos los canales activos")
        self.editor_queue = queue.Queue()
        self.drag_clip = None
        self.drag_offset = 0.0
        self.timeline_items = {}
        self.exporting = False
        self._destroyed = False
        self._build_ui()
        self._poll_after_id = self.after(100, self._poll_queue)

    def destroy(self):
        self._destroyed = True
        if getattr(self, "_poll_after_id", None):
            try:
                self.after_cancel(self._poll_after_id)
            except (AttributeError, tk.TclError, ValueError):
                pass
            self._poll_after_id = None
        super().destroy()

    def _button(self, parent, text, command, color="#383838", **pack):
        button = tk.Button(
            parent, text=text, command=command, font=("Segoe UI", 9),
            bg=color, fg=TEXT, activebackground="#4a4a4a",
            activeforeground=TEXT, relief="flat", bd=0, padx=9, pady=5,
        )
        button.pack(**pack)
        return button

    def _build_ui(self):
        toolbar = tk.Frame(self, bg=BG, padx=10, pady=8)
        toolbar.pack(fill="x")
        tk.Label(toolbar, text="Editor multipista", font=("Segoe UI", 15, "bold"), fg=TEXT, bg=BG).pack(side="left", padx=(0, 14))
        self._button(toolbar, "＋ Importar medios", self.import_media, BLUE, side="left", padx=(0, 6))
        self._button(toolbar, "＋ Canal vídeo", lambda: self.add_track("video"), side="left", padx=(0, 4))
        self._button(toolbar, "＋ Canal audio", lambda: self.add_track("audio"), side="left", padx=(0, 10))
        self._button(toolbar, "▶ Previsualizar", self.preview, side="right", padx=(5, 0))
        self._button(toolbar, "Exportar", self.export, GREEN, side="right", padx=(5, 0))

        mode = ttk.Combobox(
            toolbar, textvariable=self.export_mode,
            values=["Todos los canales activos", "Solo el canal seleccionado"],
            state="readonly", width=27,
        )
        mode.pack(side="right", padx=5)

        main = tk.PanedWindow(self, orient="horizontal", sashwidth=5, bg=BG, bd=0)
        main.pack(fill="both", expand=True, padx=10)

        tracks_card = tk.Frame(main, bg=CARD, width=235)
        editor_card = tk.Frame(main, bg=CARD)
        main.add(tracks_card, minsize=210)
        main.add(editor_card, minsize=420)

        tk.Label(tracks_card, text="Canales", font=("Segoe UI", 10, "bold"), fg=TEXT, bg=CARD).pack(anchor="w", padx=8, pady=(8, 4))
        self.track_tree = ttk.Treeview(tracks_card, columns=("type", "state", "mute"), show="tree headings", height=8)
        self.track_tree.heading("#0", text="Nombre")
        self.track_tree.heading("type", text="Tipo")
        self.track_tree.heading("state", text="Activo")
        self.track_tree.heading("mute", text="Audio")
        self.track_tree.column("#0", width=105)
        self.track_tree.column("type", width=48, anchor="center")
        self.track_tree.column("state", width=45, anchor="center")
        self.track_tree.column("mute", width=48, anchor="center")
        self.track_tree.pack(fill="both", expand=True, padx=8)
        self.track_tree.bind("<<TreeviewSelect>>", self._select_track)

        track_actions = tk.Frame(tracks_card, bg=CARD)
        track_actions.pack(fill="x", padx=8, pady=7)
        self._button(track_actions, "Activar", self.toggle_track, side="left", padx=(0, 3))
        self._button(track_actions, "Mutear", self.toggle_mute, side="left", padx=3)
        self._button(track_actions, "↑", lambda: self.move_track(-1), side="left", padx=3)
        self._button(track_actions, "↓", lambda: self.move_track(1), side="left", padx=3)
        self._button(track_actions, "Eliminar", self.delete_track, "#a52424", side="right")

        clips_header = tk.Frame(editor_card, bg=CARD)
        clips_header.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(clips_header, text="Clips del canal seleccionado", font=("Segoe UI", 10, "bold"), fg=TEXT, bg=CARD).pack(side="left")
        self._button(clips_header, "Dividir", self.split_clip, side="right", padx=(4, 0))
        self._button(clips_header, "Eliminar clip", self.delete_clip, "#a52424", side="right", padx=(4, 0))

        self.clip_tree = ttk.Treeview(editor_card, columns=("in", "out", "start", "duration", "audio"), show="tree headings", height=6)
        for column, label, width in (
            ("#0", "Clip", 170), ("in", "Entrada", 70), ("out", "Salida", 70),
            ("start", "Posición", 70), ("duration", "Duración", 70), ("audio", "Audio", 55),
        ):
            self.clip_tree.heading(column, text=label)
            self.clip_tree.column(column, width=width, anchor="center" if column != "#0" else "w")
        self.clip_tree.pack(fill="x", padx=8)
        self.clip_tree.bind("<<TreeviewSelect>>", self._select_clip)

        props = tk.LabelFrame(editor_card, text="Propiedades del clip", font=("Segoe UI", 9, "bold"), fg=TEXT, bg=CARD, bd=1)
        props.pack(fill="x", padx=8, pady=8)
        self.in_var = tk.StringVar(value="00:00.00")
        self.out_var = tk.StringVar(value="00:00.00")
        self.start_var = tk.StringVar(value="00:00.00")
        self.clip_audio_var = tk.BooleanVar(value=True)
        for column, (label, variable) in enumerate((("Entrada", self.in_var), ("Salida", self.out_var), ("Posición", self.start_var))):
            group = tk.Frame(props, bg=CARD)
            group.grid(row=0, column=column, padx=6, pady=6, sticky="ew")
            tk.Label(group, text=label, fg=TEXT, bg=CARD, font=("Segoe UI", 8)).pack(anchor="w")
            tk.Entry(group, textvariable=variable, width=13, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat").pack(ipady=3)
            props.columnconfigure(column, weight=1)
        self.clip_audio_check = tk.Checkbutton(
            props, text="Audio original", variable=self.clip_audio_var,
            fg=TEXT, bg=CARD, activebackground=CARD, activeforeground=TEXT,
            selectcolor=FIELD, font=("Segoe UI", 9),
        )
        self.clip_audio_check.grid(row=0, column=3, padx=8)
        tk.Button(
            props, text="Aplicar", command=self.apply_clip_properties,
            font=("Segoe UI", 9), bg=BLUE, fg=TEXT,
            activebackground="#1685d8", activeforeground=TEXT,
            relief="flat", bd=0, padx=9, pady=5,
        ).grid(row=0, column=4, padx=8)

        timeline_card = tk.Frame(self, bg=CARD)
        timeline_card.pack(fill="both", expand=False, padx=10, pady=(8, 4))
        timeline_header = tk.Frame(timeline_card, bg=CARD)
        timeline_header.pack(fill="x", padx=8, pady=5)
        tk.Label(timeline_header, text="Línea de tiempo", font=("Segoe UI", 10, "bold"), fg=TEXT, bg=CARD).pack(side="left")
        tk.Label(timeline_header, text="Zoom", fg=TEXT, bg=CARD).pack(side="right")
        tk.Scale(
            timeline_header, variable=self.zoom, from_=8, to=100, orient="horizontal",
            showvalue=False, command=lambda _value: self.redraw_timeline(),
            length=130, bg=CARD, fg=TEXT, troughcolor=FIELD, highlightthickness=0,
        ).pack(side="right", padx=5)

        canvas_frame = tk.Frame(timeline_card, bg=CARD)
        canvas_frame.pack(fill="both", expand=True, padx=8, pady=(0, 7))
        self.timeline = tk.Canvas(canvas_frame, height=165, bg="#101010", highlightthickness=0)
        xscroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.timeline.xview)
        yscroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.timeline.yview)
        self.timeline.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.timeline.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.timeline.bind("<Button-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)

        footer = tk.Frame(self, bg=BG, padx=10, pady=5)
        footer.pack(fill="x")
        self.status = tk.Label(footer, text="Crea un canal o importa un archivo para empezar.", fg=MUTED, bg=BG, anchor="w")
        self.status.pack(fill="x")

    def _current_track(self):
        return self.project.selected_track()

    def add_track(self, kind, name=None):
        track_name = name or unique_track_name([track.name for track in self.project.tracks], kind)
        track = Track(track_name, kind)
        self.project.tracks.append(track)
        self.project.selected_track_index = len(self.project.tracks) - 1
        self.refresh()
        return track

    def import_media(self):
        paths = filedialog.askopenfilenames(
            title="Importar vídeos o audios",
            filetypes=[("Vídeos y audios", "*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("Todos", "*.*")],
        )
        if not paths:
            return
        self.status.configure(text="Analizando archivos…", fg=YELLOW)
        for path in paths:
            try:
                media = probe_media(path)
                kind = "video" if media.has_video else "audio"
                track = self._current_track()
                if not track or track.kind != kind:
                    track = self.add_track(kind)
                start = max((clip.timeline_end for clip in track.clips), default=0.0)
                track.clips.append(Clip(media, timeline_start=start))
            except Exception as exc:
                messagebox.showerror("No se pudo importar", f"{path}\n\n{exc}")
        self.status.configure(text="Medios importados.", fg=GREEN)
        self.refresh()

    def _select_track(self, _event=None):
        selection = self.track_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not 0 <= index < len(self.project.tracks):
            return
        if index == self.project.selected_track_index:
            return
        self.project.selected_track_index = index
        self.selected_clip = None
        self.refresh_clips()
        self.redraw_timeline()

    def toggle_track(self):
        track = self._current_track()
        if track:
            track.enabled = not track.enabled
            self.refresh()

    def toggle_mute(self):
        track = self._current_track()
        if track:
            track.muted = not track.muted
            self.refresh()

    def move_track(self, direction):
        index = self.project.selected_track_index
        other = index + direction
        if 0 <= index < len(self.project.tracks) and 0 <= other < len(self.project.tracks):
            self.project.tracks[index], self.project.tracks[other] = self.project.tracks[other], self.project.tracks[index]
            self.project.selected_track_index = other
            self.refresh()

    def delete_track(self):
        index = self.project.selected_track_index
        if 0 <= index < len(self.project.tracks):
            del self.project.tracks[index]
            self.project.selected_track_index = max(0, min(index, len(self.project.tracks) - 1))
            self.selected_clip = None
            self.refresh()

    def _select_clip(self, _event=None):
        selection = self.clip_tree.selection()
        track = self._current_track()
        if not selection or not track:
            return
        index = int(selection[0])
        if not 0 <= index < len(track.clips):
            return
        self.selected_clip = track.clips[index]
        self._load_clip_properties()
        self.redraw_timeline()

    def _load_clip_properties(self):
        clip = self.selected_clip
        if not clip:
            return
        self.in_var.set(format_editor_time(clip.source_in))
        self.out_var.set(format_editor_time(clip.source_out))
        self.start_var.set(format_editor_time(clip.timeline_start))
        self.clip_audio_var.set(clip.use_source_audio)
        self.clip_audio_check.configure(state="normal" if clip.media.has_audio else "disabled")

    def apply_clip_properties(self):
        clip = self.selected_clip
        if not clip:
            return
        try:
            new_in = parse_editor_time(self.in_var.get())
            new_out = parse_editor_time(self.out_var.get())
            new_start = parse_editor_time(self.start_var.get())
            if new_out <= new_in:
                raise ValueError("La salida debe ser posterior a la entrada.")
            if new_out - new_in < MIN_CLIP_DURATION:
                raise ValueError("El clip debe durar al menos 0,01 s.")
            if new_out > clip.media.duration + 0.05:
                raise ValueError("La salida supera la duración del archivo.")
            clip.source_in = new_in
            clip.source_out = new_out
            clip.timeline_start = new_start
            clip.fade_in, clip.fade_out = clamp_fades(clip.fade_in, clip.fade_out, clip.duration)
            clip.use_source_audio = bool(self.clip_audio_var.get())
            self.refresh_clips()
            self.redraw_timeline()
        except ValueError as exc:
            messagebox.showerror("Tiempos no válidos", str(exc))

    def split_clip(self):
        clip = self.selected_clip
        track = self._current_track()
        clip_index = next((i for i, item in enumerate(track.clips) if item is clip), None) if (clip and track) else None
        if clip_index is None:
            return
        value = simpledialog.askstring(
            "Dividir clip",
            "Posición dentro del archivo original (segundos o HH:MM:SS):",
            initialvalue=format_editor_time(clip.source_in + clip.duration / 2),
            parent=self,
        )
        if value is None:
            return
        try:
            split = parse_editor_time(value)
            if not clip.source_in < split < clip.source_out:
                raise ValueError("El corte debe estar entre la entrada y la salida del clip.")
            timeline_cut = clip.timeline_start + (split - clip.source_in)
            parts = split_timeline_clip(clip, timeline_cut)
            if parts is None:
                raise ValueError("El corte debe dejar dos partes de al menos 0,01 s.")
            left, right = parts
            track.clips[clip_index:clip_index + 1] = [left, right]
            self.selected_clip = right
            self.refresh_clips()
            self.redraw_timeline()
            self._load_clip_properties()
        except ValueError as exc:
            messagebox.showerror("Corte no válido", str(exc))

    def delete_clip(self):
        track = self._current_track()
        clip_index = next((i for i, item in enumerate(track.clips) if item is self.selected_clip), None) if (track and self.selected_clip) else None
        if clip_index is not None:
            track.clips.pop(clip_index)
            self.selected_clip = None
            self.refresh_clips()
            self.redraw_timeline()

    def refresh(self):
        self.track_tree.delete(*self.track_tree.get_children())
        for index, track in enumerate(self.project.tracks):
            self.track_tree.insert(
                "", "end", iid=str(index), text=track.name,
                values=("V" if track.kind == "video" else "A", "Sí" if track.enabled else "No", "Mudo" if track.muted else "Activo"),
            )
        if self.project.tracks:
            index = max(0, min(self.project.selected_track_index, len(self.project.tracks) - 1))
            self.project.selected_track_index = index
            self.track_tree.selection_set(str(index))
        self.refresh_clips()
        self.redraw_timeline()

    def refresh_clips(self):
        self.clip_tree.delete(*self.clip_tree.get_children())
        track = self._current_track()
        if not track:
            return
        for index, clip in enumerate(track.clips):
            self.clip_tree.insert(
                "", "end", iid=str(index), text=clip.name,
                values=(format_editor_time(clip.source_in), format_editor_time(clip.source_out),
                        format_editor_time(clip.timeline_start), format_editor_time(clip.duration),
                        "Sí" if clip.use_source_audio and clip.media.has_audio else "No"),
            )
            if clip is self.selected_clip:
                self.clip_tree.selection_set(str(index))

    def redraw_timeline(self):
        self.timeline.delete("all")
        self.timeline_items.clear()
        px = self.zoom.get()
        row_height = 42
        label_width = 105
        duration = max(30.0, self.project.duration() + 5)
        total_width = label_width + duration * px
        total_height = max(165, 25 + len(self.project.tracks) * row_height)
        for second in range(0, int(duration) + 1, 5):
            x = label_width + second * px
            self.timeline.create_line(x, 0, x, total_height, fill="#2e2e2e")
            self.timeline.create_text(x + 3, 8, text=format_editor_time(second), fill=MUTED, anchor="nw", font=("Segoe UI", 7))
        for row, track in enumerate(self.project.tracks):
            y1 = 22 + row * row_height
            y2 = y1 + row_height - 4
            self.timeline.create_rectangle(0, y1, total_width, y2, fill="#171717", outline="#303030")
            state = "" if track.enabled else " (OFF)"
            mute = " 🔇" if track.muted else ""
            self.timeline.create_text(5, (y1 + y2) / 2, text=track.name + state + mute, fill=TEXT if track.enabled else MUTED, anchor="w", font=("Segoe UI", 8))
            for clip in track.clips:
                x1 = label_width + clip.timeline_start * px
                x2 = x1 + max(12, clip.duration * px)
                color = "#1769aa" if track.kind == "video" else "#287a3d"
                if clip is self.selected_clip:
                    outline, width = YELLOW, 2
                else:
                    outline, width = "#8ab4d6", 1
                item = self.timeline.create_rectangle(x1, y1 + 2, x2, y2 - 2, fill=color, outline=outline, width=width, tags=("clip",))
                text_item = self.timeline.create_text(x1 + 5, (y1 + y2) / 2, text=clip.name, fill=TEXT, anchor="w", font=("Segoe UI", 8), tags=("clip_text",))
                self.timeline_items[item] = (track, clip)
                self.timeline_items[text_item] = (track, clip)
        self.timeline.configure(scrollregion=(0, 0, total_width, total_height))

    def _timeline_press(self, event):
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        hits = self.timeline.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        chosen = next((item_id for item_id in reversed(hits) if item_id in self.timeline_items), None)
        if chosen is None:
            return
        track, clip = self.timeline_items[chosen]
        self.project.selected_track_index = self.project.tracks.index(track)
        self.selected_clip = clip
        self.drag_clip = clip
        self.drag_offset = x - (105 + clip.timeline_start * self.zoom.get())
        self.refresh()
        self._load_clip_properties()

    def _timeline_drag(self, event):
        if not self.drag_clip:
            return
        x = self.timeline.canvasx(event.x)
        self.drag_clip.timeline_start = max(0.0, (x - self.drag_offset - 105) / self.zoom.get())
        self.start_var.set(format_editor_time(self.drag_clip.timeline_start))
        self.redraw_timeline()

    def _timeline_release(self, _event):
        if self.drag_clip:
            self.drag_clip.timeline_start = round(self.drag_clip.timeline_start, 2)
            self.drag_clip = None
            self.refresh_clips()
            self.redraw_timeline()

    def _mode_key(self):
        return "selected" if self.export_mode.get().startswith("Solo") else "active"

    def _default_extension(self):
        tracks = self.project.export_tracks(self._mode_key())
        if any(clip.media.has_video for track in tracks for clip in track.clips):
            return ".mp4"
        first = next((clip for track in tracks for clip in track.clips), None)
        suffix = pathlib.Path(first.media.path).suffix.lower() if first else ".mp3"
        return suffix if suffix in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"} else ".mp3"

    def preview(self):
        folder = os.path.join(tempfile.gettempdir(), "AveDescargas")
        os.makedirs(folder, exist_ok=True)
        output = os.path.join(folder, f"previsualizacion{self._default_extension()}")
        self._start_export(output, preview=True)

    def export(self):
        extension = self._default_extension()
        output = filedialog.asksaveasfilename(
            title="Exportar montaje",
            defaultextension=extension,
            initialfile="montaje" + extension,
            filetypes=[
                ("Vídeo MP4", "*.mp4"), ("Vídeo MOV", "*.mov"), ("Vídeo MKV", "*.mkv"),
                ("Vídeo WEBM", "*.webm"), ("Audio MP3", "*.mp3"), ("Audio WAV", "*.wav"),
            ],
        )
        if output:
            self._start_export(output, preview=False)

    def _start_export(self, output, preview):
        if self.exporting:
            self.status.configure(text="Ya hay una exportación en curso.", fg=YELLOW)
            return
        try:
            plan = build_export_plan(
                self.project, self._mode_key(), self.project.selected_track_index,
                output, preview=preview,
            )
        except Exception as exc:
            messagebox.showerror("No se puede exportar", str(exc))
            return
        self.exporting = True
        self.status.configure(text="Preparando previsualización…" if preview else "Exportando montaje…", fg=YELLOW)

        def worker():
            try:
                result = run_export(plan, lambda line: self.editor_queue.put(("progress", line)))
                self.editor_queue.put(("done", result, preview))
            except Exception as exc:
                self.editor_queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        if self._destroyed or not self.winfo_exists():
            self._poll_after_id = None
            return
        if self._poll_after_id:
            try:
                self.after_cancel(self._poll_after_id)
            except (AttributeError, tk.TclError, ValueError):
                pass
            self._poll_after_id = None
        try:
            while True:
                try:
                    event = self.editor_queue.get_nowait()
                except queue.Empty:
                    break
                if event[0] == "progress":
                    text = event[1]
                    marker = text.split("time=", 1)[-1].split()[0] if "time=" in text else "procesando"
                    self.status.configure(text=f"Exportando: {marker}", fg=YELLOW)
                elif event[0] == "error":
                    self.exporting = False
                    self.status.configure(text="Error durante la exportación.", fg="#ff6b6b")
                    messagebox.showerror("Error de exportación", event[1])
                elif event[0] == "done":
                    self.exporting = False
                    path, preview = event[1], event[2]
                    self.status.configure(text=f"Terminado: {path}", fg=GREEN)
                    if preview:
                        try:
                            ffplay = discover_ffmpeg_tools()[2]
                            subprocess.Popen(
                                [ffplay, "-autoexit", "-window_title", "AveDescargas - Previsualización", path],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            )
                        except Exception as exc:
                            messagebox.showerror("No se pudo reproducir", str(exc))
                    else:
                        messagebox.showinfo("Exportación terminada", f"Archivo creado:\n\n{path}")
        finally:
            if not self._destroyed and self.winfo_exists():
                self._poll_after_id = self.after(100, self._poll_queue)

