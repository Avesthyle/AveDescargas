"""Editor visual sencillo: visor, marcas de entrada/salida y línea de tiempo."""

from __future__ import annotations

import copy
from io import BytesIO
import os
import pathlib
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import weakref

from PIL import Image, ImageTk

from media_editor import (
    Clip,
    EditorProject,
    MIN_CLIP_DURATION,
    Track,
    build_export_plan,
    clamp_media_time,
    discover_ffmpeg_tools,
    format_editor_time,
    load_project,
    parse_ffmpeg_time,
    playback_range,
    build_pitch_filter,
    audio_media_from_video,
    avoid_clip_overlap,
    probe_media,
    save_project,
    snap_timeline_position,
    resize_clip_left,
    resize_clip_right,
    run_export,
    split_clip,
    waveform_samples,
    waveform_segment,
    timeline_to_source_time,
    DEFAULT_TRACK_HEIGHTS,
    TRACK_SIZE_PRESETS,
    clamp_fades,
    clamp_track_height,
    close_gap,
    collect_snap_points,
    compact_clips,
    find_gap,
    ripple_delete,
    snap_point,
    snap_threshold,
    track_boundary_at,
    track_index_at,
    track_layout,
    unique_track_name,
)

LABEL_WIDTH = 115
RULER_HEIGHT = 24
UNDO_LIMIT = 60


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
        self.magnetic_main = tk.BooleanVar(value=False)
        self.undo_stack = []
        self.redo_stack = []
        self._pending_snapshot = None
        self.hover_boundary = None
        self.snap_label = ""
        self.waveform_pending = set()
        self.timeline_cursor = 0.0
        self.clipboard_clip = None
        self._frame_after_id = None
        self._playback_from_timeline = False
        self._playback_track = None
        self.exporting = False
        self.export_duration = 0.0
        self.timecode_var = tk.StringVar(value="00:00.00 / 00:00.00")
        self.export_resolution = tk.StringVar(value="Auto")
        self.volume_var = tk.IntVar(value=100)
        self.fade_in_var = tk.DoubleVar(value=0.0)
        self.fade_out_var = tk.DoubleVar(value=0.0)
        self._build_ui()
        self._poll_after_id = self.after(60, self._poll_queue)

    def destroy(self):
        for timer in ("_poll_after_id", "_frame_after_id"):
            after_id = getattr(self, timer, None)
            if after_id:
                try:
                    self.after_cancel(after_id)
                except (AttributeError, tk.TclError, ValueError):
                    pass
                setattr(self, timer, None)
        self.stop_playback()
        self.preview_photo = None
        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        super().destroy()

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
        self.export_btn = self._button(toolbar, "Exportar", self.export, GREEN, side="right")
        ttk.Combobox(
            toolbar, textvariable=self.export_resolution,
            values=["Auto", "1080p", "720p", "9:16"],
            state="readonly", width=7,
        ).pack(side="right", padx=2)
        ttk.Combobox(
            toolbar, textvariable=self.export_mode,
            values=["Todos los canales activos", "Solo el canal seleccionado"],
            state="readonly", width=22,
        ).pack(side="right", padx=4)
        self.select_tool_btn = self._button(toolbar, "↖ Seleccionar (V)", lambda: self.set_tool("select"), BLUE)
        self.razor_tool_btn = self._button(toolbar, "✂ Cuchilla (C)", lambda: self.set_tool("razor"), PANEL2)
        self.undo_btn = self._button(toolbar, "↶", self.undo, padx=1)
        self.redo_btn = self._button(toolbar, "↷", self.redo, padx=1)
        self.snap_btn = self._button(toolbar, "🧲 Imán ON", self.toggle_snap, BLUE)
        self._button(toolbar, "＋ Abrir", self.open_media, BLUE)
        self._button(toolbar, "＋ Vídeo", lambda: self.add_track("video"))
        self._button(toolbar, "＋ Audio", lambda: self.add_track("audio"))
        self._button(toolbar, "💾", self.save_project_dialog, padx=2)
        self._button(toolbar, "📂", self.load_project_dialog, padx=2)
        self.set_tool("select")
        shortcuts = {
            "<KeyPress-v>": (lambda: self.set_tool("select"), True),
            "<KeyPress-V>": (lambda: self.set_tool("select"), True),
            "<KeyPress-c>": (lambda: self.set_tool("razor"), True),
            "<KeyPress-C>": (lambda: self.set_tool("razor"), True),
            "<KeyPress-s>": (self.split_at_playhead, True),
            "<KeyPress-S>": (self.split_at_playhead, True),
            "<bracketleft>": (lambda: self.trim_to_playhead("left"), True),
            "<bracketright>": (lambda: self.trim_to_playhead("right"), True),
            "<space>": (self.play_from_cursor, False),
            "<Delete>": (self._delete_shortcut, False),
            "<Shift-Delete>": (self.ripple_delete_clip, False),
            "<BackSpace>": (self._delete_shortcut, False),
            "<Control-z>": (self.undo, False),
            "<Control-Z>": (self.redo, False),
            "<Control-y>": (self.redo, False),
            "<Control-Y>": (self.redo, False),
            "<Control-k>": (self.split_at_playhead, False),
            "<Control-K>": (self.split_at_playhead, False),
            "<Control-c>": (self.copy_clip, False),
            "<Control-C>": (self.copy_clip, False),
            "<Control-v>": (self.paste_clip, False),
            "<Control-V>": (self.paste_clip, False),
            "<Control-d>": (self.duplicate_clip, False),
            "<Control-D>": (self.duplicate_clip, False),
            "<Control-s>": (self.save_project_dialog, False),
            "<Control-S>": (self.save_project_dialog, False),
            "<Control-o>": (self.load_project_dialog, False),
            "<Control-O>": (self.load_project_dialog, False),
            "<Left>": (lambda: self._step_frames(-1), False),
            "<Right>": (lambda: self._step_frames(1), False),
            "<Home>": (lambda: self._move_playhead(0.0), False),
            "<End>": (lambda: self._move_playhead(self.project.duration()), False),
        }
        for sequence, (handler, no_mod) in shortcuts.items():
            self.bind_all(sequence, lambda event, handler=handler, no_mod=no_mod: self._shortcut(event, handler, no_mod), add="+")

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
        self.media_list.bind("<Double-Button-1>", lambda _e: self._insert_selected_media())
        self.media_list.bind("<ButtonPress-1>", self._library_drag_start)
        self.media_list.bind("<B1-Motion>", self._library_drag_motion)
        self.media_list.bind("<ButtonRelease-1>", self._library_drag_release)

        tk.Label(inspector_panel, text="Clip seleccionado", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 3))
        tk.Label(inspector_panel, textvariable=self.clip_title_var, bg=PANEL, fg=TEXT, wraplength=220, justify="left", anchor="w").pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(inspector_panel, textvariable=self.clip_info_var, bg=PANEL, fg=MUTED, justify="left", anchor="w").pack(fill="x", padx=10, pady=2)

        vol_row = tk.Frame(inspector_panel, bg=PANEL)
        vol_row.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(vol_row, text="Volumen (%)", bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.volume_spin = ttk.Spinbox(vol_row, from_=0, to=200, increment=5, textvariable=self.volume_var, width=6, command=self._apply_audio_props)
        self.volume_spin.pack(side="right")
        self.volume_spin.bind("<Return>", lambda _e: (self._apply_audio_props(), self.timeline.focus_set()))
        self.volume_spin.bind("<FocusOut>", lambda _e: self._apply_audio_props())

        fade_row = tk.Frame(inspector_panel, bg=PANEL)
        fade_row.pack(fill="x", padx=10, pady=(4, 4))
        tk.Label(fade_row, text="Fundido E/S (s)", bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.fade_out_spin = ttk.Spinbox(fade_row, from_=0.0, to=10.0, increment=0.25, textvariable=self.fade_out_var, width=5, command=self._apply_audio_props)
        self.fade_out_spin.pack(side="right", padx=(2, 0))
        self.fade_in_spin = ttk.Spinbox(fade_row, from_=0.0, to=10.0, increment=0.25, textvariable=self.fade_in_var, width=5, command=self._apply_audio_props)
        self.fade_in_spin.pack(side="right")
        self.fade_in_spin.bind("<Return>", lambda _e: (self._apply_audio_props(), self.timeline.focus_set()))
        self.fade_in_spin.bind("<FocusOut>", lambda _e: self._apply_audio_props())
        self.fade_out_spin.bind("<Return>", lambda _e: (self._apply_audio_props(), self.timeline.focus_set()))
        self.fade_out_spin.bind("<FocusOut>", lambda _e: self._apply_audio_props())

        tk.Label(inspector_panel, text="Cambio de tono", bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.pitch_combo = ttk.Combobox(inspector_panel, textvariable=self.pitch_var, values=self.pitch_options, state="readonly")
        self.pitch_combo.pack(fill="x", padx=10, pady=(0, 3))
        self.pitch_combo.bind("<<ComboboxSelected>>", self._pitch_changed)
        tk.Label(inspector_panel, textvariable=self.pitch_status_var, bg=PANEL, fg=MUTED, wraplength=220, justify="left").pack(fill="x", padx=10, pady=(0, 6))
        self.inspector_audio_btn = self._button(inspector_panel, "Audio original ON/OFF", self.toggle_clip_audio, side="top")
        self.unlink_btn = self._button(inspector_panel, "Desvincular audio/vídeo", self.toggle_linked_media, side="top")
        self._button(inspector_panel, "Duplicar clip (Ctrl+D)", self.duplicate_clip, side="top")
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
            width=70, height=16,
        )
        self.preview.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        transport = tk.Frame(viewer_panel, bg=PANEL, padx=10, pady=4)
        transport.pack(fill="x")
        self._button(transport, "⏮", lambda: self._move_playhead(0.0), padx=2)
        self._button(transport, "◀", lambda: self._step_frames(-1), padx=2)
        self.play_btn = self._button(transport, "▶", self.play_from_cursor, BLUE, padx=4)
        self._button(transport, "▶|", lambda: self._step_frames(1), padx=2)
        self._button(transport, "⏭", lambda: self._move_playhead(self.project.duration()), padx=2)
        self._button(transport, "📷", self.capture_frame, padx=2)
        tk.Label(transport, textvariable=self.timecode_var, bg=PANEL, fg=TEXT, font=("Consolas", 10, "bold")).pack(side="right")

        timeline_panel = tk.Frame(self, bg=PANEL)
        timeline_panel.pack(fill="both", expand=True, padx=10, pady=(4, 5))
        timeline_header = tk.Frame(timeline_panel, bg=PANEL, padx=8, pady=5)
        timeline_header.pack(fill="x")
        tk.Label(timeline_header, text="Línea de tiempo", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Checkbutton(
            timeline_header, text="Pista principal magnética", variable=self.magnetic_main,
            command=self._magnetic_toggled, bg=PANEL, fg=TEXT, selectcolor=PANEL2,
            activebackground=PANEL, activeforeground=TEXT, font=("Segoe UI", 9),
        ).pack(side="left", padx=(10, 4))
        self._button(timeline_header, "✂ Dividir (S)", self.split_at_playhead, BLUE, side="right")
        size_box = tk.Frame(timeline_header, bg=PANEL)
        size_box.pack(side="right", padx=(4, 8))
        tk.Label(size_box, text="Altura", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(0, 2))
        for preset in ("S", "M", "L"):
            self._button(size_box, preset, lambda preset=preset: self.set_all_track_heights(preset), padx=1)
        zoom_box = tk.Frame(timeline_header, bg=PANEL)
        zoom_box.pack(side="right", padx=4)
        tk.Label(zoom_box, text="Zoom", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(0, 2))
        self._button(zoom_box, "−", lambda: self.set_zoom(self.zoom / 1.25), padx=1)
        self._button(zoom_box, "＋", lambda: self.set_zoom(self.zoom * 1.25), padx=1)
        self._button(zoom_box, "Ajustar", self.zoom_to_fit, padx=1)

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
        self.timeline.bind("<ButtonPress-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)
        self.timeline.bind("<Button-2>", self._temporary_razor_press)
        self.timeline.bind("<ButtonRelease-2>", self._temporary_razor_release)
        self.timeline.bind("<Button-3>", self._timeline_context_menu)
        self.timeline.bind("<Motion>", self._timeline_hover)
        self.timeline.bind("<Leave>", lambda _event: self._set_hover_boundary(None))
        self.timeline.bind("<MouseWheel>", self._timeline_wheel)
        self.timeline.bind("<Button-4>", lambda event: self._timeline_wheel(event, 120))
        self.timeline.bind("<Button-5>", lambda event: self._timeline_wheel(event, -120))
        self._install_drop_target(self.timeline)
        self._install_drop_target(self.media_list)

        self.status = tk.Label(self, text="Abre un archivo para empezar.", bg=BG, fg=MUTED, anchor="w", padx=12, pady=5)
        self.status.pack(fill="x")
        self.add_track("video")
        self.add_track("audio")
        self.undo_stack.clear()
        self._update_undo_buttons()

    def add_track(self, kind, record_undo=True):
        if record_undo:
            self._push_undo()
        name = unique_track_name([track.name for track in self.project.tracks], kind)
        track = Track(name, kind)
        self.project.tracks.append(track)
        self.project.selected_track_index = len(self.project.tracks) - 1
        self._refresh_tracks()
        self.redraw_timeline()
        return track

    # ------------------------------------------------------------------
    # Atajos, deshacer/rehacer
    # ------------------------------------------------------------------

    def _shortcut(self, event, handler, no_modifiers=False):
        """Ejecuta un atajo solo si el editor está visible y no se está escribiendo en un campo de texto."""
        if no_modifiers and (getattr(event, "state", 0) & (0x0004 | 0x0008 | 0x20000)):
            return None
        try:
            if not self.winfo_ismapped():
                return None
            focus = self.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if isinstance(focus, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox, ttk.Spinbox)) and not isinstance(focus, ttk.Combobox):
            return None
        if isinstance(focus, ttk.Combobox) and str(focus.cget("state")) != "readonly":
            return None
        handler()
        return "break"

    def _snapshot(self):
        return copy.deepcopy((self.project.tracks, self.project.selected_track_index, self.track_heights))

    def _push_undo(self, snapshot=None):
        if not hasattr(self, "undo_btn"):
            return
        self.undo_stack.append(snapshot if snapshot is not None else self._snapshot())
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        self._update_undo_buttons()

    def _restore(self, snapshot):
        tracks, selected_index, heights = copy.deepcopy(snapshot)
        self.project.tracks = tracks
        self.project.selected_track_index = selected_index
        self.track_heights = heights
        self.selected_clip = None
        self.drag_clip = None
        total = self.project.duration()
        if self.playhead_timeline > total:
            self.playhead_timeline = total
        _t, active_clip = self._clip_at_playhead()
        if active_clip and active_clip.media.has_video:
            self.selected_media = active_clip.media
            pos = timeline_to_source_time(active_clip, self.playhead_timeline)
            if pos is not None:
                self.request_frame(pos)
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()

    def undo(self):
        if self.playing:
            self.stop_playback()
        if not self.undo_stack:
            self.status.configure(text="No hay nada que deshacer.", fg=MUTED)
            return
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())
        self._update_undo_buttons()
        self.status.configure(text="Deshecho (Ctrl+Z). Rehacer con Ctrl+Y.", fg=YELLOW)

    def redo(self):
        if self.playing:
            self.stop_playback()
        if not self.redo_stack:
            self.status.configure(text="No hay nada que rehacer.", fg=MUTED)
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())
        self._update_undo_buttons()
        self.status.configure(text="Rehecho (Ctrl+Y).", fg=YELLOW)

    def _update_undo_buttons(self):
        if hasattr(self, "undo_btn"):
            self.undo_btn.configure(fg=TEXT if self.undo_stack else MUTED)
            self.redo_btn.configure(fg=TEXT if self.redo_stack else MUTED)

    # ------------------------------------------------------------------
    # Geometría de pistas
    # ------------------------------------------------------------------

    def _row_height(self, track):
        return self.track_heights.get(track.name, DEFAULT_TRACK_HEIGHTS.get(track.kind, 56.0))

    def _layout(self):
        return track_layout([self._row_height(track) for track in self.project.tracks], RULER_HEIGHT)

    def _time_at_x(self, canvas_x):
        return max(0.0, (canvas_x - LABEL_WIDTH) / self.zoom)

    def _x_at_time(self, seconds):
        return LABEL_WIDTH + seconds * self.zoom

    def _track_at_y(self, canvas_y):
        index = track_index_at(self._layout(), canvas_y)
        return None if index is None else self.project.tracks[index]

    def set_all_track_heights(self, preset):
        self._push_undo()
        for track in self.project.tracks:
            self.track_heights[track.name] = TRACK_SIZE_PRESETS[preset]
        self.redraw_timeline()

    def set_zoom(self, value, anchor_time=None, anchor_offset=None):
        old_zoom = self.zoom
        self.zoom = max(2.0, min(400.0, float(value)))
        if anchor_time is None:
            anchor_time = self.playhead_timeline
        self.redraw_timeline()
        region = self.timeline.cget("scrollregion").split()
        if region and old_zoom != self.zoom:
            total = float(region[2]) or 1.0
            offset = (self.timeline.winfo_width() / 2) if anchor_offset is None else float(anchor_offset)
            left = max(0.0, self._x_at_time(anchor_time) - offset)
            self.timeline.xview_moveto(left / total)
        self.status.configure(text=f"Zoom: {self.zoom:.0f} px/s", fg=MUTED)

    def zoom_to_fit(self):
        duration = max(5.0, self.project.duration())
        width = max(200, self.timeline.winfo_width() - LABEL_WIDTH - 20)
        self.set_zoom(width / duration, anchor_time=0.0)
        self.timeline.xview_moveto(0)

    def _timeline_wheel(self, event, delta=None):
        delta = delta if delta is not None else event.delta
        step = 1 if delta > 0 else -1
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)
        alt = bool(event.state & 0x20000) or bool(event.state & 0x0008)
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        if ctrl:
            track = self._track_at_y(y)
            if track:
                new_height = clamp_track_height(self._row_height(track) + step * 8)
                if new_height != self._row_height(track):
                    self._push_undo()
                    self.track_heights[track.name] = new_height
                    self.redraw_timeline()
        elif alt:
            self.set_zoom(self.zoom * (1.2 if step > 0 else 1 / 1.2), anchor_time=self._time_at_x(x), anchor_offset=event.x)
        elif shift:
            self.timeline.xview_scroll(-step * 3, "units")
        else:
            self.timeline.yview_scroll(-step * 2, "units")
        return "break"

    def _set_hover_boundary(self, index):
        if index != self.hover_boundary:
            self.hover_boundary = index
            self.redraw_timeline()

    def _timeline_hover(self, event):
        if self.drag_clip or self.resize_track_index is not None:
            return
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        boundary = track_boundary_at(self._layout(), y)
        self._set_hover_boundary(boundary)
        if boundary is not None:
            self.timeline.configure(cursor="sb_v_double_arrow")
            return
        hit = self._clip_at(x, y)
        if hit is None:
            self.timeline.configure(cursor="")
        elif self.tool_mode == "razor":
            self.timeline.configure(cursor="crosshair")
        else:
            _track, clip = hit
            x1 = self._x_at_time(clip.timeline_start)
            x2 = x1 + max(14, clip.duration * self.zoom)
            near_edge = abs(x - x1) <= 8 or abs(x - x2) <= 8
            self.timeline.configure(cursor="sb_h_double_arrow" if near_edge else "fleur")

    def _clip_at(self, x, y):
        items = self.timeline.find_overlapping(x, y, x, y)
        item = next((item for item in reversed(items) if item in self.timeline_items), None)
        return None if item is None else self.timeline_items[item]

    # ------------------------------------------------------------------
    # Edición magnética
    # ------------------------------------------------------------------

    def _track_of(self, clip):
        return next((track for track in self.project.tracks if clip in track.clips), None)

    def _linked_clips(self, clip):
        if not clip or not clip.link_group:
            return []
        return [other for track in self.project.tracks for other in track.clips
                if other is not clip and other.link_group == clip.link_group]

    def _main_track(self):
        return next((track for track in self.project.tracks if track.kind == "video"), None)

    def _shift_linked_clip(self, linked, wanted_start):
        """Mueve un clip vinculado a wanted_start evitando solapar clips independientes."""
        wanted = round(max(0.0, float(wanted_start)), 6)
        current_track = self._track_of(linked)
        if not current_track:
            linked.timeline_start = wanted
            return
        def _free_in(candidate_track):
            return not any(
                other is not linked and other.link_group != linked.link_group
                and other.timeline_start < wanted + linked.duration - 1e-6
                and other.timeline_end > wanted + 1e-6
                for other in candidate_track.clips
            )
        if _free_in(current_track):
            linked.timeline_start = wanted
            return
        alt_track = next(
            (t for t in self.project.tracks if t.kind == current_track.kind and t is not current_track and _free_in(t)),
            None,
        )
        if not alt_track:
            alt_track = self.add_track(current_track.kind, record_undo=False)
        current_track.clips.remove(linked)
        alt_track.clips.append(linked)
        linked.timeline_start = wanted

    def _apply_magnetic(self):
        """Si la pista principal magnética está activa, junta sus clips desde 0 sin huecos."""
        if not self.magnetic_main.get():
            return
        main = self._main_track()
        if not main:
            return
        before = {id(clip): clip for clip in main.clips}
        deltas = compact_clips(main.clips)
        for clip_id, delta in deltas.items():
            for linked in self._linked_clips(before[clip_id]):
                self._shift_linked_clip(linked, linked.timeline_start + delta)

    def _magnetic_toggled(self):
        if self.magnetic_main.get():
            self._push_undo()
            self._apply_magnetic()
            self.status.configure(text="Pista principal magnética: los clips de Vídeo 1 se mantienen juntos, sin huecos.", fg=GREEN)
        else:
            self.status.configure(text="Pista principal magnética desactivada.", fg=YELLOW)
        self.redraw_timeline()

    def _move_playhead(self, seconds):
        self.playhead_timeline = max(0.0, float(seconds))
        self._seek_timeline(type("SeekEvent", (), {"x": self._x_at_time(self.playhead_timeline) - self.timeline.canvasx(0)})())

    def split_at_playhead(self):
        """Divide en el cabezal el clip seleccionado (o los clips bajo el cabezal en la pista seleccionada)."""
        if self.playing:
            self.stop_playback()
        time_point = self.playhead_timeline
        targets = []
        if self.selected_clip and self.selected_clip.timeline_start < time_point < self.selected_clip.timeline_end:
            targets = [self.selected_clip] + self._linked_clips(self.selected_clip)
        else:
            track = self.project.selected_track()
            if track:
                for clip in track.clips:
                    if clip.timeline_start < time_point < clip.timeline_end:
                        for item in [clip] + self._linked_clips(clip):
                            if item not in targets:
                                targets.append(item)
        targets = [clip for clip in targets if split_clip(clip, time_point) is not None]
        if not targets:
            self.status.configure(text="Coloca el cabezal dentro de un clip para dividirlo (S).", fg=YELLOW)
            return
        self._push_undo()
        for clip in targets:
            track = self._track_of(clip)
            left, right = split_clip(clip, time_point)
            if clip.link_group:
                right.link_group = clip.link_group + "-b"
            index = track.clips.index(clip)
            track.clips[index:index + 1] = [left, right]
            if clip is self.selected_clip:
                self.selected_clip = left
        if self.selected_clip:
            self._sync_selection_marks(self.selected_clip)
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text=f"Clip dividido en {format_editor_time(time_point)}.", fg=GREEN)

    def ripple_delete_clip(self):
        if self.playing:
            self.stop_playback()
        clip = self.selected_clip
        track = self._track_of(clip) if clip else None
        if not track:
            self.status.configure(text="Selecciona un clip para borrarlo y cerrar el hueco (Shift+Supr).", fg=YELLOW)
            return
        self._push_undo()
        for linked in self._linked_clips(clip):
            linked_track = self._track_of(linked)
            if linked_track:
                linked_track.clips.remove(linked)
        moved = [other for other in track.clips if other is not clip and other.timeline_start >= clip.timeline_end - 1e-6]
        shift = ripple_delete(track.clips, clip)
        if shift > 0:
            for other in moved:
                for linked in self._linked_clips(other):
                    self._shift_linked_clip(linked, linked.timeline_start - shift)
        self.selected_clip = None
        self._apply_magnetic()
        self._refresh_preview_at_playhead()
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text="Clip eliminado y hueco cerrado.", fg=GREEN)

    def close_gap_at(self, track, time_point):
        gap = find_gap(track.clips, time_point)
        if gap is None:
            self.status.configure(text="Ahí no hay ningún hueco que cerrar.", fg=YELLOW)
            return
        self._push_undo()
        _gap_start, gap_end = gap
        moved = [clip for clip in track.clips if clip.timeline_start >= gap_end - 1e-6]
        shift = close_gap(track.clips, time_point)
        if shift > 0:
            for clip in moved:
                for linked in self._linked_clips(clip):
                    self._shift_linked_clip(linked, linked.timeline_start - shift)
        self.redraw_timeline()
        self.status.configure(text=f"Hueco de {format_editor_time(shift)} cerrado en {track.name}.", fg=GREEN)

    def _timeline_context_menu(self, event):
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        track = self._track_at_y(y)
        if not track:
            return
        time_point = self._time_at_x(x)
        menu = tk.Menu(self, tearoff=0, bg=PANEL2, fg=TEXT, activebackground=BLUE, activeforeground="#ffffff")
        hit = self._clip_at(x, y)
        if hit:
            _track, clip = hit
            self.selected_clip = clip
            self.selected_media = clip.media
            self._update_inspector()
            self.redraw_timeline()
            menu.add_command(label="Dividir en el cabezal    S", command=self.split_at_playhead)
            menu.add_command(label="Copiar    Ctrl+C", command=self.copy_clip)
            menu.add_command(label="Duplicar    Ctrl+D", command=self.duplicate_clip)
            menu.add_command(label="Eliminar    Supr", command=self.delete_clip)
            menu.add_command(label="Eliminar y cerrar hueco    Shift+Supr", command=self.ripple_delete_clip)
            menu.add_separator()
            menu.add_command(label="Audio del clip ON/OFF", command=self.toggle_clip_audio)
        else:
            if self.clipboard_clip is not None:
                menu.add_command(label="Pegar aquí    Ctrl+V", command=lambda: self.paste_clip(time_point, track))
            state = "normal" if find_gap(track.clips, time_point) else "disabled"
            menu.add_command(label="Cerrar hueco", state=state, command=lambda: self.close_gap_at(track, time_point))
        menu.add_separator()
        for preset, label in (("S", "pequeña"), ("M", "mediana"), ("L", "grande")):
            menu.add_command(label=f"Altura de {track.name}: {label}",
                             command=lambda preset=preset, track=track: self._set_track_height(track, TRACK_SIZE_PRESETS[preset]))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_track_height(self, track, height):
        self._push_undo()
        self.track_heights[track.name] = clamp_track_height(height)
        self.redraw_timeline()

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

    def _add_or_get_media(self, path):
        key = os.path.normcase(os.path.abspath(path))
        for existing in self.media_library:
            if os.path.normcase(os.path.abspath(existing.path)) == key:
                return existing
        media = probe_media(path)
        self.media_library.append(media)
        icon = "▶" if media.has_video else "♫"
        self.media_list.insert("end", f"{icon}  {pathlib.Path(path).name}   ·   {format_editor_time(media.duration)}")
        return media

    def _ingest_dropped_paths(self, paths, target, event=None):
        accepted = []
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                accepted.append(self._add_or_get_media(path))
            except Exception as exc:
                self.status.configure(text=f"No se pudo abrir {pathlib.Path(path).name}: {exc}", fg=RED)
        if not accepted:
            return
        if target is self.timeline:
            event = event or type("DropEvent", (), {"x": 115, "y": 30})()
            self._drop_library_media(event, media_list=accepted)
        else:
            self.media_list.selection_clear(0, "end")
            self.media_list.selection_set(self.media_library.index(accepted[-1]))
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
        enabled = self.snap_enabled.get()
        self.snap_btn.configure(text="🧲 Imán ON" if enabled else "🧲 Imán OFF", bg=BLUE if enabled else PANEL2)
        state = "activado" if enabled else "desactivado"
        self.status.configure(text=f"Imán {state}. Mantén Alt al arrastrar para desactivarlo temporalmente.", fg=YELLOW)

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
        track = self._track_of(clip)
        if not track:
            return
        if clip.link_group:
            self._push_undo()
            linked = [other for item in self.project.tracks for other in item.clips if other.link_group == clip.link_group]
            for other in linked:
                other.link_group = None
            self.status.configure(text="Audio y vídeo desvinculados.", fg=GREEN)
            self._update_inspector()
            self.redraw_timeline()
            return
        if not clip.media.has_video or not clip.media.has_audio:
            self.status.configure(text="Selecciona un vídeo con audio para separarlo o desvincularlo.", fg=YELLOW)
            return
        self._push_undo()
        if not clip.use_source_audio:
            clip_key = os.path.normcase(os.path.abspath(clip.media.path))
            for item in self.project.tracks:
                if item.kind != "audio":
                    continue
                for other in item.clips:
                    other_key = os.path.normcase(os.path.abspath(other.media.path))
                    if (
                        not other.link_group
                        and other_key == clip_key
                        and abs(other.timeline_start - clip.timeline_start) < 0.02
                        and abs(other.source_in - clip.source_in) < 0.02
                        and abs(other.duration - clip.duration) < 0.02
                    ):
                        link_id = "link-" + str(id(clip))
                        clip.link_group = link_id
                        other.link_group = link_id
                        self._update_inspector()
                        self.status.configure(text="Audio y vídeo vinculados de nuevo.", fg=GREEN)
                        self.redraw_timeline()
                        return
        def _has_overlap(candidate_track):
            return any(
                c.timeline_start < clip.timeline_end - 1e-6 and c.timeline_end > clip.timeline_start + 1e-6
                for c in candidate_track.clips
            )
        audio_track = next((item for item in self.project.tracks if item.kind == "audio" and not _has_overlap(item)), None)
        if not audio_track:
            audio_track = self.add_track("audio", record_undo=False)
        audio_media = audio_media_from_video(clip.media)
        link_id = "link-" + str(id(clip))
        audio_clip = Clip(
            audio_media, clip.source_in, clip.source_out, clip.timeline_start,
            name=f"Audio · {clip.name}", link_group=link_id,
            pitch_semitones=clip.pitch_semitones, volume=clip.volume,
            fade_in=clip.fade_in, fade_out=clip.fade_out,
        )
        clip.link_group = link_id
        clip.use_source_audio = False
        audio_track.clips.append(audio_clip)
        self._update_inspector()
        self.status.configure(text="Audio separado a pista de audio (sin duplicar sonido).", fg=GREEN)
        self.redraw_timeline()

    def _apply_audio_props(self):
        clip = self.selected_clip
        if not clip:
            return
        try:
            vol = max(0.0, min(2.0, float(self.volume_var.get()) / 100.0))
            fade_in, fade_out = clamp_fades(float(self.fade_in_var.get()), float(self.fade_out_var.get()), clip.duration)
        except (ValueError, TypeError, tk.TclError):
            return
        if abs(clip.volume - vol) < 1e-4 and abs(clip.fade_in - fade_in) < 1e-4 and abs(clip.fade_out - fade_out) < 1e-4:
            return
        self._push_undo()
        for target in [clip] + self._linked_clips(clip):
            t_in, t_out = clamp_fades(fade_in, fade_out, target.duration)
            target.volume = vol
            target.fade_in = t_in
            target.fade_out = t_out
        self.fade_in_var.set(round(fade_in, 2))
        self.fade_out_var.set(round(fade_out, 2))
        self.redraw_timeline()
        self.status.configure(text=f"Volumen: {int(round(vol * 100))}% · Fundidos: {fade_in:.2f}s / {fade_out:.2f}s", fg=GREEN)

    def _pitch_changed(self, _event=None):
        if not self.selected_clip:
            return
        label = self.pitch_var.get().split("·", 1)[0].strip()
        try:
            value = int(label)
        except (ValueError, TypeError):
            return
        if self.selected_clip.pitch_semitones == value:
            return
        self._push_undo()
        for target in [self.selected_clip] + self._linked_clips(self.selected_clip):
            target.pitch_semitones = value
        self._update_inspector()
        self.status.configure(text=f"Tono del clip: {value:+d} semitonos.", fg=GREEN)

    def _update_inspector(self):
        clip = self.selected_clip
        if not clip:
            self.clip_title_var.set("Ningún clip seleccionado")
            self.clip_info_var.set("Selecciona un clip en la línea de tiempo")
            self.pitch_var.set("0 · Original")
            self.pitch_status_var.set("Tono original: no detectado")
            self.volume_var.set(100)
            self.fade_in_var.set(0.0)
            self.fade_out_var.set(0.0)
            self.unlink_btn.configure(text="Separar audio/vídeo")
            return
        self.clip_title_var.set(clip.name)
        self.clip_info_var.set(
            f"Duración: {format_editor_time(clip.duration)}\n"
            f"Entrada: {format_editor_time(clip.source_in)}\n"
            f"Salida: {format_editor_time(clip.source_out)}"
        )
        self.volume_var.set(int(round(clip.volume * 100)))
        self.fade_in_var.set(round(clip.fade_in, 2))
        self.fade_out_var.set(round(clip.fade_out, 2))
        self.pitch_var.set(self.pitch_options[clip.pitch_semitones + 12])
        self.pitch_status_var.set("Tono original: no detectado · ajuste relativo al original")
        linked_audio = [o for o in self._linked_clips(clip) if o.media.has_audio]
        audio_on = linked_audio[0].use_source_audio if (clip.media.has_video and linked_audio and not clip.use_source_audio) else clip.use_source_audio
        self.inspector_audio_btn.configure(text="Audio original ON" if audio_on else "Audio original OFF")
        self.unlink_btn.configure(text="Desvincular audio/vídeo" if clip.link_group else "Separar audio/vídeo")

    def _clone_for_clipboard(self, clip):
        cloned = copy.deepcopy(clip)
        if clip.link_group and clip.media.has_audio and not clip.use_source_audio:
            linked_audio = [o for o in self._linked_clips(clip) if o.media.has_audio]
            if linked_audio:
                cloned.use_source_audio = linked_audio[0].use_source_audio
                cloned.volume = linked_audio[0].volume
                cloned.pitch_semitones = linked_audio[0].pitch_semitones
                cloned.fade_in = min(cloned.duration, linked_audio[0].fade_in)
                cloned.fade_out = min(cloned.duration, linked_audio[0].fade_out)
            else:
                cloned.use_source_audio = True
        cloned.link_group = None
        return cloned

    def copy_clip(self):
        if not self.selected_clip:
            self.status.configure(text="Selecciona un clip para copiar (Ctrl+C).", fg=YELLOW)
            return
        self.clipboard_clip = self._clone_for_clipboard(self.selected_clip)
        self.status.configure(text=f"Copiado: {self.clipboard_clip.name}", fg=GREEN)

    def paste_clip(self, at_time=None, target_track=None):
        if not self.clipboard_clip:
            self.status.configure(text="No hay ningún clip copiado.", fg=YELLOW)
            return
        kind = "video" if self.clipboard_clip.media.has_video else "audio"
        track = target_track or self.project.selected_track()
        if not track or track.kind != kind:
            track = next((t for t in self.project.tracks if t.kind == kind), None)
        if not track:
            track = self.add_track(kind, record_undo=False)
        self._push_undo()
        pasted = copy.deepcopy(self.clipboard_clip)
        wanted = self.playhead_timeline if at_time is None else float(at_time)
        ranges = [(c.timeline_start, c.timeline_end) for c in track.clips]
        pasted.timeline_start = round(avoid_clip_overlap(wanted, pasted.duration, ranges), 6)
        track.clips.append(pasted)
        self.selected_clip = pasted
        self.selected_media = pasted.media
        self.project.selected_track_index = self.project.tracks.index(track)
        if track is self._main_track():
            self._apply_magnetic()
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text=f"Pegado: {pasted.name}", fg=GREEN)

    def duplicate_clip(self):
        if not self.selected_clip:
            self.status.configure(text="Selecciona un clip para duplicarlo (Ctrl+D).", fg=YELLOW)
            return
        track = self._track_of(self.selected_clip)
        if not track:
            return
        self.clipboard_clip = self._clone_for_clipboard(self.selected_clip)
        self.paste_clip(at_time=self.selected_clip.timeline_end, target_track=track)

    def _step_frames(self, count):
        _track, clip = self._clip_at_playhead()
        ref = clip or self.selected_clip
        fps = ref.media.fps if ref and ref.media.fps > 0 else (self.selected_media.fps if self.selected_media and self.selected_media.fps > 0 else 30.0)
        self._move_playhead(max(0.0, self.playhead_timeline + count / fps))

    def save_project_dialog(self):
        path = filedialog.asksaveasfilename(
            title="Guardar proyecto", defaultextension=".avep",
            initialfile="proyecto.avep",
            filetypes=[("Proyecto AveDescargas", "*.avep"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            save_project(path, self.project, self.track_heights, media_library=self.media_library)
            self.status.configure(text=f"Proyecto guardado: {pathlib.Path(path).name}", fg=GREEN)
        except Exception as exc:
            messagebox.showerror("Error al guardar", str(exc))

    def load_project_dialog(self):
        path = filedialog.askopenfilename(
            title="Abrir proyecto",
            filetypes=[("Proyecto AveDescargas", "*.avep"), ("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not path:
            return
        try:
            project, heights, media_list = load_project(path)
        except Exception as exc:
            messagebox.showerror("Error al abrir proyecto", str(exc))
            return
        self.stop_playback()
        self._push_undo()
        self.project = project
        self.track_heights = heights
        self.selected_clip = None
        self.selected_media = None
        self.waveform_cache.clear()
        self.waveform_pending.clear()
        by_path = {}
        for media in media_list:
            key = os.path.normcase(os.path.abspath(media.path))
            if key not in by_path or (media.has_video and not by_path[key].has_video):
                by_path[key] = media
        for track in self.project.tracks:
            for clip in track.clips:
                key = os.path.normcase(os.path.abspath(clip.media.path))
                canonical = by_path.get(key)
                if canonical:
                    clip.media = canonical if clip.media.has_video else (audio_media_from_video(canonical) if canonical.has_video else canonical)
        unique_media = list(by_path.values())
        self.media_library = unique_media
        self.media_list.delete(0, "end")
        missing = []
        for media in unique_media:
            exists = os.path.isfile(media.path)
            if not exists:
                missing.append(media.path)
            icon = "▶" if media.has_video else "♫"
            suffix = "" if exists else " (no encontrado)"
            self.media_list.insert("end", f"{icon}  {pathlib.Path(media.path).name}   ·   {format_editor_time(media.duration)}{suffix}")
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()
        if missing:
            messagebox.showwarning(
                "Archivos no encontrados",
                "Algunos archivos del proyecto ya no están en su ruta:\n\n" + "\n".join(missing[:8]),
            )
            self.status.configure(text=f"Proyecto cargado con {len(missing)} archivo(s) ausente(s).", fg=YELLOW)
        else:
            self.status.configure(text=f"Proyecto cargado: {pathlib.Path(path).name}", fg=GREEN)

    def toggle_track(self):
        track = self.project.selected_track()
        if track:
            self._push_undo()
            track.enabled = not track.enabled
            self._refresh_tracks()
            self.redraw_timeline()

    def toggle_mute(self):
        track = self.project.selected_track()
        if track:
            self._push_undo()
            track.muted = not track.muted
            self._refresh_tracks()
            self.redraw_timeline()

    def delete_track(self):
        index = self.project.selected_track_index
        if 0 <= index < len(self.project.tracks):
            self._push_undo()
            removed = self.project.tracks.pop(index)
            removed_groups = {c.link_group for c in removed.clips if c.link_group}
            for track in self.project.tracks:
                for clip in track.clips:
                    if clip.link_group in removed_groups and not self._linked_clips(clip):
                        clip.link_group = None
                        if clip.media.has_video and clip.media.has_audio:
                            clip.use_source_audio = True
            self.track_heights.pop(removed.name, None)
            self.project.selected_track_index = max(0, min(index, len(self.project.tracks) - 1))
            self.selected_clip = None
            self._refresh_tracks()
            self._update_inspector()
            self.redraw_timeline()

    def open_media(self):
        paths = filedialog.askopenfilenames(
            title="Abrir vídeo o canción",
            filetypes=[("Vídeos y canciones", "*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("Todos", "*.*")],
        )
        last_media = None
        for path in paths:
            try:
                last_media = self._add_or_get_media(path)
            except Exception as exc:
                messagebox.showerror("No se pudo abrir", f"{path}\n\n{exc}")
        if last_media is not None:
            self.media_list.selection_clear(0, "end")
            self.media_list.selection_set(self.media_library.index(last_media))
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

    def _drop_library_media(self, event, media_list=None):
        items = list(media_list) if media_list is not None else ([self.pending_library_media] if self.pending_library_media else [])
        self.pending_library_media = None
        if not items:
            return False
        track = self._track_at_y(self.timeline.canvasy(event.y))
        compatible = [
            m for m in items
            if track and not ((track.kind == "video" and not m.has_video) or (track.kind == "audio" and not m.has_audio))
        ]
        if not track or not compatible:
            self.status.configure(text="Suelta el archivo en un canal compatible.", fg=RED)
            return True
        self._push_undo()
        raw_start = self._time_at_x(self.timeline.canvasx(event.x))
        added = []
        for media in compatible:
            clip = Clip(media, timeline_start=raw_start, name=pathlib.Path(media.path).stem)
            if self.snap_enabled.get():
                points = collect_snap_points(self.project.tracks, playhead=self.playhead_timeline)
                raw_start, _target = snap_timeline_position(raw_start, clip.duration, points, snap_threshold(self.zoom))
            ranges = [(other.timeline_start, other.timeline_end) for other in track.clips]
            clip.timeline_start = round(avoid_clip_overlap(raw_start, clip.duration, ranges), 6)
            track.clips.append(clip)
            added.append(clip)
            raw_start = clip.timeline_end
        last_clip = added[-1]
        self.selected_clip = last_clip
        self.selected_media = last_clip.media
        self._sync_selection_marks(last_clip)
        self.project.selected_track_index = self.project.tracks.index(track)
        if track is self._main_track():
            self._apply_magnetic()
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()
        if len(added) == 1:
            self.status.configure(text=f"Añadido: {pathlib.Path(last_clip.media.path).name}", fg=GREEN)
        else:
            self.status.configure(text=f"Añadidos {len(added)} clips.", fg=GREEN)
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
        self.keep_audio.set(self.selected_media.has_audio)
        if self.selected_clip and self.selected_clip.media is not self.selected_media:
            self.selected_clip = None
            self._update_inspector()
            self.redraw_timeline()
        if self.selected_media.has_video and os.path.isfile(self.selected_media.path):
            self.request_frame(0.0)
        elif not self.selected_media.has_video:
            self.preview_photo = None
            self.preview.configure(image="", text=f"♫  {pathlib.Path(self.selected_media.path).name}")
        self.status.configure(text="Doble clic o arrastra a un canal para añadirlo a la línea de tiempo.", fg=TEXT)

    def _insert_selected_media(self):
        selection = self.media_list.curselection()
        if not selection:
            return
        media = self.media_library[selection[0]]
        kind = "video" if media.has_video else "audio"
        track = self.project.selected_track()
        if not track or track.kind != kind:
            track = next((t for t in self.project.tracks if t.kind == kind), None)
        if not track:
            track = self.add_track(kind, record_undo=False)
        self._push_undo()
        clip = Clip(media, timeline_start=self.playhead_timeline, name=pathlib.Path(media.path).stem)
        ranges = [(other.timeline_start, other.timeline_end) for other in track.clips]
        clip.timeline_start = round(avoid_clip_overlap(self.playhead_timeline, clip.duration, ranges), 6)
        track.clips.append(clip)
        self.selected_clip = clip
        self.selected_media = media
        self._sync_selection_marks(clip)
        self.project.selected_track_index = self.project.tracks.index(track)
        if track is self._main_track():
            self._apply_magnetic()
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()
        if media.has_video and os.path.isfile(media.path):
            self.request_frame(clip.source_in)
        self.status.configure(text=f"Añadido al timeline: {pathlib.Path(media.path).name}", fg=GREEN)

    def remove_selected_library_media(self):
        selection = self.media_list.curselection()
        if not selection:
            return
        index = selection[0]
        media = self.media_library[index]
        media_key = os.path.normcase(os.path.abspath(media.path))
        in_use = any(
            os.path.normcase(os.path.abspath(c.media.path)) == media_key
            for t in self.project.tracks for c in t.clips
        )
        if in_use:
            self.status.configure(text="No se puede quitar: este archivo tiene clips en la línea de tiempo.", fg=YELLOW)
            return
        self.media_library.pop(index)
        self.media_list.delete(index)
        if self.selected_media is media:
            self.selected_media = None
        self.status.configure(text=f"Quitado de la lista: {pathlib.Path(media.path).name}", fg=TEXT)

    def _delete_shortcut(self):
        try:
            focus = self.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if focus is self.media_list and self.media_list.curselection():
            self.remove_selected_library_media()
        else:
            self.delete_clip()

    def trim_to_playhead(self, side):
        if self.playing:
            self.stop_playback()
        clip = self.selected_clip
        track = self._track_of(clip) if clip else None
        if not clip or not track:
            self.status.configure(text="Selecciona un clip para recortarlo hasta el cabezal ([ / ]).", fg=YELLOW)
            return
        target = self.playhead_timeline
        if side == "left":
            if target <= clip.timeline_start + 1e-6 or target >= clip.timeline_end - MIN_CLIP_DURATION:
                self.status.configure(text="Coloca el cabezal dentro del clip para recortar su inicio ([).", fg=YELLOW)
                return
            self._push_undo()
            updated = resize_clip_left(clip, target)
            self._replace_clip(track, clip, updated)
            for other in self._linked_clips(updated):
                self._replace_linked(other, resize_clip_left(other, updated.timeline_start))
        else:
            if target >= clip.timeline_end - 1e-6 or target <= clip.timeline_start + MIN_CLIP_DURATION:
                self.status.configure(text="Coloca el cabezal dentro del clip para recortar su final (]).", fg=YELLOW)
                return
            self._push_undo()
            updated = resize_clip_right(clip, target)
            self._replace_clip(track, clip, updated)
            for other in self._linked_clips(updated):
                self._replace_linked(other, resize_clip_right(other, updated.timeline_end))
        self.selected_clip = updated
        self._sync_selection_marks(updated)
        self._apply_magnetic()
        self._update_inspector()
        self.redraw_timeline()
        if updated.media.has_video:
            self.request_frame(self.cursor_time)
        self.status.configure(text=f"Clip recortado hasta {format_editor_time(target)}.", fg=GREEN)

    def _refresh_preview_at_playhead(self):
        _t, active = self._clip_at_playhead()
        if active and active.media.has_video:
            self.selected_media = active.media
            pos = timeline_to_source_time(active, self.playhead_timeline)
            if pos is not None:
                self.cursor_time = pos
                self.request_frame(pos)
            return
        if active and not active.media.has_video:
            self.selected_media = active.media
            self.preview_photo = None
            self.preview.configure(image="", text=f"♫  {pathlib.Path(active.media.path).name}")
            return
        if not any(t.clips for t in self.project.tracks):
            self.selected_media = None
            self.preview_photo = None
            self.preview.configure(image="", text="Abre un vídeo o una canción")
        else:
            self.preview_photo = None
            self.preview.configure(image="", text="")

    def capture_frame(self):
        media = self.selected_media
        if not media or not media.has_video:
            self.status.configure(text="Selecciona un vídeo para capturar un fotograma.", fg=YELLOW)
            return
        if not os.path.isfile(media.path):
            self.status.configure(text=f"Archivo no encontrado: {os.path.basename(media.path)}", fg=RED)
            return
        default_name = f"{pathlib.Path(media.path).stem}_frame.png"
        path = filedialog.asksaveasfilename(
            title="Guardar fotograma", defaultextension=".png", initialfile=default_name,
            filetypes=[("Imagen PNG", "*.png"), ("Imagen JPEG", "*.jpg")],
        )
        if not path:
            return
        try:
            ffmpeg = discover_ffmpeg_tools()[0]
            subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-ss", f"{self.cursor_time:.3f}", "-i", media.path, "-frames:v", "1", path],
                capture_output=True, text=True, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.status.configure(text=f"Fotograma guardado: {pathlib.Path(path).name}", fg=GREEN)
        except Exception as exc:
            messagebox.showerror("Error al capturar", str(exc))

    def _clip_at_playhead(self, at_time=None):
        """Busca primero en pistas de vídeo activas (de arriba abajo) y luego en audio."""
        pos = self.playhead_timeline if at_time is None else float(at_time)
        ordered = [t for t in reversed(self.project.tracks) if t.enabled and t.kind == "video"]
        ordered += [t for t in reversed(self.project.tracks) if t.enabled and t.kind == "audio"]
        for track in ordered:
            for clip in track.clips:
                if timeline_to_source_time(clip, pos) is not None:
                    return track, clip
        return None, None

    def _update_timecode(self):
        total = self.project.duration()
        self.timecode_var.set(f"{format_editor_time(self.playhead_timeline)} / {format_editor_time(total)}")

    def _seek_timeline(self, event):
        self.playhead_timeline = self._time_at_x(self.timeline.canvasx(event.x))
        _track, clip = self._clip_at_playhead()
        if clip:
            self.selected_clip = clip
            self.selected_media = clip.media
            self.cursor_time = timeline_to_source_time(clip, self.playhead_timeline)
            self.mark_in, self.mark_out = clip.source_in, clip.source_out
            self.keep_audio.set(clip.use_source_audio)
            self._update_inspector()
            if clip.media.has_video:
                self.request_frame(self.cursor_time)
        self._update_timecode()
        self.redraw_timeline()

    def _timeline_playhead_press(self, event):
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        if self.resize_track_index is not None or track_boundary_at(self._layout(), y) is not None:
            return
        if self._clip_at(x, y) is None:
            self._seek_timeline(event)

    def request_frame(self, seconds):
        media = self.selected_media
        if not media or not media.has_video or not os.path.isfile(media.path):
            return
        if self._frame_after_id:
            try:
                self.after_cancel(self._frame_after_id)
            except (tk.TclError, ValueError):
                pass
        self.frame_token += 1
        token = self.frame_token
        media_path = media.path
        ui_q = self.ui_queue
        width, height = self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT

        def launch():
            self._frame_after_id = None
            if token != self.frame_token:
                return

            def worker(path=media_path, tok=token, q=ui_q, w=width, h=height, sec=seconds):
                try:
                    ffmpeg = discover_ffmpeg_tools()[0]
                except Exception:
                    return
                command = [
                    ffmpeg, "-v", "error", "-ss", f"{sec:.3f}", "-i", path,
                    "-frames:v", "1", "-vf",
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-f", "image2pipe", "-vcodec", "png", "pipe:1",
                ]
                result = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode == 0 and result.stdout:
                    q.put(("frame", tok, result.stdout))

            threading.Thread(target=worker, daemon=True).start()

        self._frame_after_id = self.after(40, launch)

    def _next_clip_after(self, at_time):
        """Devuelve el siguiente (track, clip) activo en o después de at_time."""
        pos = float(at_time)
        ordered = [t for t in reversed(self.project.tracks) if t.enabled and t.kind == "video"]
        ordered += [t for t in reversed(self.project.tracks) if t.enabled and t.kind == "audio"]
        best = None
        for priority, track in enumerate(ordered):
            for clip in track.clips:
                if clip.timeline_end <= pos + 1e-6:
                    continue
                effective = max(pos, clip.timeline_start)
                key = (round(effective, 6), priority)
                if best is None or key < best[0]:
                    best = (key, track, clip)
        return (best[1], best[2]) if best else (None, None)

    def play_from_cursor(self):
        if self.playing:
            self.stop_playback()
            return
        track, clip = self._clip_at_playhead()
        if not clip:
            track, clip = self._next_clip_after(self.playhead_timeline)
            if not clip and self.playhead_timeline > 0:
                track, clip = self._next_clip_after(0.0)
            if clip:
                self.playhead_timeline = clip.timeline_start
                self._update_timecode()
                self.redraw_timeline()
        if not clip:
            self.status.configure(text="Coloca el cabezal sobre un clip de la línea de edición.", fg=YELLOW)
            return
        self._playback_from_timeline = True
        self._playback_track = track
        self.selected_clip = clip
        self.selected_media = clip.media
        self.cursor_time = timeline_to_source_time(clip, self.playhead_timeline)
        self.mark_in, self.mark_out = clip.source_in, clip.source_out
        self.keep_audio.set(clip.use_source_audio)
        self._update_inspector()
        self.toggle_play(from_mark=False)

    @staticmethod
    def _preview_fade_filters(clip_ref, start, duration, is_audio=False):
        if not clip_ref:
            return []
        filters = []
        prefix = "afade" if is_audio else "fade"
        d_in, d_out = clamp_fades(clip_ref.fade_in, clip_ref.fade_out, clip_ref.duration)
        if d_in > 0:
            fade_in_end = clip_ref.source_in + d_in
            if start < fade_in_end - 1e-4:
                st_in = max(0.0, clip_ref.source_in - start)
                rem_in = min(duration, fade_in_end - max(start, clip_ref.source_in))
                if rem_in > 0.01:
                    filters.append(f"{prefix}=t=in:st={st_in:.3f}:d={rem_in:.3f}")
        if d_out > 0:
            fade_out_start = clip_ref.source_out - d_out
            if start + duration > fade_out_start + 1e-4:
                st_out = max(0.0, fade_out_start - start)
                rem_out = min(d_out, (start + duration) - fade_out_start)
                if rem_out > 0.01:
                    filters.append(f"{prefix}=t=out:st={st_out:.3f}:d={rem_out:.3f}")
        return filters

    def _linked_audio_source(self, clip_ref):
        """Devuelve el clip de audio vinculado activo si el vídeo tiene el audio separado."""
        if not clip_ref or clip_ref.use_source_audio:
            return None
        for other in self._linked_clips(clip_ref):
            other_track = self._track_of(other)
            if other.media.has_audio and other.use_source_audio and other_track and other_track.enabled and not other_track.muted:
                return other
        return None

    def toggle_play(self, from_mark=False):
        if self.playing:
            self.stop_playback()
            return
        media = self.selected_media
        if not media:
            return
        if not os.path.isfile(media.path):
            self._playback_from_timeline = False
            self.status.configure(text=f"Archivo no encontrado: {os.path.basename(media.path)}", fg=RED)
            return
        start, end = playback_range(self.cursor_time, self.mark_in, self.mark_out, media.duration, from_mark=from_mark)
        self.cursor_time = start
        self.playback_started_at = None
        self.playback_start_time = start
        self.playback_end_time = end
        track = self._playback_track or (self._track_of(self.selected_clip) if self.selected_clip else None)
        track_muted = bool(track and track.muted)
        clip_ref = self.selected_clip if self.selected_clip and self.selected_clip.media is media else None
        if not media.has_video:
            if track_muted or (clip_ref and not clip_ref.use_source_audio):
                self._playback_from_timeline = False
                self.status.configure(text="La pista o el clip está silenciado.", fg=YELLOW)
                return
            self._play_audio_only(media, start, end)
            return
        self.playing = True
        self.play_btn.configure(text="Ⅱ")
        self.frame_token += 1
        token = self.frame_token
        duration = max(0.1, end - start)
        linked_audio = self._linked_audio_source(clip_ref)
        audio_clip = linked_audio or clip_ref
        play_audio = bool(
            (linked_audio is not None)
            or (media.has_audio and self.keep_audio.get() and not track_muted)
        )
        audio_path = linked_audio.media.path if linked_audio else media.path
        pitch_filter = build_pitch_filter(getattr(audio_clip, "pitch_semitones", 0))
        volume = getattr(audio_clip, "volume", 1.0)
        vf_parts = [
            f"scale={self.PREVIEW_WIDTH}:{self.PREVIEW_HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={self.PREVIEW_WIDTH}:{self.PREVIEW_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        ]
        vf_parts.extend(self._preview_fade_filters(clip_ref, start, duration, is_audio=False))
        audio_fade_filters = self._preview_fade_filters(audio_clip, start, duration, is_audio=True)

        def worker(
            panel_ref=weakref.ref(self),
            ui_q=self.ui_queue,
            media_path=media.path,
            preview_w=self.PREVIEW_WIDTH,
            preview_h=self.PREVIEW_HEIGHT,
            start=start,
            duration=duration,
            vf_parts=list(vf_parts),
            play_audio=play_audio,
            audio_path=audio_path,
            pitch_filter=pitch_filter,
            volume=volume,
            audio_fade_filters=list(audio_fade_filters),
            token=token,
        ):
            v_proc = None
            try:
                ffmpeg, _, ffplay = discover_ffmpeg_tools()
                command = [
                    ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-i", media_path,
                    "-t", f"{duration:.3f}", "-an", "-vf", ",".join(vf_parts),
                    "-r", "15", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ]
                v_proc = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                p = panel_ref()
                if p is not None:
                    p.video_process = v_proc
                p = None
                if play_audio:
                    audio_command = [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]
                    af_parts = []
                    if pitch_filter:
                        af_parts.append(pitch_filter)
                    if abs(volume - 1.0) > 1e-4:
                        af_parts.append(f"volume={volume:.3f}")
                    af_parts.extend(audio_fade_filters)
                    if af_parts:
                        audio_command += ["-af", ",".join(af_parts)]
                    audio_command += [audio_path]
                    a_proc = subprocess.Popen(
                        audio_command,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    p = panel_ref()
                    if p is not None:
                        p.audio_process = a_proc
                    p = None
                frame_size = preview_w * preview_h * 3
                frame_index = 0
                began = time.monotonic()
                while v_proc.poll() is None:
                    p = panel_ref()
                    if p is None or token != p.frame_token:
                        p = None
                        break
                    p = None
                    raw = v_proc.stdout.read(frame_size) if v_proc.stdout else b""
                    if len(raw) != frame_size:
                        break
                    target = frame_index / 15
                    wait = target - (time.monotonic() - began)
                    if wait > 0:
                        time.sleep(wait)
                    ui_q.put(("raw_frame", token, raw, start + target))
                    frame_index += 1
                code = v_proc.wait()
                p = panel_ref()
                if p is not None and token == p.frame_token and code not in (0, None):
                    error = v_proc.stderr.read().decode("utf-8", "replace").strip() if v_proc.stderr else ""
                    ui_q.put(("play_error", error or "FFmpeg no pudo generar la previsualización."))
                p = None
            except Exception as exc:
                p = panel_ref()
                if p is not None and token == p.frame_token:
                    ui_q.put(("play_error", str(exc)))
                p = None
            finally:
                for stream in (getattr(v_proc, "stdout", None), getattr(v_proc, "stderr", None)):
                    if stream:
                        try:
                            stream.close()
                        except Exception:
                            pass
                ui_q.put(("play_done", token))

        threading.Thread(target=worker, daemon=True).start()

    def _play_audio_only(self, media, start=None, end=None):
        if not os.path.isfile(media.path):
            self.playing = False
            self._playback_from_timeline = False
            self.play_btn.configure(text="▶")
            self.status.configure(text=f"Archivo no encontrado: {os.path.basename(media.path)}", fg=RED)
            return
        try:
            _, _, ffplay = discover_ffmpeg_tools()
        except Exception as exc:
            self.playing = False
            self._playback_from_timeline = False
            self.play_btn.configure(text="▶")
            self.status.configure(text=f"Previsualización: {exc}", fg=RED)
            return
        self.playing = True
        self.playback_started_at = time.monotonic()
        self.play_btn.configure(text="Ⅱ")
        self.frame_token += 1
        token = self.frame_token
        if start is None or end is None:
            start, end = playback_range(self.cursor_time, self.mark_in, self.mark_out, media.duration)
        duration = max(0.1, end - start)
        command = [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]
        clip_ref = self.selected_clip if self.selected_clip and self.selected_clip.media is media else None
        pitch_filter = build_pitch_filter(getattr(clip_ref, "pitch_semitones", 0))
        volume = getattr(clip_ref, "volume", 1.0)
        af_parts = []
        if pitch_filter:
            af_parts.append(pitch_filter)
        if abs(volume - 1.0) > 1e-4:
            af_parts.append(f"volume={volume:.3f}")
        af_parts.extend(self._preview_fade_filters(clip_ref, start, duration, is_audio=True))
        if af_parts:
            command += ["-af", ",".join(af_parts)]
        command += [media.path]
        audio_process = subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.audio_process = audio_process

        def wait_audio(panel_ref=weakref.ref(self), ui_q=self.ui_queue, proc=audio_process, token=token):
            code = proc.wait()
            p = panel_ref()
            if p is not None and token == p.frame_token and code not in (0, None):
                ui_q.put(("audio_error", "No se pudo reproducir el audio con FFplay."))
            p = None
            ui_q.put(("play_done", token))
        threading.Thread(target=wait_audio, daemon=True).start()

    def stop_playback(self):
        self.frame_token += 1
        self._playback_from_timeline = False
        self._playback_track = None
        for process in (self.video_process, self.audio_process):
            if process and process.poll() is None:
                process.terminate()
        self.video_process = None
        self.audio_process = None
        self.playing = False
        self.playback_started_at = None
        self.play_btn.configure(text="▶")

    def _sync_selection_marks(self, clip=None):
        clip = clip or self.selected_clip
        if not clip:
            return
        self.mark_in = clip.source_in
        self.mark_out = clip.source_out
        self.cursor_time = max(clip.source_in, min(self.cursor_time, clip.source_out))

    def cut_selected_clip(self):
        clip = self.selected_clip
        if not clip or not self.selected_media or clip.media is not self.selected_media:
            self.status.configure(text="Selecciona un clip de la línea de tiempo y coloca el cursor donde quieras cortar.", fg=YELLOW)
            return
        timeline_cut = clip.timeline_start + (self.cursor_time - clip.source_in)
        if self.snap_enabled.get():
            inner_candidates = [
                pt for pt in self._snap_points_for(clip)
                if clip.timeline_start + MIN_CLIP_DURATION < pt < clip.timeline_end - MIN_CLIP_DURATION
            ]
            timeline_cut, _target = snap_point(timeline_cut, inner_candidates, snap_threshold(self.zoom))
        pieces = split_clip(clip, timeline_cut)
        if pieces is None:
            self.status.configure(text="La cuchilla debe estar dentro del clip, no en sus extremos.", fg=YELLOW)
            return
        self._push_undo()
        for linked in self._linked_clips(clip):
            linked_pieces = split_clip(linked, timeline_cut)
            linked_track = self._track_of(linked)
            if linked_pieces and linked_track:
                linked_pieces[1].link_group = (linked.link_group or "") + "-b"
                index = linked_track.clips.index(linked)
                linked_track.clips[index:index + 1] = list(linked_pieces)
        if clip.link_group:
            pieces[1].link_group = clip.link_group + "-b"
        for track in self.project.tracks:
            if clip in track.clips:
                index = track.clips.index(clip)
                track.clips[index:index + 1] = list(pieces)
                self.selected_clip = pieces[0]
                self.playhead_timeline = timeline_cut
                self._sync_selection_marks(self.selected_clip)
                self.project.selected_track_index = self.project.tracks.index(track)
                self._refresh_tracks()
                self._update_inspector()
                self.redraw_timeline()
                self.status.configure(text=f"Clip dividido en {format_editor_time(timeline_cut)}.", fg=GREEN)
                return

    def _replace_clip(self, track, old_clip, new_clip):
        index = track.clips.index(old_clip)
        track.clips[index] = new_clip
        self.selected_clip = new_clip
        self.drag_clip = new_clip

    def _request_waveform(self, path):
        if path in self.waveform_cache or path in self.waveform_pending:
            return
        if not os.path.isfile(path):
            self.waveform_cache[path] = []
            return
        self.waveform_pending.add(path)
        ui_q = self.ui_queue

        def worker(media_path=path, q=ui_q):
            try:
                samples = waveform_samples(media_path, 640)
            except Exception:
                samples = []
            q.put(("waveform", media_path, samples))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _ruler_step(zoom):
        for step in (0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if step * zoom >= 70:
                return step
        return 1200

    def redraw_timeline(self):
        self.timeline.delete("all")
        self.timeline_items.clear()
        layout = self._layout()
        duration = max(30.0, self.project.duration() + 5)
        visible_width = max(1, self.timeline.winfo_width())
        total_width = max(visible_width, LABEL_WIDTH + duration * self.zoom)
        total_height = max(190, (layout[-1][1] if layout else RULER_HEIGHT) + 8)

        # Regla de tiempo adaptativa al zoom.
        self.timeline.create_rectangle(0, 0, total_width, RULER_HEIGHT, fill="#111314", outline="")
        step = self._ruler_step(self.zoom)
        tick = 0.0
        while tick <= duration:
            x = self._x_at_time(tick)
            self.timeline.create_line(x, RULER_HEIGHT - 8, x, total_height, fill="#22272b")
            self.timeline.create_text(x + 3, 4, text=format_editor_time(tick), fill=MUTED, anchor="nw", font=("Segoe UI", 7))
            minor = step / 5
            for index in range(1, 5):
                mx = self._x_at_time(tick + minor * index)
                self.timeline.create_line(mx, RULER_HEIGHT - 4, mx, RULER_HEIGHT, fill="#3a4045")
            tick += step

        selected_index = self.project.selected_track_index
        for row, track in enumerate(self.project.tracks):
            y1, y2 = layout[row]
            inner_bottom = y2 - 3
            self.timeline.create_rectangle(LABEL_WIDTH, y1, total_width, inner_bottom, fill="#151719", outline="")
            label_bg = "#20262b" if row == selected_index else "#181b1d"
            self.timeline.create_rectangle(0, y1, LABEL_WIDTH - 2, inner_bottom, fill=label_bg, outline="")
            label = track.name + ("  OFF" if not track.enabled else "") + ("  M" if track.muted else "")
            self.timeline.create_text(8, (y1 + inner_bottom) / 2, text=label, fill=TEXT if track.enabled else MUTED, anchor="w", font=("Segoe UI", 8, "bold" if row == selected_index else "normal"))
            if self.magnetic_main.get() and track is self._main_track():
                self.timeline.create_text(LABEL_WIDTH - 8, y1 + 4, text="🧲", fill=YELLOW, anchor="ne", font=("Segoe UI", 7))
            for clip in track.clips:
                x1 = self._x_at_time(clip.timeline_start)
                x2 = x1 + max(14, clip.duration * self.zoom)
                muted_or_off = (not track.enabled) or track.muted
                if track.kind == "video":
                    color = "#2c3e4d" if muted_or_off else "#126da8"
                else:
                    color = "#2d4236" if muted_or_off else "#28784a"
                selected = clip is self.selected_clip
                outline = YELLOW if selected else "#78a9c8"
                item = self.timeline.create_rectangle(x1, y1 + 4, x2, inner_bottom - 3, fill=color, outline=outline, width=2 if selected else 1)
                self.timeline_items[item] = (track, clip)
                has_linked_audio = self._linked_audio_source(clip) is not None
                clip_audible = track.enabled and not track.muted and (clip.use_source_audio or has_linked_audio)
                if clip.media.has_audio:
                    samples = self.waveform_cache.get(clip.media.path)
                    if samples is None:
                        self._request_waveform(clip.media.path)
                    elif samples:
                        count = int(max(8, min(400, (x2 - x1) / 3)))
                        segment = waveform_segment(samples, clip.source_in, clip.source_out, clip.media.duration, count)
                        mid = (y1 + inner_bottom) / 2 + (6 if track.kind == "video" else 0)
                        height = max(4, (inner_bottom - y1 - 12) / 2) if track.kind == "audio" else max(3, (inner_bottom - y1 - 16) / 3)
                        wave_color = "#c3f3d0" if clip_audible else "#65756b"
                        for index, peak in enumerate(segment):
                            px = x1 + (index / max(1, len(segment) - 1)) * max(4, x2 - x1)
                            self.timeline.create_line(px, mid - peak * height, px, mid + peak * height, fill=wave_color, width=2)
                if clip.fade_in > 0:
                    fx_in = min(x2, x1 + clip.fade_in * self.zoom)
                    self.timeline.create_line(x1, inner_bottom - 3, fx_in, y1 + 4, fill="#ffd166", width=2)
                if clip.fade_out > 0:
                    fx_out = max(x1, x2 - clip.fade_out * self.zoom)
                    self.timeline.create_line(fx_out, y1 + 4, x2, inner_bottom - 3, fill="#ffd166", width=2)
                muted_badge = "🔇 " if (clip.media.has_audio and (track.muted or (not clip.use_source_audio and not has_linked_audio))) else ""
                name = muted_badge + ("🔗 " if clip.link_group else "") + clip.name
                text_color = "#b8c0c4" if muted_or_off else "#ffffff"
                self.timeline.create_text(x1 + 6, y1 + 7, text=name, fill=text_color, anchor="nw", font=("Segoe UI", 8))
                if selected:
                    left_handle = self.timeline.create_rectangle(x1, y1 + 2, x1 + 6, inner_bottom - 1, fill=YELLOW, outline="")
                    right_handle = self.timeline.create_rectangle(x2 - 6, y1 + 2, x2, inner_bottom - 1, fill=YELLOW, outline="")
                    self.timeline_items[left_handle] = (track, clip)
                    self.timeline_items[right_handle] = (track, clip)
            # Separador redimensionable entre pistas.
            active = row in (self.hover_boundary, self.resize_track_index)
            self.timeline.create_rectangle(0, y2 - 3, total_width, y2, fill=BLUE if active else "#2b3136", outline="")
            grip_x = LABEL_WIDTH / 2
            for offset in (-6, 0, 6):
                self.timeline.create_line(grip_x + offset - 2, y2 - 1.5, grip_x + offset + 2, y2 - 1.5, fill=TEXT if active else MUTED)

        playhead_x = self._x_at_time(self.playhead_timeline)
        self.timeline.create_line(playhead_x, 0, playhead_x, total_height, fill="#f2f4f5", width=2)
        self.timeline.create_polygon(playhead_x - 6, 0, playhead_x + 6, 0, playhead_x, 9, fill="#f2f4f5", outline="")
        if self.snap_target is not None:
            guide_x = self._x_at_time(self.snap_target)
            self.timeline.create_line(guide_x, 0, guide_x, total_height, fill="#ffcf4a", width=2, dash=(4, 3))
            text = self.snap_label or format_editor_time(self.snap_target)
            label_id = self.timeline.create_text(guide_x + 6, RULER_HEIGHT + 2, text=text, fill="#111111", anchor="nw", font=("Segoe UI", 8, "bold"))
            bbox = self.timeline.bbox(label_id)
            if bbox:
                background = self.timeline.create_rectangle(bbox[0] - 3, bbox[1] - 1, bbox[2] + 3, bbox[3] + 1, fill="#ffcf4a", outline="")
                self.timeline.tag_lower(background, label_id)
        self.timeline.configure(scrollregion=(0, 0, total_width, total_height))
        self._update_timecode()

    def _alt_pressed(self, event):
        return bool(getattr(event, "state", 0) & (0x0008 | 0x20000))

    def _snap_points_for(self, clip):
        exclude = [clip] + self._linked_clips(clip)
        return collect_snap_points(self.project.tracks, exclude=exclude, playhead=self.playhead_timeline)

    def _describe_snap(self, target):
        if target is None:
            return ""
        if abs(target - self.playhead_timeline) < 1e-6:
            return f"Cabezal · {format_editor_time(target)}"
        if abs(target) < 1e-6:
            return "Inicio · 00:00"
        return f"Clip · {format_editor_time(target)}"

    def _snap_start(self, track, clip, raw_start, alt=False):
        if not self.snap_enabled.get() or alt:
            self.snap_target = None
            return max(0.0, raw_start)
        snapped, target = snap_timeline_position(raw_start, clip.duration, self._snap_points_for(clip), snap_threshold(self.zoom))
        self.snap_target = target
        self.snap_label = self._describe_snap(target)
        return snapped

    def _snap_edge(self, clip, raw_edge, alt=False):
        if not self.snap_enabled.get() or alt:
            self.snap_target = None
            return max(0.0, raw_edge)
        snapped, target = snap_point(raw_edge, self._snap_points_for(clip), snap_threshold(self.zoom))
        self.snap_target = target
        self.snap_label = self._describe_snap(target)
        return snapped

    def _timeline_press(self, event):
        self.timeline.focus_set()
        if self.playing:
            self.stop_playback()
        if self.pending_library_media is not None and self._drop_library_media(event):
            return
        x = self.timeline.canvasx(event.x)
        y = self.timeline.canvasy(event.y)
        self.scrubbing = False
        boundary = track_boundary_at(self._layout(), y)
        if boundary is not None:
            self.resize_track_index = boundary
            track = self.project.tracks[boundary]
            self._resize_origin = (y, self._row_height(track))
            self._pending_snapshot = self._snapshot()
            self.drag_clip = None
            return
        hit = self._clip_at(x, y)
        if hit is None:
            self.scrubbing = True
            self.playhead_timeline = self._time_at_x(x)
            self._update_timecode()
            self.status.configure(text=f"Cabezal: {format_editor_time(self.playhead_timeline)} · haz clic en un clip o pulsa reproducir.", fg=TEXT)
            track = self._track_at_y(y)
            if track:
                self.project.selected_track_index = self.project.tracks.index(track)
                self._refresh_tracks()
            _t, active_clip = self._clip_at_playhead()
            if active_clip:
                self.selected_clip = active_clip
                self.selected_media = active_clip.media
                self.mark_in = active_clip.source_in
                self.mark_out = active_clip.source_out
                self.keep_audio.set(active_clip.use_source_audio)
                self.cursor_time = timeline_to_source_time(active_clip, self.playhead_timeline)
                if active_clip.media.has_video:
                    self.request_frame(self.cursor_time)
            else:
                self.selected_clip = None
            self._update_inspector()
            self.redraw_timeline()
            return
        track, clip = hit
        if self.tool_mode == "razor":
            self.selected_clip = clip
            self.selected_media = clip.media
            self.cursor_time = clamp_media_time(clip.source_in + (self._time_at_x(x) - clip.timeline_start), clip.media.duration)
            self.cut_selected_clip()
            if not self.temporary_razor:
                self.set_tool("select")
            return
        self.project.selected_track_index = self.project.tracks.index(track)
        self.selected_clip = clip
        self.selected_media = clip.media
        self.mark_in = clip.source_in
        self.mark_out = clip.source_out
        self.keep_audio.set(clip.use_source_audio)
        inside = clip.timeline_start <= self.playhead_timeline < clip.timeline_end
        offset = (self.playhead_timeline - clip.timeline_start) if inside else 0.0
        self.cursor_time = clamp_media_time(clip.source_in + offset, clip.media.duration)
        if clip.media.has_video:
            self.request_frame(self.cursor_time)
        self.resize_side = None
        x1 = self._x_at_time(clip.timeline_start)
        x2 = x1 + max(14, clip.duration * self.zoom)
        if abs(x - x1) <= 8:
            self.resize_side = "left"
        elif abs(x - x2) <= 8:
            self.resize_side = "right"
        self.drag_clip = clip
        self.drag_track = track
        self.drag_offset = x - x1
        self._pending_snapshot = self._snapshot()
        self.status.configure(text=f"Clip: {clip.name} · arrastra para mover, bordes para recortar (Alt = sin imán).", fg=TEXT)
        self._refresh_tracks()
        self._update_inspector()
        self.redraw_timeline()

    def _replace_linked(self, old_clip, new_clip):
        track = self._track_of(old_clip)
        if track:
            track.clips[track.clips.index(old_clip)] = new_clip

    def _trim_drag(self, track, x, alt):
        clip = self.drag_clip
        linked = self._linked_clips(clip)
        main_track = self._main_track()
        is_magnetic_main = self.magnetic_main.get() and (
            track is main_track or any(self._track_of(o) is main_track for o in linked)
        )
        if self.resize_side == "left":
            boundary = self._snap_edge(clip, self._time_at_x(x), alt)
            neighbors = [item.timeline_end for item in track.clips if item is not clip and item.timeline_end <= clip.timeline_start + 0.001]
            for other in linked:
                other_track = self._track_of(other)
                if other_track:
                    neighbors.extend(item.timeline_end for item in other_track.clips if item is not other and item.timeline_end <= other.timeline_start + 0.001)
            if neighbors:
                boundary = max(boundary, max(neighbors))
            updated = resize_clip_left(clip, boundary)
            self._replace_clip(track, clip, updated)
            for other in linked:
                self._replace_linked(other, resize_clip_left(other, updated.timeline_start))
        else:
            boundary = self._snap_edge(clip, self._time_at_x(x), alt)
            if not is_magnetic_main:
                neighbors = [item.timeline_start for item in track.clips if item is not clip and item.timeline_start >= clip.timeline_end - 0.001]
                for other in linked:
                    other_track = self._track_of(other)
                    if other_track:
                        neighbors.extend(item.timeline_start for item in other_track.clips if item is not other and item.timeline_start >= other.timeline_end - 0.001)
                if neighbors:
                    boundary = min(boundary, min(neighbors))
            updated = resize_clip_right(clip, boundary)
            self._replace_clip(track, clip, updated)
            for other in linked:
                self._replace_linked(other, resize_clip_right(other, updated.timeline_end))
            if is_magnetic_main:
                self._apply_magnetic()
        self._sync_selection_marks(self.drag_clip)
        if self.snap_target is not None:
            self.snap_label = f"{self.snap_label}  ·  duración {format_editor_time(self.drag_clip.duration)}"

    def _move_drag(self, track, x, y, alt):
        clip = self.drag_clip
        old_start = clip.timeline_start
        raw_start = self._time_at_x(x - self.drag_offset)
        new_start = self._snap_start(track, clip, raw_start, alt)
        target_track = track
        target_index = track_index_at(self._layout(), y)
        if target_index is not None:
            candidate = self.project.tracks[target_index]
            if candidate.kind == track.kind and candidate is not track:
                target_track = candidate
        linked = self._linked_clips(clip)
        main_track = self._main_track()
        free_move = self.magnetic_main.get() and (
            target_track is main_track
            or (target_track is track and any(self._track_of(o) is main_track for o in linked))
        )
        if not free_move:
            neighbor_ranges = [(other.timeline_start, other.timeline_end) for other in target_track.clips if other is not clip]
            constrained = avoid_clip_overlap(new_start, clip.duration, neighbor_ranges)
            if abs(constrained - new_start) > 1e-6:
                edges = {edge for pair in neighbor_ranges for edge in pair}
                touching = next((edge for edge in edges if abs(edge - constrained) < 1e-6 or abs(edge - (constrained + clip.duration)) < 1e-6), None)
                self.snap_target = touching
                self.snap_label = "Pegado al clip vecino" if touching is not None else ""
            new_start = constrained
        delta = new_start - old_start
        main_groups = {c.link_group for c in main_track.clips if c.link_group} if free_move and main_track else set()
        for other in linked:
            other_track = self._track_of(other)
            if not other_track:
                continue
            ranges = [
                (item.timeline_start, item.timeline_end)
                for item in other_track.clips
                if item is not other and item not in linked and not (free_move and item.link_group in main_groups)
            ]
            wanted = other.timeline_start + delta
            if wanted < -1e-6 or abs(avoid_clip_overlap(wanted, other.duration, ranges) - wanted) > 1e-6:
                self.status.configure(text="El audio vinculado chocaría con otro clip; desvincúlalo para moverlo por separado.", fg=YELLOW)
                return
        if target_track is not track:
            track.clips.remove(clip)
            target_track.clips.append(clip)
            self.drag_track = target_track
            self.project.selected_track_index = self.project.tracks.index(target_track)
        clip.timeline_start = new_start
        for other in linked:
            other.timeline_start = max(0.0, other.timeline_start + delta)

    def _timeline_drag(self, event):
        y = self.timeline.canvasy(event.y)
        if self.resize_track_index is not None:
            track = self.project.tracks[self.resize_track_index]
            origin_y, origin_height = getattr(self, "_resize_origin", (y, self._row_height(track)))
            self.track_heights[track.name] = clamp_track_height(origin_height + (y - origin_y))
            self.redraw_timeline()
            return
        if not self.drag_clip:
            if getattr(self, "scrubbing", False):
                self._seek_timeline(event)
            return
        x = self.timeline.canvasx(event.x)
        track = self._track_of(self.drag_clip)
        if not track:
            return
        alt = self._alt_pressed(event)
        if self.resize_side in ("left", "right"):
            self._trim_drag(track, x, alt)
        else:
            self._move_drag(track, x, y, alt)
        self._update_inspector()
        self.redraw_timeline()

    def _commit_pending_snapshot(self):
        snapshot, self._pending_snapshot = self._pending_snapshot, None
        if snapshot is not None and snapshot != self._snapshot():
            self._push_undo(snapshot)

    def _timeline_release(self, event):
        if self.pending_library_media is not None and self._drop_library_media(event):
            return
        self.scrubbing = False
        if self.resize_track_index is not None:
            self.resize_track_index = None
            self._commit_pending_snapshot()
            self.redraw_timeline()
            return
        if self.drag_clip:
            is_trim = self.resize_side in ("left", "right")
            for clip in [self.drag_clip] + self._linked_clips(self.drag_clip):
                clip.timeline_start = round(clip.timeline_start, 6)
                if is_trim:
                    clip.source_in = round(clip.source_in, 6)
                    clip.source_out = round(clip.source_out, 6)
            self._sync_selection_marks(self.drag_clip)
            self._apply_magnetic()
            self._commit_pending_snapshot()
            self.drag_clip = None
            self.drag_track = None
            self.snap_target = None
            self.snap_label = ""
            self.resize_side = None
            self._update_inspector()
            self.redraw_timeline()

    def toggle_clip_audio(self):
        clip = self.selected_clip
        if not clip or not clip.media.has_audio:
            return
        self._push_undo()
        linked_audio = [o for o in self._linked_clips(clip) if o.media.has_audio] if clip.media.has_video else []
        if linked_audio and not clip.use_source_audio:
            new_state = not linked_audio[0].use_source_audio
            for other in linked_audio:
                other.use_source_audio = new_state
        else:
            clip.use_source_audio = not clip.use_source_audio
            new_state = clip.use_source_audio
        self.keep_audio.set(clip.use_source_audio)
        state = "activado" if new_state else "quitado"
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text=f"Audio original del clip: {state}.", fg=YELLOW)

    def delete_clip(self):
        if self.playing:
            self.stop_playback()
        clip = self.selected_clip
        if not clip or not self._track_of(clip):
            self.status.configure(text="Selecciona un clip para eliminarlo (Supr).", fg=YELLOW)
            return
        self._push_undo()
        for target in [clip] + self._linked_clips(clip):
            track = self._track_of(target)
            if track:
                track.clips.remove(target)
        self.selected_clip = None
        self._apply_magnetic()
        self._refresh_preview_at_playhead()
        self._update_inspector()
        self.redraw_timeline()
        self.status.configure(text="Clip eliminado. Ctrl+Z para deshacer · Shift+Supr borra y cierra el hueco.", fg=GREEN)

    def _mode_key(self):
        return "selected" if self.export_mode.get().startswith("Solo") else "active"

    def _resolution_key(self):
        mapping = {"Auto": "auto", "1080p": "1080p", "720p": "720p", "9:16": "vertical"}
        return mapping.get(self.export_resolution.get(), "auto")

    def _ensure_playhead_visible(self):
        region = self.timeline.cget("scrollregion").split()
        if not region:
            return
        total = float(region[2]) or 1.0
        width = max(1, self.timeline.winfo_width())
        if total <= width:
            return
        left_frac, right_frac = self.timeline.xview()
        px = self._x_at_time(self.playhead_timeline)
        left_px = left_frac * total
        right_px = right_frac * total
        if px > right_px - 30 or px < left_px:
            target_left = max(0.0, px - width * 0.25)
            self.timeline.xview_moveto(min(1.0, target_left / total))

    def export(self):
        if self.exporting:
            self.status.configure(text="Ya hay una exportación en curso.", fg=YELLOW)
            return
        tracks = self.project.export_tracks(self._mode_key())
        if not any(track.clips for track in tracks):
            messagebox.showerror("No se puede exportar", "No hay clips activos para exportar.")
            return
        missing = [
            clip.media.path
            for track in tracks for clip in track.clips
            if not os.path.isfile(clip.media.path)
        ]
        if missing:
            messagebox.showerror(
                "Archivos no encontrados",
                "Faltan archivos necesarios para exportar:\n\n" + "\n".join(dict.fromkeys(missing[:6])),
            )
            return
        has_video = any(clip.media.has_video for track in tracks for clip in track.clips)
        has_audible = any(
            clip.media.has_audio and not track.muted and clip.use_source_audio
            for track in tracks for clip in track.clips
        )
        if not has_video and not has_audible:
            messagebox.showerror("No se puede exportar", "No hay pistas de vídeo ni de audio activas para exportar (revisa si están silenciadas).")
            return
        default = ".mp4" if has_video else ".mp3"
        path = filedialog.asksaveasfilename(
            title="Exportar montaje", defaultextension=default, initialfile="montaje" + default,
            filetypes=[("MP4", "*.mp4"), ("MOV", "*.mov"), ("MKV", "*.mkv"), ("WEBM", "*.webm"), ("MP3", "*.mp3"), ("WAV", "*.wav")],
        )
        if not path:
            return
        try:
            plan = build_export_plan(
                self.project, self._mode_key(), self.project.selected_track_index,
                path, resolution=self._resolution_key(),
            )
        except Exception as exc:
            messagebox.showerror("No se puede exportar", str(exc))
            return
        self.exporting = True
        self.export_btn.configure(state="disabled", text="Exportando…")
        self.export_duration = max(0.01, plan.get("duration", 0.0))
        self.status.configure(text="Exportando… 0%", fg=YELLOW)

        def worker(plan=plan, ui_q=self.ui_queue):
            try:
                output = run_export(plan, lambda line: ui_q.put(("progress", line)))
                ui_q.put(("export_done", output))
            except Exception as exc:
                ui_q.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        if getattr(self, "_poll_after_id", None):
            try:
                self.after_cancel(self._poll_after_id)
            except (tk.TclError, ValueError):
                pass
            self._poll_after_id = None
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "waveform":
                self.waveform_cache[event[1]] = event[2]
                self.waveform_pending.discard(event[1])
                self.redraw_timeline()
            elif kind == "frame" and event[1] == self.frame_token:
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
                    self._update_timecode()
                    self.redraw_timeline()
                    self._ensure_playhead_visible()
            elif kind == "play_done" and event[1] == self.frame_token:
                was_timeline = self._playback_from_timeline
                prev_clip = self.selected_clip
                self.playing = False
                self.playback_started_at = None
                self.play_btn.configure(text="▶")
                if was_timeline and prev_clip is not None:
                    next_pos = prev_clip.timeline_end + 0.001
                    _nt, next_clip = self._next_clip_after(next_pos)
                    if next_clip and next_clip is not prev_clip and next_clip.timeline_end > next_pos:
                        self.playhead_timeline = max(prev_clip.timeline_end, next_clip.timeline_start)
                        self.play_from_cursor()
                    else:
                        self._playback_from_timeline = False
            elif kind == "play_error":
                self.status.configure(text=f"Previsualización: {event[1]}", fg=RED)
            elif kind == "audio_error":
                self.status.configure(text=event[1], fg=RED)
            elif kind == "progress":
                secs = parse_ffmpeg_time(event[1])
                if secs is not None and self.export_duration > 0:
                    pct = min(99, int(round(secs / self.export_duration * 100)))
                    self.status.configure(text=f"Exportando montaje… {pct}%", fg=YELLOW)
                else:
                    self.status.configure(text="Exportando montaje…", fg=YELLOW)
            elif kind == "export_done":
                self.exporting = False
                self.export_btn.configure(state="normal", text="Exportar")
                self.status.configure(text=f"Exportado: {event[1]}", fg=GREEN)
                messagebox.showinfo("Exportación terminada", f"Archivo creado:\n\n{event[1]}")
            elif kind == "error":
                self.exporting = False
                self.export_btn.configure(state="normal", text="Exportar")
                self.status.configure(text="Error de exportación.", fg=RED)
                messagebox.showerror("Error", event[1])
        if self.playing and self.playback_started_at is not None:
            elapsed = time.monotonic() - self.playback_started_at
            self.cursor_time = min(self.playback_end_time, self.playback_start_time + elapsed)
            clip = self.selected_clip
            if clip and clip.media is self.selected_media:
                self.playhead_timeline = clip.timeline_start + self.cursor_time - clip.source_in
                self._update_timecode()
                self.redraw_timeline()
                self._ensure_playhead_visible()
        if self.winfo_exists():
            self._poll_after_id = self.after(60, self._poll_queue)

