"""Editor visual sencillo: visor, marcas de entrada/salida y línea de tiempo."""

from __future__ import annotations

from io import BytesIO
import os
import pathlib
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from media_editor import (
    Clip,
    EditorProject,
    Track,
    build_export_plan,
    clamp_media_time,
    discover_ffmpeg_tools,
    format_editor_time,
    normalize_selection,
    playback_range,
    build_pitch_filter,
    audio_media_from_video,
    avoid_clip_overlap,
    probe_media,
    snap_timeline_position,
    resize_clip_left,
    resize_clip_right,
    run_export,
    split_clip,
    unlink_video_audio,
    waveform_samples,
    waveform_segment,
    timeline_to_source_time,
)


BG = "#131516"
PANEL = "#1d2022"
PANEL2 = "#292d30"
TEXT = "#f2f4f5"
MUTED = "#9ca3a8"
LINE = "#3b4146"
BLUE = "#0785d1"
GREEN = "#269d58"
YELLOW = "#f1b941"
RED = "#db4a4a"


class SimpleEditorPanel(tk.Frame):
    PREVIEW_WIDTH = 720
    PREVIEW_HEIGHT = 405

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.project = EditorProject()
        self.media_library = []
        self.selected_media = None
        self.selected_clip = None
        self.mark_in = 0.0
        self.mark_out = 0.0
        self.cursor_time = 0.0
        self.keep_audio = tk.BooleanVar(value=True)
        self.export_mode = tk.StringVar(value="Todos los canales activos")
        self.track_name = tk.StringVar()
        self.ui_queue = queue.Queue()
        self.frame_token = 0
        self.preview_photo = None
        self.playing = False
        self.playback_started_at = None
        self.playback_start_time = 0.0
        self.playback_end_time = 0.0
        self.video_process = None
        self.audio_process = None
        self.drag_clip = None
        self.resize_side = None
        self.drag_offset = 0.0
        self.drag_track = None
        self.pending_library_media = None
        self.snap_enabled = tk.BooleanVar(value=True)
        self.snap_target = None
        self.snap_line_id = None
        self.tool_mode = "select"
        self.previous_tool_mode = "select"
        self.temporary_razor = False
        self.waveform_cache = {}
        self.playhead_timeline = 0.0
        self.track_heights = {}
        self.resize_track_index = None
        self.clip_title_var = tk.StringVar(value="Ningún clip seleccionado")
        self.clip_info_var = tk.StringVar(value="Selecciona un clip en la línea de tiempo")
        self.pitch_var = tk.StringVar(value="0 · Original")
        self.pitch_status_var = tk.StringVar(value="Tono original: no detectado")
        self.pitch_options = self._pitch_options()
        self.timeline_items = {}
        self.zoom = 24.0
        self._build_ui()
        self.after(60, self._poll_queue)

    def _button(self, parent, text, command, color=PANEL2, width=None, side="left", padx=3):
        button = tk.Button(
            parent, text=text, command=command, bg=color, fg=TEXT,
            activebackground="#3c4247", activeforeground=TEXT,
            font=("Segoe UI", 9), relief="flat", bd=0, padx=10, pady=6,
            width=width,
        )
        button.pack(side=side, padx=padx)
        return button

    @staticmethod
    def _pitch_options():
        options = []
        for value in range(-12, 13):
            if value == 0:
                label = "0 · Original"
            elif abs(value) == 12:
                label = f"{value:+d} · 1 octava {'grave' if value < 0 else 'aguda'}"
            else:
                label = f"{value:+d} · {abs(value)} semitono{'s' if abs(value) != 1 else ''} {'grave' if value < 0 else 'agudo'}"
            options.append(label)
        return options

    def _build_ui(self):
        toolbar = tk.Frame(self, bg=PANEL, padx=10, pady=7)
        toolbar.pack(fill="x")
        tk.Label(toolbar, text="Editor", bg=PANEL, fg=TEXT, font=("Segoe UI", 15, "bold")).pack(side="left", padx=(0, 12))
        self.select_tool_btn = self._button(toolbar, "↖ Seleccionar (V)", lambda: self.set_tool("select"), BLUE)
        self.razor_tool_btn = self._button(toolbar, "✂ Cuchilla (C)", lambda: self.set_tool("razor"), PANEL2)
        self._button(toolbar, "＋ Abrir vídeo o canción", self.open_media, BLUE)
        self._button(toolbar, "🧲 Ajuste", self.toggle_snap)
        self._button(toolbar, "＋ Canal vídeo", lambda: self.add_track("video"))
        self._button(toolbar, "＋ Canal audio", lambda: self.add_track("audio"))
        self._button(toolbar, "Exportar", self.export, GREEN, side="right")
        ttk.Combobox(
            toolbar, textvariable=self.export_mode,
            values=["Todos los canales activos", "Solo el canal seleccionado"],
            state="readonly", width=26,
        ).pack(side="right", padx=4)
        self.set_tool("select")
        self.bind_all("<KeyPress-v>", lambda _event: self.set_tool("select"))
        self.bind_all("<KeyPress-c>", lambda _event: self.set_tool("razor"))
        self.bind_all("<space>", self._space_play)
        self.winfo_toplevel().bind("<space>", self._space_play, add="+")

        workspace = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=5, bd=0)
        workspace.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        library_panel = tk.Frame(workspace, bg=PANEL, width=230)
        viewer_panel = tk.Frame(workspace, bg=PANEL)
        inspector_panel = tk.Frame(workspace, bg=PANEL, width=250)
        workspace.add(library_panel, minsize=190)
        workspace.add(viewer_panel, minsize=520)
        workspace.add(inspector_panel, minsize=220)

        tk.Label(library_panel, text="Tus archivos", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=9, pady=(9, 5))
        self.media_list = tk.Listbox(
            library_panel, bg="#161819", fg=TEXT, selectbackground=BLUE,
            selectforeground="#ffffff", relief="flat", bd=0,
            font=("Segoe UI", 9), activestyle="none",
        )
        self.media_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.media_list.bind("<<ListboxSelect>>", self._select_media)
        self.media_list.bind("<ButtonPress-1>", self._library_drag_start)
        self.media_list.bind("<B1-Motion>", self._library_drag_motion)
        self.media_list.bind("<ButtonRelease-1>", self._library_drag_release)

        tk.Label(inspector_panel, text="Clip seleccionado", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 3))
        tk.Label(inspector_panel, textvariable=self.clip_title_var, bg=PANEL, fg=TEXT, wraplength=220, justify="left", anchor="w").pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(inspector_panel, textvariable=self.clip_info_var, bg=PANEL, fg=MUTED, justify="left", anchor="w").pack(fill="x", padx=10, pady=3)
        tk.Label(inspector_panel, text="Cambio de tono", bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(12, 2))
        self.pitch_combo = ttk.Combobox(inspector_panel, textvariable=self.pitch_var, values=self.pitch_options, state="readonly")
        self.pitch_combo.pack(fill="x", padx=10, pady=(0, 3))
        self.pitch_combo.bind("<<ComboboxSelected>>", self._pitch_changed)
        tk.Label(inspector_panel, textvariable=self.pitch_status_var, bg=PANEL, fg=MUTED, wraplength=220, justify="left").pack(fill="x", padx=10, pady=(0, 8))
        self.inspector_audio_btn = self._button(inspector_panel, "Audio original ON/OFF", self.toggle_clip_audio, side="top")
        self.unlink_btn = self._button(inspector_panel, "Desvincular audio/vídeo", self.toggle_linked_media, side="top")
        self._button(inspector_panel, "Eliminar clip", self.delete_clip, "#9f2f2f", side="top")

        track_box = tk.LabelFrame(library_panel, text="Canal de destino", bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold"), bd=1)
        track_box.pack(fill="x", padx=8, pady=(0, 8))
        self.track_combo = ttk.Combobox(track_box, textvariable=self.track_name, state="readonly")
        self.track_combo.pack(fill="x", padx=6, pady=(6, 4))
        self.track_combo.bind("<<ComboboxSelected>>", self._track_selected)
        track_actions = tk.Frame(track_box, bg=PANEL)
        track_actions.pack(fill="x", padx=4, pady=(0, 5))
        self.track_active_btn = self._button(track_actions, "Activo", self.toggle_track, width=7)
        self.track_mute_btn = self._button(track_actions, "Audio ON", self.toggle_mute, width=8)
        self._button(track_actions, "Eliminar", self.delete_track, "#9f2f2f", side="right")

        self.preview = tk.Label(
            viewer_panel, bg="#07090a", fg=MUTED,
            text="Abre un vídeo o una canción", font=("Segoe UI", 13),
            width=70, height=18,
        )
        self.preview.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        self.time_scale = tk.Scale(self, from_=0, to=1, resolution=.01, command=self._scrub)
        self.time_label = tk.Label(self, text="", bg=BG, fg=BG)
        self.selection_canvas = tk.Canvas(self, height=1)
        self.selection_label = tk.Label(self, text="")
        self.keep_audio_check = tk.Checkbutton(viewer_panel, variable=self.keep_audio)

        timeline_panel = tk.Frame(self, bg=PANEL)
        timeline_panel.pack(fill="both", expand=True, padx=10, pady=(4, 5))
        timeline_header = tk.Frame(timeline_panel, bg=PANEL, padx=8, pady=5)
        timeline_header.pack(fill="x")
        tk.Label(timeline_header, text="Línea de tiempo", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(side="left")
        self.play_btn = self._button(timeline_header, "▶ Reproducir (Espacio)", self.play_from_cursor, BLUE, side="left")
        self._button(timeline_header, "✂ Cuchilla", self.cut_selected_clip, BLUE, side="right")
        self._button(timeline_header, "Eliminar clip", self.delete_clip, "#9f2f2f", side="right")
        self._button(timeline_header, "Audio del clip ON/OFF", self.toggle_clip_audio, side="right")

        canvas_box = tk.Frame(timeline_panel, bg=PANEL)
        canvas_box.pack(fill="both", expand=True, padx=8, pady=(0, 7))
        self.timeline = tk.Canvas(canvas_box, height=190, bg="#0d0f10", highlightthickness=0)
        xscroll = ttk.Scrollbar(canvas_box, orient="horizontal", command=self.timeline.xview)
        yscroll = ttk.Scrollbar(canvas_box, orient="vertical", command=self.timeline.yview)
        self.timeline.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.timeline.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        canvas_box.rowconfigure(0, weight=1)
        canvas_box.columnconfigure(0, weight=1)
        self.timeline.bind("<Button-1>", self._timeline_press)
        self.timeline.bind("<ButtonPress-1>", self._timeline_playhead_press, add="+")
        self.timeline.bind("<B1-Motion>", self._timeline_playhead_drag, add="+")
        self.timeline.bind("<Button-2>", self._temporary_razor_press)
        self.timeline.bind("<ButtonRelease-2>", self._temporary_razor_release)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)
        self._install_drop_target(self.timeline)
        self._install_drop_target(self.media_list)

        self.status = tk.Label(self, text="Abre un archivo para empezar.", bg=BG, fg=MUTED, anchor="w", padx=12, pady=5)
        self.status.pack(fill="x")
        self.add_track("video")
        self.add_track("audio")

    def add_track(self, kind):
        count = sum(track.kind == kind for track in self.project.tracks) + 1
        name = f"Vídeo {count}" if kind == "video" else f"Audio {count}"
        track = Track(name, kind)
        self.project.tracks.append(track)
        self.project.selected_track_index = len(self.project.tracks) - 1
        self._refresh_tracks()
        self.redraw_timeline()
        return track

    def _install_drop_target(self, widget):
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", lambda event, target=widget: self._drop_event(event, target))
        except Exception:
            pass

    def _drop_event(self, event, target):
        try:
            paths = self.tk.splitlist(event.data)
            self._ingest_dropped_paths(paths, target, event)
        except Exception as exc:
            self.status.configure(text=f"No se pudo cargar el archivo: {exc}", fg=RED)
        return "break"

    def _ingest_dropped_paths(self, paths, target, event=None):
        accepted = []
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                media = probe_media(path)
                self.media_library.append(media)
                self.media_list.insert("end", f"{'▶' if media.has_video else '♫'}  {pathlib.Path(path).name}   ·   {format_editor_time(media.duration)}")
                accepted.append(media)
            except Exception as exc:
                self.status.configure(text=f"No se pudo abrir {pathlib.Path(path).name}: {exc}", fg=RED)
        if not accepted:
            return
        if target is self.timeline:
            event = event or type("DropEvent", (), {"x": 115, "y": 30})()
            self.pending_library_media = accepted[-1]
            self._drop_library_media(event)
        else:
            self.media_list.selection_clear(0, "end")
            self.media_list.selection_set(len(self.media_library) - 1)
            self._select_media()

    def _refresh_tracks(self):
        names = [track.name for track in self.project.tracks]
        self.track_combo.configure(values=names)
        if names:
            index = max(0, min(self.project.selected_track_index, len(names) - 1))
            self.project.selected_track_index = index
            self.track_name.set(names[index])
            track = self.project.tracks[index]
            self.track_active_btn.configure(text="Activo" if track.enabled else "Desactivado")
            self.track_mute_btn.configure(text="Audio OFF" if track.muted else "Audio ON")
        else:
            self.track_name.set("")

    def _track_selected(self, _event=None):
        if self.track_name.get() in [track.name for track in self.project.tracks]:
            self.project.selected_track_index = [track.name for track in self.project.tracks].index(self.track_name.get())
            self._refresh_tracks()
            self.redraw_timeline()

    def toggle_snap(self):
        self.snap_enabled.set(not self.snap_enabled.get())
        state = "activado" if self.snap_enabled.get() else "desactivado"
        self.status.configure(text=f"Ajuste magnético {state}. Mantén Alt para desactivarlo temporalmente.", fg=YELLOW)

    def set_tool(self, mode):
        if mode not in {"select", "razor"}:
            return
        self.tool_mode = mode
        if hasattr(self, "select_tool_btn"):
            self.select_tool_btn.configure(bg=BLUE if mode == "select" else PANEL2)
            self.razor_tool_btn.configure(bg=BLUE if mode == "razor" else PANEL2)
            if hasattr(self, "status"):
                self.status.configure(text="Herramienta Cuchilla activa: haz clic sobre un clip." if mode == "razor" else "Herramienta Seleccionar activa.", fg=YELLOW)

    def _temporary_razor_press(self, _event):
        if not self.temporary_razor:
            self.previous_tool_mode = self.tool_mode
            self.temporary_razor = True
            self.set_tool("razor")

    def _temporary_razor_release(self, _event):
        if self.temporary_razor:
            self.temporary_razor = False
            self.set_tool(self.previous_tool_mode)

    def toggle_linked_media(self):
        clip = self.selected_clip
        if not clip:
            return
        track = next((item for item in self.project.tracks if clip in item.clips), None)
        if not track or not clip.media.has_video or not clip.media.has_audio:
            self.status.configure(text="Selecciona un vídeo con audio para desvincularlo.", fg=YELLOW)
            return
        if clip.link_group:
            linked = [other for item in self.project.tracks for other in item.clips if other.link_group == clip.link_group]
            for other in linked:
                other.link_group = None
            self.status.configure(text="Audio y vídeo desvinculados.", fg=GREEN)
            self.unlink_btn.configure(text="Vincular audio/vídeo")
            self.redraw_timeline()
            return
        audio_track = next((item for item in self.project.tracks if item.kind == "audio"), None)
        if not audio_track:
            audio_track = self.add_track("audio")
        audio_media = audio_media_from_video(clip.media)
        audio_clip = Clip(audio_media, clip.source_in, clip.source_out, clip.timeline_start, name=f"Audio · {clip.name}", link_group="link-" + str(id(clip)))
        clip.link_group = audio_clip.link_group
        audio_track.clips.append(audio_clip)
        self.status.configure(text="Audio separado y vinculado al vídeo. Puedes desvincularlo para editarlo aparte.", fg=GREEN)
        self.redraw_timeline()

    def _pitch_changed(self, _event=None):
        if not self.selected_clip:
            return
        label = self.pitch_var.get().split("·", 1)[0].strip()
        try:
            value = int(label)
            self.selected_clip.pitch_semitones = value
        except (ValueError, TypeError):
            return
        self._update_inspector()
        self.status.configure(text=f"Tono del clip: {value:+d} semitonos.", fg=GREEN)

    def _update_inspector(self):
        clip = self.selected_clip
        if not clip:
            self.clip_title_var.set("Ningún clip seleccionado")
            self.clip_info_var.set("Selecciona un clip en la línea de tiempo")
            self.pitch_var.set("0 · Original")
            self.pitch_status_var.set("Tono original: no detectado")
            return
        self.clip_title_var.set(clip.name)
        self.clip_info_var.set(
            f"Duración: {format_editor_time(clip.duration)}\n"
            f"Entrada: {format_editor_time(clip.source_in)}\n"
            f"Salida: {format_editor_time(clip.source_out)}"
        )
        self.pitch_var.set(self.pitch_options[clip.pitch_semitones + 12])
        self.pitch_status_var.set("Tono original: no detectado · ajuste relativo al original")
        self.inspector_audio_btn.configure(text="Audio original ON" if clip.use_source_audio else "Audio original OFF")

    def toggle_track(self):
        track = self.project.selected_track()
        if track:
            track.enabled = not track.enabled
            self._refresh_tracks()
            self.redraw_timeline()

    def toggle_mute(self):
        track = self.project.selected_track()
        if track:
            track.muted = not track.muted
            self._refresh_tracks()
            self.redraw_timeline()

    def delete_track(self):
        index = self.project.selected_track_index
        if 0 <= index < len(self.project.tracks):
            del self.project.tracks[index]
            self.project.selected_track_index = max(0, min(index, len(self.project.tracks) - 1))
            self.selected_clip = None
            self._refresh_tracks()
            self.redraw_timeline()

    def open_media(self):
        paths = filedialog.askopenfilenames(
            title="Abrir vídeo o canción",
            filetypes=[("Vídeos y canciones", "*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("Todos", "*.*")],
        )
        for path in paths:
            try:
                media = probe_media(path)
                self.media_library.append(media)
                icon = "▶" if media.has_video else "♫"
                self.media_list.insert("end", f"{icon}  {pathlib.Path(path).name}   ·   {format_editor_time(media.duration)}")
            except Exception as exc:
                messagebox.showerror("No se pudo abrir", f"{path}\n\n{exc}")
        if paths and self.media_library:
            self.media_list.selection_clear(0, "end")
            self.media_list.selection_set(len(self.media_library) - 1)
            self._select_media()

    def _library_drag_start(self, event):
        index = self.media_list.nearest(event.y)
        if 0 <= index < len(self.media_library):
            self.pending_library_media = self.media_library[index]
            self.status.configure(text="Arrastra el archivo a un canal de vídeo o audio.", fg=TEXT)
            self._library_drag_origin = (event.x_root, event.y_root)
            self._library_dragging = False

    def _library_drag_motion(self, event):
        if not self.pending_library_media:
            return
        ox, oy = self._library_drag_origin
        if abs(event.x_root - ox) + abs(event.y_root - oy) > 8:
            self._library_dragging = True

    def _library_drag_release(self, event):
        media = self.pending_library_media
        if not media or not self._library_dragging:
            self.pending_library_media = None
            return
        x = event.x_root - self.timeline.winfo_rootx()
        y = event.y_root - self.timeline.winfo_rooty()
        if 0 <= x < self.timeline.winfo_width() and 0 <= y < self.timeline.winfo_height():
            self._drop_library_media(type("DropEvent", (), {"x": x, "y": y})())
        else:
            self.pending_library_media = None
            self.status.configure(text="Suelta el archivo dentro de un canal compatible.", fg=YELLOW)

    def _drop_library_media(self, event):
        media = self.pending_library_media
        self.pending_library_media = None
        if not media:
            return False
        y = self.timeline.canvasy(event.y)
        track = None
        current = 24
        for item in self.project.tracks:
            height = self.track_heights.get(item.name, 64 if item.kind == "audio" else 48)
            if current <= y <= current + height:
                track = item
                break
            current += height
        if not track or (track.kind == "video" and not media.has_video) or (track.kind == "audio" and not media.has_audio):
            self.status.configure(text="Suelta el archivo en un canal compatible.", fg=RED)
            return True
        start = max(0.0, (self.timeline.canvasx(event.x) - 115) / self.zoom)
        clip = Clip(media, timeline_start=start, name=pathlib.Path(media.path).stem)
        track.clips.append(clip)
        self.selected_clip = clip
        self.selected_media = media
        self.project.selected_track_index = self.project.tracks.index(track)
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text=f"Añadido: {pathlib.Path(media.path).name}", fg=GREEN)
        return True

    def _select_media(self, _event=None):
        selection = self.media_list.curselection()
        if not selection:
            return
        self.stop_playback()
        self.selected_media = self.media_library[selection[0]]
        self.cursor_time = 0.0
        self.mark_in = 0.0
        self.mark_out = self.selected_media.duration
        self.time_scale.configure(to=max(0.01, self.selected_media.duration))
        self.time_scale.set(0)
        self.keep_audio.set(self.selected_media.has_audio)
        self.keep_audio_check.configure(state="normal" if self.selected_media.has_audio else "disabled")
        self.status.configure(text="Archivo cargado. Arrástralo a un canal inferior para editarlo.", fg=TEXT)

    def _scrub(self, value):
        if not self.selected_media or self.playing:
            return
        self.cursor_time = clamp_media_time(float(value), self.selected_media.duration)
        self._update_source_ui()
        if self.selected_media.has_video:
            self.request_frame(self.cursor_time)

    def _seek_timeline(self, event):
        self.playhead_timeline = max(0.0, (self.timeline.canvasx(event.x) - 115) / self.zoom)
        clip = next((clip for track in reversed(self.project.tracks) if track.enabled for clip in track.clips
                     if timeline_to_source_time(clip, self.playhead_timeline) is not None), None)
        if clip:
            self.selected_clip = clip
            self.selected_media = clip.media
            self.cursor_time = timeline_to_source_time(clip, self.playhead_timeline)
            self.mark_in, self.mark_out = clip.source_in, clip.source_out
            self.time_label.configure(text=format_editor_time(self.playhead_timeline))
            self.keep_audio.set(clip.use_source_audio)
            self._update_source_ui()
            self._update_inspector()
            if clip.media.has_video:
                self.request_frame(self.cursor_time)
        self.redraw_timeline()

    def _scrub(self, value):
        if self.playing:
            return
        self.playhead_timeline = max(0.0, float(value))
        self._seek_timeline(type("SeekEvent", (), {"x": 115 + self.playhead_timeline * self.zoom})())

    def _timeline_playhead_press(self, event):
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        hit = self.timeline.find_overlapping(x, y, x, y)
        if not any(item in self.timeline_items for item in hit):
            self._seek_timeline(event)

    def _timeline_playhead_drag(self, event):
        if not self.drag_clip and self.resize_track_index is None:
            self._seek_timeline(event)

    def request_frame(self, seconds):
        media = self.selected_media
        if not media or not media.has_video:
            return
        self.frame_token += 1
        token = self.frame_token

        def worker():
            ffmpeg = discover_ffmpeg_tools()[0]
            command = [
                ffmpeg, "-v", "error", "-ss", f"{seconds:.3f}", "-i", media.path,
                "-frames:v", "1", "-vf",
                f"scale={self.PREVIEW_WIDTH}:{self.PREVIEW_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={self.PREVIEW_WIDTH}:{self.PREVIEW_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
                "-f", "image2pipe", "-vcodec", "png", "pipe:1",
            ]
            result = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0 and result.stdout:
                self.ui_queue.put(("frame", token, result.stdout))

        threading.Thread(target=worker, daemon=True).start()

    def play_from_cursor(self):
        if self.playing:
            self.stop_playback()
            return
        clip = next((clip for track in reversed(self.project.tracks) if track.enabled
                     for clip in track.clips
                     if timeline_to_source_time(clip, self.playhead_timeline) is not None), None)
        if not clip:
            self.status.configure(text="Coloca el cabezal sobre un clip de la línea de edición.", fg=YELLOW)
            return
        self.selected_clip = clip
        self.selected_media = clip.media
        self.cursor_time = timeline_to_source_time(clip, self.playhead_timeline)
        self.mark_in, self.mark_out = clip.source_in, clip.source_out
        self.keep_audio.set(clip.use_source_audio)
        self._update_inspector()
        self.toggle_play(from_mark=False)

    def toggle_play(self, from_mark=False):
        if self.playing:
            self.stop_playback()
            return
        media = self.selected_media
        if not media:
            return
        start, end = playback_range(self.cursor_time, self.mark_in, self.mark_out, media.duration, from_mark=from_mark)
        self.cursor_time = start
        self.time_scale.set(start)
        self._update_source_ui()
        self.playback_started_at = time.monotonic()
        self.playback_start_time = start
        self.playback_end_time = end
        if not media.has_video:
            self._play_audio_only(media, start, end)
            return
        self.playing = True
        self.play_btn.configure(text="Ⅱ")
        self.frame_token += 1
        token = self.frame_token
        duration = max(0.1, end - start)
        play_audio = bool(media.has_audio and self.keep_audio.get())
        pitch_filter = build_pitch_filter(
            getattr(self.selected_clip, "pitch_semitones", 0)
            if self.selected_clip and self.selected_clip.media is media else 0
        )

        def worker():
            try:
                ffmpeg, _, ffplay = discover_ffmpeg_tools()
                command = [
                    ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-i", media.path,
                    "-t", f"{duration:.3f}", "-an", "-vf",
                    f"scale={self.PREVIEW_WIDTH}:{self.PREVIEW_HEIGHT}:force_original_aspect_ratio=decrease,"
                    f"pad={self.PREVIEW_WIDTH}:{self.PREVIEW_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
                    "-r", "15", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ]
                self.video_process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if play_audio:
                    audio_command = [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]
                    if pitch_filter:
                        audio_command += ["-af", pitch_filter]
                    audio_command += [media.path]
                    self.audio_process = subprocess.Popen(
                        audio_command,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                frame_size = self.PREVIEW_WIDTH * self.PREVIEW_HEIGHT * 3
                frame_index = 0
                began = time.monotonic()
                while self.video_process and self.video_process.poll() is None and token == self.frame_token:
                    raw = self.video_process.stdout.read(frame_size) if self.video_process.stdout else b""
                    if len(raw) != frame_size:
                        break
                    target = frame_index / 15
                    wait = target - (time.monotonic() - began)
                    if wait > 0:
                        time.sleep(wait)
                    self.ui_queue.put(("raw_frame", token, raw, start + target))
                    frame_index += 1
                if self.video_process and self.video_process.returncode not in (0, None):
                    error = self.video_process.stderr.read().decode("utf-8", "replace").strip() if self.video_process.stderr else ""
                    self.ui_queue.put(("play_error", error or "FFmpeg no pudo generar la previsualización."))
            except Exception as exc:
                self.ui_queue.put(("play_error", str(exc)))
            finally:
                self.ui_queue.put(("play_done", token))

        threading.Thread(target=worker, daemon=True).start()

    def _space_play(self, event=None):
        self.toggle_play(from_mark=True)
        return "break"

    def _play_audio_only(self, media, start=None, end=None):
        try:
            _, _, ffplay = discover_ffmpeg_tools()
        except Exception as exc:
            self.playing = False
            self.play_btn.configure(text="▶")
            self.status.configure(text=f"Previsualización: {exc}", fg=RED)
            return
        self.playing = True
        self.play_btn.configure(text="Ⅱ")
        self.frame_token += 1
        token = self.frame_token
        if start is None or end is None:
            start, end = playback_range(self.cursor_time, self.mark_in, self.mark_out, media.duration)
        duration = max(0.1, end - start)
        command = [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]
        pitch_filter = build_pitch_filter(getattr(self.selected_clip, "pitch_semitones", 0) if self.selected_clip and self.selected_clip.media is media else 0)
        if pitch_filter:
            command += ["-af", pitch_filter]
        command += [media.path]
        self.audio_process = subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        audio_process = self.audio_process

        def wait_audio():
            audio_process.wait()
            if audio_process.returncode not in (0, None) and self.playing:
                self.ui_queue.put(("audio_error", "No se pudo reproducir el audio con FFplay."))
            self.ui_queue.put(("play_done", token))
        threading.Thread(target=wait_audio, daemon=True).start()

    def stop_playback(self):
        self.frame_token += 1
        for process in (self.video_process, self.audio_process):
            if process and process.poll() is None:
                process.terminate()
        self.video_process = None
        self.audio_process = None
        self.playing = False
        self.playback_started_at = None
        self.play_btn.configure(text="▶")

    def set_mark_in(self):
        if not self.selected_media:
            return
        self.mark_in, self.mark_out = normalize_selection(self.cursor_time, self.mark_out, self.selected_media.duration)
        self._update_source_ui()

    def set_mark_out(self):
        if not self.selected_media:
            return
        self.mark_in, self.mark_out = normalize_selection(self.mark_in, self.cursor_time, self.selected_media.duration)
        self._update_source_ui()

    def _update_source_ui(self):
        media = self.selected_media
        if not media:
            return
        return

    def add_selection(self):
        media = self.selected_media
        if not media:
            messagebox.showinfo("Selecciona un archivo", "Primero abre y selecciona un vídeo o una canción.")
            return
        kind = "video" if media.has_video else "audio"
        track = self.project.selected_track()
        if not track or track.kind != kind:
            matching = next((item for item in self.project.tracks if item.kind == kind), None)
            track = matching or self.add_track(kind)
            self.project.selected_track_index = self.project.tracks.index(track)
        start = max((clip.timeline_end for clip in track.clips), default=0.0)
        clip = Clip(
            media, self.mark_in, self.mark_out, start,
            use_source_audio=bool(self.keep_audio.get()),
            name=f"{pathlib.Path(media.path).stem} · {format_editor_time(self.mark_in)}",
        )
        track.clips.append(clip)
        self.selected_clip = clip
        self.project.selected_track_index = self.project.tracks.index(track)
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text="Selección añadida. Puedes arrastrarla por la línea de tiempo.", fg=GREEN)

    def _update_source_ui(self):
        # Los controles de selección fuente se conservan solo como estado interno.
        return

    def cut_selected_clip(self):
        clip = self.selected_clip
        if not clip or not self.selected_media or clip.media is not self.selected_media:
            self.status.configure(text="Selecciona un clip de la línea de tiempo y coloca el cursor donde quieras cortar.", fg=YELLOW)
            return
        timeline_cut = clip.timeline_start + (self.cursor_time - clip.source_in)
        pieces = split_clip(clip, timeline_cut)
        if pieces is None:
            self.status.configure(text="La cuchilla debe estar dentro del clip, no en sus extremos.", fg=YELLOW)
            return
        for track in self.project.tracks:
            if clip in track.clips:
                index = track.clips.index(clip)
                track.clips[index:index + 1] = list(pieces)
                self.selected_clip = pieces[0]
                self.project.selected_track_index = self.project.tracks.index(track)
                self._refresh_tracks()
                self._update_inspector()
                self.redraw_timeline()
                self.status.configure(text="Clip dividido en dos tramos editables.", fg=GREEN)
                return

    def _replace_clip(self, track, old_clip, new_clip):
        index = track.clips.index(old_clip)
        track.clips[index] = new_clip
        self.selected_clip = new_clip
        self.drag_clip = new_clip

    def redraw_timeline(self):
        self.timeline.delete("all")
        self.timeline_items.clear()
        label_width = 115
        duration = max(30.0, self.project.duration() + 5)
        total_width = label_width + duration * self.zoom
        total_height = 24 + sum(self.track_heights.get(item.name, 64 if item.kind == "audio" else 48) for item in self.project.tracks)
        for second in range(0, int(duration) + 1, 5):
            x = label_width + second * self.zoom
            self.timeline.create_line(x, 0, x, max(190, total_height + 8), fill="#262b2f")
            self.timeline.create_text(x + 2, 8, text=format_editor_time(second), fill=MUTED, anchor="nw", font=("Segoe UI", 7))
        for row, track in enumerate(self.project.tracks):
            row_height = self.track_heights.get(track.name, 64 if track.kind == "audio" else 48)
            y1 = 24 + row * row_height
            y2 = y1 + row_height - 4
            self.timeline.create_rectangle(0, y1, total_width, y2, fill="#151719", outline=LINE)
            label = track.name + ("  OFF" if not track.enabled else "") + ("  M" if track.muted else "")
            self.timeline.create_text(7, (y1 + y2) / 2, text=label, fill=TEXT if track.enabled else MUTED, anchor="w", font=("Segoe UI", 8))
            for clip in track.clips:
                x1 = label_width + clip.timeline_start * self.zoom
                x2 = x1 + max(14, clip.duration * self.zoom)
                color = "#126da8" if track.kind == "video" else "#28784a"
                outline = YELLOW if clip is self.selected_clip else "#78a9c8"
                item = self.timeline.create_rectangle(x1, y1 + 4, x2, y2 - 4, fill=color, outline=outline, width=2 if clip is self.selected_clip else 1)
                self.timeline.create_text(x1 + 5, (y1 + y2) / 2, text=clip.name, fill="#ffffff", anchor="w", font=("Segoe UI", 8))
                self.timeline_items[item] = (track, clip)
                if clip.media.has_audio:
                    samples = self.waveform_cache.get(clip.media.path)
                    if samples is None:
                        samples = waveform_samples(clip.media.path, 640)
                        self.waveform_cache[clip.media.path] = samples
                    samples = waveform_segment(samples, clip.source_in, clip.source_out, clip.media.duration, 160)
                    mid = (y1 + y2) / 2
                    height = max(4, (y2 - y1 - 10) / 2) if track.kind == "audio" else max(3, (y2 - y1 - 14) / 3)
                    self.timeline.create_line(x1, mid, x2, mid, fill="#78a984", width=1)
                    points = []
                    for index, peak in enumerate(samples):
                        px = x1 + (index / max(1, len(samples) - 1)) * max(4, x2 - x1)
                        points.extend((px, mid - peak * height, px, mid + peak * height))
                    for index in range(0, len(points) - 2, 4):
                        self.timeline.create_line(points[index], points[index + 1], points[index + 2], points[index + 3], fill="#c3f3d0", width=2)
                if clip is self.selected_clip:
                    left_handle = self.timeline.create_rectangle(x1, y1 + 2, x1 + 6, y2 - 2, fill=YELLOW, outline="")
                    right_handle = self.timeline.create_rectangle(x2 - 6, y1 + 2, x2, y2 - 2, fill=YELLOW, outline="")
                    self.timeline_items[left_handle] = (track, clip)
                    self.timeline_items[right_handle] = (track, clip)
        playhead_x = label_width + self.playhead_timeline * self.zoom
        self.timeline.create_line(playhead_x, 0, playhead_x, max(190, len(self.project.tracks) * 64 + 24), fill="#f2f4f5", width=2)
        if self.snap_target is not None:
            guide_x = label_width + self.snap_target * self.zoom
            self.timeline.create_line(guide_x, 0, guide_x, max(190, len(self.project.tracks) * row_height + 24), fill="#ffcf4a", width=2, dash=(4, 3))
        self.timeline.configure(scrollregion=(0, 0, total_width, max(190, total_height + 8)))

    def _track_boundary_at(self, y):
        current = 24
        for index, track in enumerate(self.project.tracks[:-1]):
            current += self.track_heights.get(track.name, 64 if track.kind == "audio" else 48)
            if abs(y - current) <= 4:
                return index
        return None

    def _snap_start(self, track, clip, raw_start, alt=False):
        if not self.snap_enabled.get() or alt:
            self.snap_target = None
            return max(0.0, raw_start)
        candidates = []
        for other_track in self.project.tracks:
            for other in other_track.clips:
                if other is clip:
                    continue
                candidates.extend((other.timeline_start, other.timeline_end))
        candidates.append(getattr(self, "timeline_cursor", 0.0))
        threshold = max(0.08, 8.0 / self.zoom)
        snapped, target = snap_timeline_position(raw_start, clip.duration, candidates, threshold)
        self.snap_target = target
        return snapped

    def _timeline_press(self, event):
        if self.pending_library_media is not None and self._drop_library_media(event):
            return
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        boundary = self._track_boundary_at(y)
        if boundary is not None:
            self.resize_track_index = boundary
            self.drag_clip = None
            return
        items = self.timeline.find_overlapping(x, y, x, y)
        item = next((item for item in reversed(items) if item in self.timeline_items), None)
        if item is None:
            self.playhead_timeline = max(0.0, (x - 115) / self.zoom)
            self.status.configure(text=f"Cabezal: {format_editor_time(self.playhead_timeline)} · haz clic en un clip o pulsa reproducir.", fg=TEXT)
            if self.selected_clip:
                self.cursor_time = clamp_media_time(self.selected_clip.source_in + (self.playhead_timeline - self.selected_clip.timeline_start), self.selected_clip.media.duration)
                self.time_scale.set(self.cursor_time)
                self._update_source_ui()
                if self.selected_clip.media.has_video:
                    self.request_frame(self.cursor_time)
            return
        track, clip = self.timeline_items[item]
        if self.tool_mode == "razor":
            self.selected_clip = clip
            self.selected_media = clip.media
            self.cursor_time = clamp_media_time(clip.source_in + ((x - 115) / self.zoom - clip.timeline_start), clip.media.duration)
            self.cut_selected_clip()
            if not self.temporary_razor:
                self.set_tool("select")
            return
        self.project.selected_track_index = self.project.tracks.index(track)
        self.selected_clip = clip
        self.selected_media = clip.media
        self.mark_in = clip.source_in
        self.mark_out = clip.source_out
        self.playhead_timeline = max(0.0, (x - 115) / self.zoom)
        self.cursor_time = clamp_media_time(clip.source_in + (self.playhead_timeline - clip.timeline_start), clip.media.duration)
        self.time_scale.configure(to=max(0.01, clip.media.duration))
        self.time_scale.set(self.cursor_time)
        if clip.media.has_video:
            self.request_frame(self.cursor_time)
        self.resize_side = None
        x1 = 115 + clip.timeline_start * self.zoom
        x2 = x1 + max(14, clip.duration * self.zoom)
        if abs(x - x1) <= 8:
            self.resize_side = "left"
        elif abs(x - x2) <= 8:
            self.resize_side = "right"
        self.drag_clip = clip
        self.drag_track = track
        self.drag_offset = x - x1
        self.playhead_timeline = max(0.0, (x - 115) / self.zoom)
        self.status.configure(text=f"Cabezal: {format_editor_time(self.playhead_timeline)}", fg=TEXT)
        self.timeline_cursor = clip.timeline_start
        self._update_source_ui()
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()

    def _timeline_drag(self, event):
        if self.resize_track_index is not None:
            index = self.resize_track_index
            track = self.project.tracks[index]
            next_track = self.project.tracks[index + 1]
            current_y = self.timeline.canvasy(event.y)
            base = 24 + sum(self.track_heights.get(item.name, 64 if item.kind == "audio" else 48) for item in self.project.tracks[:index])
            self.track_heights[track.name] = max(34, min(180, current_y - base))
            self.redraw_timeline()
            return
        if not self.drag_clip:
            return
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        track = next((item for item in self.project.tracks if self.drag_clip in item.clips), None)
        if not track:
            return
        if self.resize_side == "left":
            boundary = (x - 115) / self.zoom
            neighbors = [item.timeline_end for item in track.clips if item is not self.drag_clip and item.timeline_end <= self.drag_clip.timeline_start + 0.001]
            if neighbors:
                boundary = max(boundary, max(neighbors))
            updated = resize_clip_left(self.drag_clip, boundary)
            self._replace_clip(track, self.drag_clip, updated)
        elif self.resize_side == "right":
            boundary = (x - 115) / self.zoom
            neighbors = [item.timeline_start for item in track.clips if item is not self.drag_clip and item.timeline_start >= self.drag_clip.timeline_end - 0.001]
            if neighbors:
                boundary = min(boundary, min(neighbors))
            updated = resize_clip_right(self.drag_clip, boundary)
            self._replace_clip(track, self.drag_clip, updated)
        else:
            raw_start = max(0.0, (x - self.drag_offset - 115) / self.zoom)
            alt_override = bool(event.state & 0x0008)
            new_start = self._snap_start(track, self.drag_clip, raw_start, alt_override)
            target_row = 0
            running_y = 24
            for index, candidate in enumerate(self.project.tracks):
                height = self.track_heights.get(candidate.name, 64 if candidate.kind == "audio" else 48)
                if running_y <= y < running_y + height:
                    target_row = index
                    break
                running_y += height
            target_track = self.project.tracks[target_row] if self.project.tracks else track
            if target_track.kind == track.kind and target_track is not track:
                track.clips.remove(self.drag_clip)
                target_track.clips.append(self.drag_clip)
                self.drag_track = target_track
                self.project.selected_track_index = self.project.tracks.index(target_track)
                track = target_track
            if not alt_override:
                neighbor_ranges = [(other.timeline_start, other.timeline_end) for other in track.clips if other is not self.drag_clip]
                constrained = avoid_clip_overlap(new_start, self.drag_clip.duration, neighbor_ranges)
                if constrained != new_start:
                    self.snap_target = constrained if constrained in {edge for pair in neighbor_ranges for edge in pair} else self.snap_target
                new_start = constrained
            self.drag_clip.timeline_start = new_start
            self.timeline_cursor = new_start
        self._update_inspector()
        self.redraw_timeline()

    def _timeline_release(self, event):
        if self.pending_library_media is not None and self._drop_library_media(event):
            return
        if self.resize_track_index is not None:
            self.resize_track_index = None
            return
        if self.drag_clip:
            self.drag_clip.timeline_start = round(self.drag_clip.timeline_start, 2)
            self.drag_clip.source_in = round(self.drag_clip.source_in, 3)
            self.drag_clip.source_out = round(self.drag_clip.source_out, 3)
            self.drag_clip = None
            self.drag_track = None
            self.snap_target = None
            self.resize_side = None
            self.redraw_timeline()

    def toggle_clip_audio(self):
        if self.selected_clip and self.selected_clip.media.has_audio:
            self.selected_clip.use_source_audio = not self.selected_clip.use_source_audio
            state = "activado" if self.selected_clip.use_source_audio else "quitado"
            self._update_inspector()
            self.status.configure(text=f"Audio original del clip: {state}.", fg=YELLOW)

    def delete_clip(self):
        for track in self.project.tracks:
            if self.selected_clip in track.clips:
                track.clips.remove(self.selected_clip)
                break
        self.selected_clip = None
        self._update_inspector()
        self.redraw_timeline()

    def _mode_key(self):
        return "selected" if self.export_mode.get().startswith("Solo") else "active"

    def export(self):
        tracks = self.project.export_tracks(self._mode_key())
        has_video = any(clip.media.has_video for track in tracks for clip in track.clips)
        default = ".mp4" if has_video else ".mp3"
        path = filedialog.asksaveasfilename(
            title="Exportar montaje", defaultextension=default, initialfile="montaje" + default,
            filetypes=[("MP4", "*.mp4"), ("MOV", "*.mov"), ("MKV", "*.mkv"), ("WEBM", "*.webm"), ("MP3", "*.mp3"), ("WAV", "*.wav")],
        )
        if not path:
            return
        try:
            plan = build_export_plan(self.project, self._mode_key(), self.project.selected_track_index, path)
        except Exception as exc:
            messagebox.showerror("No se puede exportar", str(exc))
            return
        self.status.configure(text="Exportando…", fg=YELLOW)

        def worker():
            try:
                output = run_export(plan, lambda line: self.ui_queue.put(("progress", line)))
                self.ui_queue.put(("export_done", output))
            except Exception as exc:
                self.ui_queue.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "frame" and event[1] == self.frame_token:
                image = Image.open(BytesIO(event[2])).convert("RGB")
                self.preview_photo = ImageTk.PhotoImage(image)
                self.preview.configure(image=self.preview_photo, text="")
            elif kind == "raw_frame" and event[1] == self.frame_token:
                image = Image.frombytes("RGB", (self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT), event[2])
                self.preview_photo = ImageTk.PhotoImage(image)
                self.preview.configure(image=self.preview_photo, text="")
                self.cursor_time = event[3]
                clip = self.selected_clip
                if clip and clip.media is self.selected_media:
                    self.playhead_timeline = clip.timeline_start + self.cursor_time - clip.source_in
                    self.time_label.configure(text=format_editor_time(self.playhead_timeline))
                    self.redraw_timeline()
            elif kind == "play_done" and event[1] == self.frame_token:
                self.playing = False
                self.playback_started_at = None
                self.play_btn.configure(text="▶")
            elif kind == "play_error":
                self.status.configure(text=f"Previsualización: {event[1]}", fg=RED)
            elif kind == "audio_error":
                self.status.configure(text=event[1], fg=RED)
            elif kind == "progress":
                self.status.configure(text="Exportando montaje…", fg=YELLOW)
            elif kind == "export_done":
                self.status.configure(text=f"Exportado: {event[1]}", fg=GREEN)
                messagebox.showinfo("Exportación terminada", f"Archivo creado:\n\n{event[1]}")
            elif kind == "error":
                self.status.configure(text="Error de exportación.", fg=RED)
                messagebox.showerror("Error", event[1])
        if self.playing and self.playback_started_at is not None:
            elapsed = time.monotonic() - self.playback_started_at
            self.cursor_time = min(self.playback_end_time, self.playback_start_time + elapsed)
            clip = self.selected_clip
            if clip and clip.media is self.selected_media:
                self.playhead_timeline = clip.timeline_start + self.cursor_time - clip.source_in
                self.time_label.configure(text=format_editor_time(self.playhead_timeline))
                self.redraw_timeline()
        self.after(60, self._poll_queue)

