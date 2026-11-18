"""Pruebas de interfaz del editor con Tk real (ratón y teclado simulados).

Se saltan automáticamente si no hay pantalla disponible. En Linux se pueden
ejecutar con:  xvfb-run -a python -m unittest test_editor_panel_gui
"""

import gc
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    import tkinter as tk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    HAS_DISPLAY = True
except Exception:  # pragma: no cover - depende del entorno
    HAS_DISPLAY = False

if HAS_DISPLAY:
    from media_editor import Clip, MediaInfo, Track, audio_media_from_video, build_export_plan, probe_media, run_export
    from simple_editor_panel import LABEL_WIDTH, SimpleEditorPanel

BUTTON1 = 0x0100
ALT = 0x0008


def fake_media(duration=20.0, path="fake.mp4", video=True):
    return MediaInfo(path=path, duration=duration, has_video=video, has_audio=False)


@unittest.skipUnless(HAS_DISPLAY, "Necesita una pantalla (usa xvfb-run en Linux)")
class EditorPanelGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("1500x950+0+0")
        self.panel = SimpleEditorPanel(self.root)
        self.panel.pack(fill="both", expand=True)
        self.pump()
        self.panel.zoom = 24.0
        self.video, self.audio = self.panel.project.tracks
        self.canvas = self.panel.timeline

    def tearDown(self):
        self.panel.destroy()
        self.panel = None
        self.video = None
        self.audio = None
        self.canvas = None
        gc.collect()
        self.root.destroy()
        self.root = None

    # -- utilidades --------------------------------------------------------
    def pump(self):
        for _ in range(3):
            self.root.update_idletasks()
            self.root.update()

    def x_at(self, seconds):
        return int(round(LABEL_WIDTH + seconds * self.panel.zoom))

    def row(self, track):
        index = self.panel.project.tracks.index(track)
        return self.panel._layout()[index]

    def y_mid(self, track):
        y1, y2 = self.row(track)
        return int((y1 + y2) / 2)

    def drag(self, x1, y1, x2, y2, state=0, steps=6):
        self.canvas.event_generate("<ButtonPress-1>", x=x1, y=y1, state=state)
        self.pump()
        for step in range(1, steps + 1):
            x = int(x1 + (x2 - x1) * step / steps)
            y = int(y1 + (y2 - y1) * step / steps)
            self.canvas.event_generate("<Motion>", x=x, y=y, state=BUTTON1 | state)
            self.pump()
        self.canvas.event_generate("<ButtonRelease-1>", x=x2, y=y2, state=BUTTON1 | state)
        self.pump()

    def add(self, track, start, length, media=None):
        clip = Clip(media or fake_media(), source_in=0, source_out=length, timeline_start=start)
        track.clips.append(clip)
        self.panel.redraw_timeline()
        self.pump()
        return clip

    # -- redimensionar pistas ---------------------------------------------
    def test_dragging_track_separator_resizes_track(self):
        _y1, y2 = self.row(self.video)
        before = y2 - _y1
        self.drag(60, int(y2 - 1), 60, int(y2 - 1 + 40))
        self.assertAlmostEqual(self.panel._row_height(self.video), before + 40, delta=2)
        # La pista siguiente empieza justo donde termina la redimensionada (sin solapes).
        self.assertEqual(self.row(self.audio)[0], self.row(self.video)[1])
        self.panel.undo()
        self.assertAlmostEqual(self.panel._row_height(self.video), before, delta=0.01)

    def test_hovering_separator_shows_resize_cursor(self):
        _y1, y2 = self.row(self.video)
        self.canvas.event_generate("<Motion>", x=300, y=int(y2 - 1))
        self.pump()
        self.assertEqual(str(self.canvas.cget("cursor")), "sb_v_double_arrow")
        self.assertEqual(self.panel.hover_boundary, 0)

    def test_ctrl_wheel_changes_track_height(self):
        before = self.panel._row_height(self.audio)
        self.canvas.event_generate("<MouseWheel>", x=400, y=self.y_mid(self.audio), delta=120, state=0x0004)
        self.pump()
        self.assertGreater(self.panel._row_height(self.audio), before)

    def test_size_presets(self):
        self.panel.set_all_track_heights("L")
        self.assertEqual(self.panel._row_height(self.video), 110.0)
        self.assertEqual(self.panel._row_height(self.audio), 110.0)

    # -- imán al mover -----------------------------------------------------
    def test_moving_clip_snaps_to_previous_clip_end(self):
        self.add(self.video, 0, 4)
        moving = self.add(self.video, 10, 3)
        y = self.y_mid(self.video)
        grab = self.x_at(11)  # agarramos por el centro del clip
        target_start = 4.3    # 7 px de distancia: dentro del radio de 12 px
        self.drag(grab, y, self.x_at(target_start + 1), y)
        self.assertAlmostEqual(moving.timeline_start, 4.0, places=3)

    def test_alt_disables_snap(self):
        self.add(self.video, 0, 4)
        moving = self.add(self.video, 10, 3)
        y = self.y_mid(self.video)
        self.drag(self.x_at(11), y, self.x_at(5.3 + 1), y, state=ALT)
        self.assertAlmostEqual(moving.timeline_start, 5.3, delta=0.05)

    def test_moving_clip_snaps_to_playhead(self):
        self.panel.playhead_timeline = 8.0
        moving = self.add(self.video, 1, 2)
        y = self.y_mid(self.video)
        self.drag(self.x_at(2), y, self.x_at(8.25 + 1), y)
        self.assertAlmostEqual(moving.timeline_start, 8.0, places=3)

    def test_moving_clip_snaps_to_clip_in_other_track(self):
        self.add(self.audio, 6, 5, fake_media(path="song.mp3", video=False))
        moving = self.add(self.video, 0, 2)
        y = self.y_mid(self.video)
        self.drag(self.x_at(1), y, self.x_at(5.8 + 1), y)
        self.assertAlmostEqual(moving.timeline_start, 6.0, places=3)

    def test_clip_does_not_overlap_neighbour(self):
        self.add(self.video, 0, 4)
        moving = self.add(self.video, 10, 3)
        y = self.y_mid(self.video)
        self.drag(self.x_at(11), y, self.x_at(2 + 1), y)
        self.assertGreaterEqual(moving.timeline_start, 4.0 - 1e-6)

    def test_snap_guide_is_drawn_while_dragging(self):
        self.add(self.video, 0, 4)
        self.add(self.video, 10, 3)
        y = self.y_mid(self.video)
        self.canvas.event_generate("<ButtonPress-1>", x=self.x_at(11), y=y)
        self.pump()
        self.canvas.event_generate("<Motion>", x=self.x_at(5.2), y=y, state=BUTTON1)
        self.pump()
        self.assertEqual(self.panel.snap_target, 4.0)
        self.assertTrue(self.panel.snap_label)
        self.canvas.event_generate("<ButtonRelease-1>", x=self.x_at(5.2), y=y, state=BUTTON1)
        self.pump()
        self.assertIsNone(self.panel.snap_target)

    # -- imán al recortar --------------------------------------------------
    def test_trimming_right_edge_snaps_to_playhead(self):
        self.panel.playhead_timeline = 6.0
        clip = self.add(self.video, 0, 4)
        self.panel.selected_clip = clip
        self.panel.redraw_timeline()
        y = self.y_mid(self.video)
        self.drag(self.x_at(4) - 2, y, self.x_at(5.8), y)
        self.assertAlmostEqual(clip.timeline_start, 0.0)
        self.assertAlmostEqual(self.panel.selected_clip.timeline_end, 6.0, places=3)

    def test_trimming_left_edge_snaps_to_clip_in_other_track(self):
        self.add(self.audio, 2, 6, fake_media(path="song.mp3", video=False))
        clip = Clip(fake_media(), source_in=5, source_out=15, timeline_start=5)
        self.video.clips.append(clip)
        self.panel.selected_clip = clip
        self.panel.redraw_timeline()
        self.pump()
        y = self.y_mid(self.video)
        self.drag(self.x_at(5) + 2, y, self.x_at(2.3), y)
        trimmed = self.panel.selected_clip
        self.assertAlmostEqual(trimmed.timeline_start, 2.0, places=3)
        self.assertAlmostEqual(trimmed.source_in, 2.0, places=3)
        self.assertAlmostEqual(trimmed.timeline_end, 15.0, places=3)

    # -- edición magnética -------------------------------------------------
    def test_ripple_delete_and_close_gap(self):
        first = self.add(self.video, 0, 2)
        middle = self.add(self.video, 2, 3)
        last = self.add(self.video, 8, 2)
        self.panel.selected_clip = middle
        self.panel.ripple_delete_clip()
        self.assertNotIn(middle, self.video.clips)
        self.assertAlmostEqual(last.timeline_start, 5.0)
        self.panel.close_gap_at(self.video, 3.0)
        self.assertAlmostEqual(last.timeline_start, first.timeline_end)

    def test_magnetic_main_track_keeps_clips_together(self):
        a = self.add(self.video, 0, 3)
        b = self.add(self.video, 3, 2)
        self.panel.magnetic_main.set(True)
        self.panel._magnetic_toggled()
        y = self.y_mid(self.video)
        # Arrastrar el primero detrás del segundo lo reordena sin dejar huecos.
        self.drag(self.x_at(1.5), y, self.x_at(9), y)
        self.assertAlmostEqual(b.timeline_start, 0.0)
        self.assertAlmostEqual(a.timeline_start, 2.0)

    def test_split_at_playhead(self):
        clip = self.add(self.video, 0, 6)
        self.panel.selected_clip = clip
        self.panel.playhead_timeline = 2.5
        self.panel.split_at_playhead()
        self.assertEqual(len(self.video.clips), 2)
        self.assertEqual(sorted(c.timeline_end for c in self.video.clips), [2.5, 6.0])

    def test_undo_redo_after_move(self):
        moving = self.add(self.video, 10, 3)
        y = self.y_mid(self.video)
        self.drag(self.x_at(11), y, self.x_at(16), y)
        moved_to = self.video.clips[0].timeline_start
        self.assertNotAlmostEqual(moved_to, 10.0)
        self.panel.undo()
        self.assertAlmostEqual(self.panel.project.tracks[0].clips[0].timeline_start, 10.0)
        self.panel.redo()
        self.assertAlmostEqual(self.panel.project.tracks[0].clips[0].timeline_start, moved_to)
        self.assertIsNotNone(moving)

    def test_click_without_change_does_not_pollute_undo(self):
        self.add(self.video, 0, 3)
        self.panel.undo_stack.clear()
        y = self.y_mid(self.video)
        self.drag(self.x_at(1), y, self.x_at(1), y, steps=1)
        self.assertEqual(self.panel.undo_stack, [])

    # -- atajos ------------------------------------------------------------
    def test_shortcuts_are_ignored_while_typing(self):
        entry = tk.Entry(self.root)
        entry.place(x=0, y=0)
        self.pump()
        entry.focus_force()
        self.pump()
        entry.event_generate("<KeyPress-c>")
        self.pump()
        self.assertEqual(self.panel.tool_mode, "select")
        self.canvas.focus_force()
        self.pump()
        self.canvas.event_generate("<KeyPress-c>")
        self.pump()
        self.assertEqual(self.panel.tool_mode, "razor")

    def test_delete_key_and_ctrl_z(self):
        clip = self.add(self.video, 0, 3)
        self.panel.selected_clip = clip
        self.canvas.focus_force()
        self.pump()
        self.canvas.event_generate("<Delete>")
        self.pump()
        self.assertEqual(self.video.clips, [])
        self.canvas.event_generate("<Control-z>")
        self.pump()
        self.assertEqual(len(self.panel.project.tracks[0].clips), 1)

    def test_track_names_stay_unique(self):
        self.panel.add_track("video")            # Vídeo 2
        self.panel.project.selected_track_index = 0
        self.panel.delete_track()                # borra Vídeo 1
        self.panel.add_track("video")
        names = [track.name for track in self.panel.project.tracks]
        self.assertEqual(len(names), len(set(names)))

    def test_playhead_prioritizes_video_over_audio_track(self):
        vclip = self.add(self.video, 0, 5, fake_media(path="v.mp4", video=True))
        self.add(self.audio, 0, 5, fake_media(path="a.mp3", video=False))
        self.panel.playhead_timeline = 2.0
        track, clip = self.panel._clip_at_playhead()
        self.assertIs(track, self.video)
        self.assertIs(clip, vclip)

    def test_separate_audio_disables_source_audio_and_is_undoable(self):
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 0, 5, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()
        self.assertFalse(vclip.use_source_audio)
        self.assertEqual(len(self.audio.clips), 1)
        self.assertTrue(vclip.link_group)
        self.panel.undo()
        self.assertTrue(self.panel.project.tracks[0].clips[0].use_source_audio)
        self.assertEqual(self.panel.project.tracks[1].clips, [])

    def test_copy_paste_and_duplicate_clip(self):
        vclip = self.add(self.video, 0, 3)
        self.panel.selected_clip = vclip
        self.panel.duplicate_clip()
        self.assertEqual(len(self.video.clips), 2)
        self.assertAlmostEqual(self.video.clips[1].timeline_start, 3.0)
        self.panel.copy_clip()
        self.panel.playhead_timeline = 8.0
        self.panel.paste_clip()
        self.assertEqual(len(self.video.clips), 3)
        self.assertAlmostEqual(self.video.clips[2].timeline_start, 8.0)

    def test_close_gap_moves_linked_clips_together(self):
        v = self.add(self.video, 5.0, 3.0)
        a = self.add(self.audio, 5.0, 3.0, fake_media(path="a.mp3", video=False))
        v.link_group = "g1"
        a.link_group = "g1"
        self.panel.close_gap_at(self.video, 1.0)
        self.assertAlmostEqual(v.timeline_start, 0.0)
        self.assertAlmostEqual(a.timeline_start, 0.0)

    def test_separate_audio_without_audio_track_creates_single_undo_step(self):
        self.panel.project.tracks = [self.video]
        self.panel.undo_stack.clear()
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 0, 5, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()
        self.assertEqual(len(self.panel.undo_stack), 1)
        self.assertIsNotNone(self.panel._linked_audio_source(vclip))

    def test_continuous_playback_does_not_rewind_to_long_background_audio(self):
        v1 = self.add(self.video, 0.0, 4.0, fake_media(path="v1.mp4", video=True))
        self.add(self.audio, 0.0, 20.0, fake_media(path="bgm.mp3", video=False))
        self.panel.selected_clip = v1
        self.panel._playback_from_timeline = True
        self.panel.frame_token = 42
        called = []
        self.panel.play_from_cursor = lambda: called.append(self.panel.playhead_timeline)
        self.panel.ui_queue.put(("play_done", 42))
        self.panel._poll_queue()
        self.assertEqual(len(called), 1)
        self.assertGreaterEqual(called[0], 4.0)

    def test_step_frames_works_with_clip_at_playhead(self):
        self.add(self.video, 0.0, 5.0, fake_media(path="v.mp4", video=True))
        self.panel.playhead_timeline = 1.0
        self.panel._step_frames(1)
        self.assertGreater(self.panel.playhead_timeline, 1.0)

    def test_magnetic_main_allows_expanding_clip_right_and_pushes_next(self):
        c1 = self.add(self.video, 0.0, 3.0, Clip_media := fake_media(path="c1.mp4", duration=10.0, video=True))
        c1.source_out = 3.0
        c2 = self.add(self.video, 3.0, 2.0, Clip_media)
        self.panel.magnetic_main.set(True)
        self.panel.drag_clip = c1
        self.panel.resize_side = "right"
        self.panel._trim_drag(self.video, self.x_at(5.0), alt=True)
        self.assertAlmostEqual(self.video.clips[0].duration, 5.0)
        self.assertAlmostEqual(c2.timeline_start, 5.0)

    def test_separate_audio_again_after_unlink_relinks_without_overlap(self):
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 0.0, 5.0, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()  # separa
        self.panel.toggle_linked_media()  # desvincula
        self.assertIsNone(vclip.link_group)
        self.panel.toggle_linked_media()  # re-vincula sin duplicar
        self.assertEqual(len(self.audio.clips), 1)
        self.assertIsNotNone(vclip.link_group)

    def test_duplicate_separated_video_restores_source_audio_on_copy(self):
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 0.0, 5.0, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()
        self.assertFalse(vclip.use_source_audio)
        self.panel.duplicate_clip()
        self.assertEqual(len(self.video.clips), 2)
        self.assertTrue(self.video.clips[1].use_source_audio)

    def test_split_at_playhead_without_selection_splits_linked_audio(self):
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 0.0, 6.0, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()
        self.panel.selected_clip = None
        self.panel.project.selected_track_index = 0
        self.panel.playhead_timeline = 3.0
        self.panel.split_at_playhead()
        self.assertEqual(len(self.video.clips), 2)
        self.assertEqual(len(self.audio.clips), 2)

    def test_magnetic_main_left_trim_does_not_collapse_clip(self):
        c1 = self.add(self.video, 0.0, 6.0, fake_media(path="c1.mp4", duration=10.0, video=True))
        self.panel.magnetic_main.set(True)
        self.panel.drag_clip = c1
        self.panel.resize_side = "left"
        self.panel._trim_drag(self.video, self.x_at(2.0), alt=True)
        self.assertAlmostEqual(self.video.clips[0].duration, 4.0)
        self.panel._trim_drag(self.video, self.x_at(2.0), alt=True)
        self.assertAlmostEqual(self.video.clips[0].duration, 4.0)

    def test_delete_audio_track_restores_orphaned_video_audio_and_unlinks(self):
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 0.0, 5.0, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()
        self.panel.project.selected_track_index = 1
        self.panel.delete_track()
        self.assertIsNone(vclip.link_group)
        self.assertTrue(vclip.use_source_audio)

    def test_magnetic_compact_moves_linked_audio_to_free_track_on_collision(self):
        media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=25.0)
        vclip = self.add(self.video, 5.0, 3.0, media)
        self.panel.selected_clip = vclip
        self.panel.toggle_linked_media()
        self.add(self.audio, 0.0, 3.0, fake_media(path="bgm.mp3", video=False))
        self.panel.magnetic_main.set(True)
        self.panel._apply_magnetic()
        self.assertAlmostEqual(vclip.timeline_start, 0.0)
        audio_tracks_with_linked = [t for t in self.panel.project.tracks if t.kind == "audio" and any(c.link_group == vclip.link_group for c in t.clips)]
        self.assertEqual(len(audio_tracks_with_linked), 1)
        self.assertIsNot(audio_tracks_with_linked[0], self.audio)


@unittest.skipUnless(HAS_DISPLAY and shutil.which("ffmpeg") and shutil.which("ffprobe"), "Necesita pantalla y FFmpeg")
class EditorEndToEndTests(unittest.TestCase):
    """Flujo completo con archivos reales: soltar desde la biblioteca, imán y exportar."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.video_path = os.path.join(cls.tmp, "prueba.mp4")
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", cls.video_path,
        ], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_drop_snap_and_export(self):
        root = tk.Tk()
        root.geometry("1500x950+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        for _ in range(3):
            root.update()
        try:
            media = probe_media(self.video_path)
            video = panel.project.tracks[0]
            y1, y2 = panel._layout()[0]
            # Primer clip en 0, segundo soltado "a ojo" 6 px después del final del primero.
            for drop_seconds in (0.1, 4.25):
                panel.pending_library_media = media
                event = type("Drop", (), {"x": int(LABEL_WIDTH + drop_seconds * panel.zoom), "y": int((y1 + y2) / 2)})()
                panel._drop_library_media(event)
            starts = sorted(clip.timeline_start for clip in video.clips)
            self.assertEqual(starts, [0.0, 4.0])
            # La forma de onda se calcula en segundo plano y llega por la cola de la interfaz.
            for _ in range(100):
                root.update()
                if media.path in panel.waveform_cache:
                    break
                root.after(30)
            self.assertIn(media.path, panel.waveform_cache)
            output = os.path.join(self.tmp, "montaje.mp4")
            plan = build_export_plan(panel.project, "active", None, output)
            run_export(plan)
            self.assertAlmostEqual(probe_media(output).duration, 8.0, delta=0.3)
        finally:
            panel.destroy()
            root.destroy()

    def test_ripple_delete_keeps_subsequent_linked_audio_synced(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track = panel.project.tracks[0]
            a_track = panel.project.tracks[1]
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            a_media = audio_media_from_video(v_media)
            c1 = Clip(v_media, source_out=3.0, timeline_start=0.0, name="c1")
            c2 = Clip(v_media, source_out=4.0, timeline_start=3.0, use_source_audio=False, link_group="g2", name="c2")
            c2_a = Clip(a_media, source_out=4.0, timeline_start=3.0, link_group="g2", name="a2")
            music = Clip(MediaInfo(path="m.mp3", duration=5.0, has_video=False, has_audio=True), source_out=5.0, timeline_start=8.0, name="m")
            v_track.clips = [c1, c2]
            a_track.clips = [c2_a, music]
            panel.selected_clip = c1
            panel.ripple_delete_clip()
            self.assertAlmostEqual(c2.timeline_start, 0.0, places=5)
            self.assertAlmostEqual(c2_a.timeline_start, 0.0, places=5)
            self.assertAlmostEqual(music.timeline_start, 8.0, places=5)
        finally:
            panel.destroy()
            root.destroy()

    def test_toggle_clip_audio_on_separated_video_toggles_linked_audio(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track = panel.project.tracks[0]
            a_track = panel.project.tracks[1]
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            a_media = audio_media_from_video(v_media)
            v_clip = Clip(v_media, source_out=4.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            a_clip = Clip(a_media, source_out=4.0, timeline_start=0.0, link_group="g1")
            v_track.clips = [v_clip]
            a_track.clips = [a_clip]
            panel.selected_clip = v_clip
            panel.toggle_clip_audio()
            self.assertFalse(v_clip.use_source_audio)
            self.assertFalse(a_clip.use_source_audio)
            panel.toggle_clip_audio()
            self.assertFalse(v_clip.use_source_audio)
            self.assertTrue(a_clip.use_source_audio)
        finally:
            panel.destroy()
            root.destroy()

    def test_next_clip_after_skips_gaps_and_release_preserves_precision(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track = panel.project.tracks[0]
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            c1 = Clip(v_media, source_in=0.123456, source_out=2.123456, timeline_start=0.0)
            c2 = Clip(v_media, source_out=2.0, timeline_start=5.0)
            v_track.clips = [c1, c2]
            _t, found = panel._next_clip_after(2.5)
            self.assertIs(found, c2)
            panel.drag_clip = c1
            panel.resize_side = None
            panel._timeline_release(type("E", (), {})())
            self.assertAlmostEqual(c1.source_in, 0.123456, places=6)
        finally:
            panel.destroy()
            root.destroy()

    def test_export_rejects_empty_timeline_before_file_dialog(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            with mock.patch("simple_editor_panel.filedialog.asksaveasfilename") as ask_mock, \
                 mock.patch("simple_editor_panel.messagebox.showerror") as err_mock:
                panel.export()
            ask_mock.assert_not_called()
            err_mock.assert_called_once()
        finally:
            panel.destroy()
            root.destroy()

    def test_trim_and_split_sync_mark_in_out(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track = panel.project.tracks[0]
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            clip = Clip(v_media, source_in=0.0, source_out=8.0, timeline_start=0.0)
            v_track.clips = [clip]
            panel.selected_clip = clip
            panel.selected_media = v_media
            panel.drag_clip = clip
            panel.resize_side = "right"
            panel._trim_drag(v_track, panel._x_at_time(6.0), alt=True)
            self.assertAlmostEqual(panel.mark_out, 6.0, places=3)
            panel.drag_clip = None
            panel.playhead_timeline = 2.5
            panel.split_at_playhead()
            self.assertAlmostEqual(panel.mark_in, 0.0, places=3)
            self.assertAlmostEqual(panel.mark_out, 2.5, places=3)
        finally:
            panel.destroy()
            root.destroy()

    def test_multi_file_drop_onto_timeline_inserts_all_compatible_clips(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            m1 = MediaInfo(path="v1.mp4", duration=3.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            m2 = MediaInfo(path="v2.mp4", duration=4.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            y1, y2 = panel._layout()[0]
            ev = type("Drop", (), {"x": int(LABEL_WIDTH + 10), "y": int((y1 + y2) / 2)})()
            with mock.patch("simple_editor_panel.os.path.isfile", return_value=True), \
                 mock.patch.object(panel, "_add_or_get_media", side_effect=[m1, m2]):
                panel._ingest_dropped_paths(["v1.mp4", "v2.mp4"], panel.timeline, ev)
            self.assertEqual(len(panel.project.tracks[0].clips), 2)
            self.assertAlmostEqual(panel.project.tracks[0].clips[1].timeline_start, panel.project.tracks[0].clips[0].timeline_end, places=5)
        finally:
            panel.destroy()
            root.destroy()

    def test_export_rejects_muted_audio_only_before_file_dialog(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            a_media = MediaInfo(path="a.mp3", duration=5.0, has_video=False, has_audio=True)
            panel.project.tracks[1].clips.append(Clip(a_media, source_out=5.0, use_source_audio=False))
            with mock.patch("simple_editor_panel.filedialog.asksaveasfilename") as ask_mock, \
                 mock.patch("simple_editor_panel.messagebox.showerror") as err_mock:
                panel.export()
            ask_mock.assert_not_called()
            err_mock.assert_called_once()
        finally:
            panel.destroy()
            root.destroy()

    def test_restore_clamps_playhead_and_shortcut_guards_modifiers(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            panel.project.tracks[0].clips.append(Clip(v_media, source_out=4.0, timeline_start=0.0))
            panel.playhead_timeline = 15.0
            panel._restore(panel._snapshot())
            self.assertAlmostEqual(panel.playhead_timeline, 4.0, places=5)
            called = []
            ev_ctrl = type("E", (), {"state": 0x0004})()
            self.assertIsNone(panel._shortcut(ev_ctrl, lambda: called.append(1), no_modifiers=True))
            self.assertEqual(called, [])
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_unlink_from_audio_clip_and_clipboard_preserves_muted_linked_audio(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            v_clip = Clip(v_media, source_out=4.0, timeline_start=0.0)
            panel.project.tracks[0].clips.append(v_clip)
            panel.selected_clip = v_clip
            panel.toggle_linked_media()
            a_clip = panel.project.tracks[1].clips[0]
            panel.toggle_clip_audio()  # mutes linked audio
            cloned = panel._clone_for_clipboard(v_clip)
            self.assertFalse(cloned.use_source_audio)
            panel.selected_clip = a_clip
            panel.toggle_linked_media()  # unlink from audio side
            self.assertIsNone(v_clip.link_group)
            self.assertIsNone(a_clip.link_group)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_magnetic_main_allows_reordering_clips_with_linked_audio(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track, a_track = panel.project.tracks
            v_media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            a_media = audio_media_from_video(v_media)
            c1 = Clip(v_media, source_out=3.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            c2 = Clip(v_media, source_out=3.0, timeline_start=3.0, use_source_audio=False, link_group="g2")
            a1 = Clip(a_media, source_out=3.0, timeline_start=0.0, link_group="g1")
            a2 = Clip(a_media, source_out=3.0, timeline_start=3.0, link_group="g2")
            v_track.clips = [c1, c2]
            a_track.clips = [a1, a2]
            panel.magnetic_main.set(True)
            panel.drag_clip = c1
            panel.drag_offset = 0.0
            panel._move_drag(v_track, panel._x_at_time(4.0), panel._layout()[0][0] + 10, alt=True)
            self.assertGreater(c1.timeline_start, 2.5)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_razor_cut_ignores_outer_snap_updates_playhead_and_inspector(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track = panel.project.tracks[0]
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            clip = Clip(v_media, source_in=0.0, source_out=4.0, timeline_start=0.0, fade_in=1.5)
            v_track.clips = [clip]
            panel.selected_clip = clip
            panel.selected_media = v_media
            panel.zoom = 10.0  # snap_threshold grande (~1.2s) que antes enganchaba 0.25s al borde 0.0
            panel.snap_enabled.set(True)
            panel.cursor_time = 0.25
            panel.cut_selected_clip()
            self.assertEqual(len(v_track.clips), 2)
            self.assertAlmostEqual(panel.playhead_timeline, 0.25, places=5)
            self.assertAlmostEqual(float(panel.fade_in_var.get()), 0.25, places=2)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_linked_audio_trim_and_move_respect_magnetic_main(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track, a_track = panel.project.tracks
            v_media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            a_media = audio_media_from_video(v_media)
            c1 = Clip(v_media, source_out=3.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            c2 = Clip(v_media, source_out=3.0, timeline_start=3.0, use_source_audio=False, link_group="g2")
            a1 = Clip(a_media, source_out=3.0, timeline_start=0.0, link_group="g1")
            a2 = Clip(a_media, source_out=3.0, timeline_start=3.0, link_group="g2")
            v_track.clips = [c1, c2]
            a_track.clips = [a1, a2]
            panel.magnetic_main.set(True)
            panel.drag_clip = a1
            panel.resize_side = "right"
            panel._trim_drag(a_track, panel._x_at_time(5.0), alt=True)
            self.assertAlmostEqual(a_track.clips[0].timeline_end, 5.0, places=3)
            self.assertAlmostEqual(v_track.clips[1].timeline_start, 5.0, places=3)
            panel.resize_side = None
            panel.drag_clip = a_track.clips[0]
            panel.drag_offset = 0.0
            panel._move_drag(a_track, panel._x_at_time(6.0), panel._layout()[1][0] + 10, alt=True)
            self.assertGreater(v_track.clips[0].timeline_start, 4.5)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_toggle_play_rejects_missing_file_and_redraw_handles_muted_fades(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_media = MediaInfo(path="/no/existe/video.mp4", duration=5.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            clip = Clip(v_media, source_out=5.0, timeline_start=0.0, use_source_audio=False, fade_in=0.5, fade_out=0.5)
            panel.project.tracks[0].clips.append(clip)
            panel.project.tracks[0].muted = True
            panel.selected_clip = clip
            panel.selected_media = v_media
            panel.redraw_timeline()
            panel.toggle_play()
            self.assertFalse(panel.playing)
            self.assertIn("no encontrado", panel.status.cget("text").lower())
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_clone_clipboard_preserves_linked_audio_props_and_trim_to_playhead(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            a_media = audio_media_from_video(v_media)
            v_clip = Clip(v_media, source_out=6.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            a_clip = Clip(a_media, source_out=6.0, timeline_start=0.0, link_group="g1", volume=1.5, pitch_semitones=3, fade_in=0.4)
            panel.project.tracks[0].clips.append(v_clip)
            panel.project.tracks[1].clips.append(a_clip)
            cloned = panel._clone_for_clipboard(v_clip)
            self.assertAlmostEqual(cloned.volume, 1.5, places=3)
            self.assertEqual(cloned.pitch_semitones, 3)
            self.assertAlmostEqual(cloned.fade_in, 0.4, places=3)

            panel.selected_clip = v_clip
            panel.playhead_timeline = 1.5
            panel.trim_to_playhead("left")
            self.assertAlmostEqual(panel.project.tracks[0].clips[0].timeline_start, 1.5, places=3)
            self.assertAlmostEqual(panel.project.tracks[1].clips[0].timeline_start, 1.5, places=3)
            panel.playhead_timeline = 4.0
            panel.trim_to_playhead("right")
            self.assertAlmostEqual(panel.project.tracks[0].clips[0].timeline_end, 4.0, places=3)
            self.assertAlmostEqual(panel.project.tracks[1].clips[0].timeline_end, 4.0, places=3)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_library_insert_remove_and_export_missing_file_or_vertical_resolution(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_media = MediaInfo(path="/no/existe/v.mp4", duration=4.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
            panel.media_library.append(v_media)
            panel.media_list.insert("end", "v.mp4")
            panel.media_list.selection_set(0)
            panel._insert_selected_media()
            self.assertEqual(len(panel.project.tracks[0].clips), 1)
            panel.remove_selected_library_media()
            self.assertEqual(len(panel.media_library), 1)  # En uso en el timeline

            with mock.patch("simple_editor_panel.filedialog.asksaveasfilename") as ask_mock, \
                 mock.patch("simple_editor_panel.messagebox.showerror") as err_mock:
                panel.export()
            ask_mock.assert_not_called()
            err_mock.assert_called_once()

            from media_editor import build_export_plan
            panel.project.tracks[1].clips.append(Clip(audio_media_from_video(v_media), source_out=2.0, timeline_start=0.0))
            with mock.patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")):
                plan = build_export_plan(panel.project, "active", 0, "/tmp/out.mp4", resolution="vertical")
            self.assertEqual((plan["width"], plan["height"]), (1080, 1920))
            self.assertIn("level=disabled", plan["filter_complex"])
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_apply_audio_props_clamps_fades_delete_clears_preview_and_auto_max_res_fps(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            low = MediaInfo(path="low.mp4", duration=4.0, has_video=True, has_audio=False, width=640, height=360, fps=24.0)
            high = MediaInfo(path="high.mp4", duration=4.0, has_video=True, has_audio=False, width=1920, height=1080, fps=60.0)
            c1 = Clip(low, source_out=4.0, timeline_start=0.0)
            c2 = Clip(high, source_out=4.0, timeline_start=4.0)
            panel.project.tracks[0].clips = [c1, c2]
            panel.selected_clip = c1
            panel.fade_in_var.set(3.0)
            panel.fade_out_var.set(3.0)
            panel._apply_audio_props()
            self.assertAlmostEqual(c1.fade_in, 2.0, places=3)
            self.assertAlmostEqual(c1.fade_out, 2.0, places=3)
            self.assertAlmostEqual(float(panel.fade_in_var.get()), 2.0, places=3)

            from media_editor import build_export_plan
            with mock.patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")):
                plan = build_export_plan(panel.project, "active", 0, "/tmp/out.mp4", resolution="auto")
            self.assertEqual((plan["width"], plan["height"], plan["fps"]), (1920, 1080, 60.0))

            panel.project.tracks[0].clips = [c1]
            panel.selected_clip = c1
            panel.delete_clip()
            self.assertIn("Abre un vídeo", panel.preview.cget("text"))
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_track_header_click_preserves_playhead_and_move_playhead_scrolls(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            panel.playhead_timeline = 4.25
            y1, y2 = panel._layout()[1]
            panel._timeline_press(type("E", (), {"x": 20, "y": int((y1 + y2) / 2)})())
            self.assertEqual(panel.project.selected_track_index, 1)
            self.assertAlmostEqual(panel.playhead_timeline, 4.25, places=5)
            self.assertFalse(panel.scrubbing)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_seek_gap_clears_selection_and_keep_audio_syncs_linked_state(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            c1 = Clip(v_media, source_out=2.0, timeline_start=0.0)
            c2 = Clip(v_media, source_out=2.0, timeline_start=5.0)
            panel.project.tracks[0].clips = [c1, c2]
            panel.selected_clip = c1
            panel.toggle_linked_media()
            panel.toggle_clip_audio()
            self.assertFalse(panel.keep_audio.get())
            panel.toggle_clip_audio()
            self.assertTrue(panel.keep_audio.get())

            panel._move_playhead(3.5)
            self.assertIsNone(panel.selected_clip)
            self.assertEqual(panel.clip_title_var.get(), "Ningún clip seleccionado")
            self.assertEqual(panel.preview.cget("text"), "")
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_magnetic_trim_respects_independent_audio_neighbor_and_play_done_cleans_up(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            v_track, a_track = panel.project.tracks
            v_media = MediaInfo(path="av.mp4", duration=10.0, has_video=True, has_audio=True, width=320, height=180, fps=30.0)
            a_media = audio_media_from_video(v_media)
            music = MediaInfo(path="music.mp3", duration=10.0, has_video=False, has_audio=True)
            v1 = Clip(v_media, source_out=3.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            a1 = Clip(a_media, source_out=3.0, timeline_start=0.0, link_group="g1")
            indep = Clip(music, source_out=2.0, timeline_start=4.0)
            v_track.clips = [v1]
            a_track.clips = [a1, indep]
            panel.magnetic_main.set(True)
            panel.drag_clip = v1
            panel.resize_side = "right"
            panel._trim_drag(v_track, panel._x_at_time(6.0), alt=True)
            self.assertLessEqual(a_track.clips[0].timeline_end, indep.timeline_start + 1e-6)
            self.assertAlmostEqual(v_track.clips[0].timeline_end, a_track.clips[0].timeline_end, places=5)

            proc = mock.MagicMock()
            proc.poll.return_value = None
            panel.audio_process = proc
            panel._playback_from_timeline = True
            panel.selected_clip = None
            panel.frame_token = 7
            panel.ui_queue.put(("play_done", 7))
            panel._poll_queue()
            proc.terminate.assert_called_once()
            self.assertFalse(panel._playback_from_timeline)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_long_timeline_at_max_zoom_bounds_ruler_canvas_items(self):
        root = tk.Tk()
        root.withdraw()
        panel = SimpleEditorPanel(root)
        try:
            long_audio = MediaInfo(path="podcast.mp3", duration=7200.0, has_video=False, has_audio=True)
            panel.project.tracks[1].clips.append(Clip(long_audio, source_out=7200.0, timeline_start=0.0))
            panel.zoom = 400.0
            panel.redraw_timeline()
            self.assertLess(len(panel.timeline.find_withtag("ruler")), 250)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_paste_syncs_marks_load_resets_state_and_ctrl_shift_z_bindings(self):
        root = tk.Tk()
        root.geometry("900x700+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            a_media = MediaInfo(path="song.mp3", duration=8.0, has_video=False, has_audio=True)
            clip = Clip(a_media, source_in=1.0, source_out=4.0, timeline_start=0.0, use_source_audio=False)
            panel.clipboard_clip = clip
            panel.mark_in, panel.mark_out = 0.0, 99.0
            panel.playhead_timeline = 2.0
            panel.paste_clip()
            self.assertAlmostEqual(panel.mark_in, 1.0, places=3)
            self.assertAlmostEqual(panel.mark_out, 4.0, places=3)
            self.assertFalse(panel.keep_audio.get())

            missing_media = MediaInfo(path="/no/existe/clip.mp4", duration=5.0, has_video=True, has_audio=False, width=320, height=180, fps=30.0)
            from media_editor import EditorProject, Track, save_project
            with tempfile.TemporaryDirectory() as tmp:
                proj_path = tmp + "/test.avep"
                save_project(proj_path, EditorProject([Track("V1", "video")]), media_library=[missing_media])
                panel.playhead_timeline = 12.0
                with mock.patch("simple_editor_panel.filedialog.askopenfilename", return_value=proj_path), \
                     mock.patch("simple_editor_panel.messagebox.showwarning"):
                    panel.load_project_dialog()
            self.assertAlmostEqual(panel.playhead_timeline, 0.0, places=5)
            panel.media_list.selection_set(0)
            panel._insert_selected_media()
            self.assertEqual(panel.project.tracks[0].clips, [])

            # Bloq Mayus + Ctrl+Z (state=0x0006) debe llamar a undo y <Control-Shift-Z> a redo
            v_clip = Clip(MediaInfo(path="v.mp4", duration=4.0, has_video=True, has_audio=False), source_out=4.0)
            panel._push_undo()
            panel.project.tracks[0].clips.append(v_clip)
            panel.timeline.focus_force()
            root.update_idletasks()
            root.update()
            panel.timeline.event_generate("<KeyPress-Z>", state=0x0006)
            root.update()
            self.assertEqual(panel.project.tracks[0].clips, [])
            panel.timeline.event_generate("<Control-Shift-Z>")
            root.update()
            self.assertEqual(len(panel.project.tracks[0].clips), 1)

            # Si cambia frame_token justo al arrancar ffmpeg, v_proc se termina y no se registra como activo
            with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp_vid:
                panel.selected_media = MediaInfo(path=tmp_vid.name, duration=4.0, has_video=True, has_audio=False)
                panel.mark_in, panel.mark_out = 0.0, 4.0
                panel.cursor_time = 0.0
                fake_proc = mock.MagicMock()
                fake_proc.poll.return_value = None
                fake_proc.stdout = mock.MagicMock()
                fake_proc.stderr = mock.MagicMock()

                def bump_token(*_args, **_kwargs):
                    panel.frame_token += 1
                    return fake_proc

                with mock.patch("simple_editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                     mock.patch("simple_editor_panel.subprocess.Popen", side_effect=bump_token):
                    panel.toggle_play()
                    import time
                    deadline = time.monotonic() + 2.0
                    while not fake_proc.terminate.called and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertTrue(fake_proc.terminate.called)
                self.assertIsNone(panel.video_process)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_repeated_left_splits_avoid_link_group_collision_and_spinbox_commits_on_click(self):
        root = tk.Tk()
        root.geometry("1200x800+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            v_media = MediaInfo(path="v.mp4", duration=12.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
            a_media = audio_media_from_video(v_media)
            v0 = Clip(v_media, source_in=0.0, source_out=10.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            a0 = Clip(a_media, source_in=0.0, source_out=10.0, timeline_start=0.0, link_group="g1")
            panel.project.tracks[0].clips = [v0]
            panel.project.tracks[1].clips = [a0]

            panel.playhead_timeline = 5.0
            panel.selected_clip = panel.project.tracks[0].clips[0]
            panel.split_at_playhead()
            # Volver a cortar la mitad izquierda en 2.5s
            panel.playhead_timeline = 2.5
            panel.selected_clip = panel.project.tracks[0].clips[0]
            panel.split_at_playhead()

            v_clips = panel.project.tracks[0].clips
            a_clips = panel.project.tracks[1].clips
            self.assertEqual(len(v_clips), 3)
            self.assertEqual(len(a_clips), 3)
            v_groups = [c.link_group for c in v_clips]
            a_groups = [c.link_group for c in a_clips]
            self.assertEqual(len(set(v_groups)), 3)
            self.assertEqual(v_groups, a_groups)

            # Editar el spinbox de volumen del clip 1 y hacer clic directo en el clip 2
            panel.selected_clip = v_clips[0]
            panel._update_inspector()
            panel.redraw_timeline()
            root.update()
            panel.volume_spin.focus_force()
            root.update()
            panel.volume_spin.delete(0, "end")
            panel.volume_spin.insert(0, "150")
            x_second = panel._x_at_time(v_clips[1].timeline_start + 1.0)
            y_first_track = panel._layout()[0][0] + 15
            panel.timeline.event_generate("<ButtonPress-1>", x=int(x_second), y=int(y_first_track))
            root.update()
            self.assertAlmostEqual(v_clips[0].volume, 1.5, places=2)
            self.assertAlmostEqual(a_clips[0].volume, 1.5, places=2)
            self.assertAlmostEqual(v_clips[1].volume, 1.0, places=2)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_muted_linked_audio_does_not_leak_source_and_audio_popen_error_resets_playing(self):
        root = tk.Tk()
        root.geometry("900x700+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp_vid, \
                 tempfile.NamedTemporaryFile(suffix=".mp3") as tmp_aud:
                v_media = MediaInfo(path=tmp_vid.name, duration=5.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
                a_media = audio_media_from_video(v_media)
                v_clip = Clip(v_media, source_out=5.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
                a_clip = Clip(a_media, source_out=5.0, timeline_start=0.0, link_group="g1")
                panel.project.tracks[0].clips = [v_clip]
                panel.project.tracks[1].clips = [a_clip]
                panel.project.tracks[1].muted = True
                panel.selected_clip = v_clip
                panel.selected_media = v_media
                panel.keep_audio.set(panel._effective_clip_audio(v_clip))
                self.assertTrue(panel.keep_audio.get())

                spawned_cmds = []
                fake_proc = mock.MagicMock()
                fake_proc.poll.return_value = 0
                fake_proc.wait.return_value = 0
                fake_proc.stdout = mock.MagicMock()
                fake_proc.stderr = mock.MagicMock()

                def fake_popen(cmd, **_kwargs):
                    spawned_cmds.append(cmd)
                    return fake_proc

                with mock.patch("simple_editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                     mock.patch("simple_editor_panel.subprocess.Popen", side_effect=fake_popen):
                    panel.toggle_play()
                    import time
                    deadline = time.monotonic() + 2.0
                    while not spawned_cmds and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertEqual(len(spawned_cmds), 1)
                self.assertEqual(spawned_cmds[0][0], "ffmpeg")
                panel.stop_playback()

                # Si _play_audio_only falla al lanzar ffplay, playing vuelve a False
                a_only = MediaInfo(path=tmp_aud.name, duration=5.0, has_video=False, has_audio=True)
                with mock.patch("simple_editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                     mock.patch("simple_editor_panel.subprocess.Popen", side_effect=OSError("missing ffplay")):
                    panel._play_audio_only(a_only, 0.0, 3.0)
                self.assertFalse(panel.playing)
                self.assertEqual(panel.play_btn.cget("text"), "▶")
        finally:
            panel.destroy()
            self.assertTrue(panel._destroyed)
            self.assertEqual(panel._shortcut_handlers, {})
            panel = None
            gc.collect()
            root.destroy()

    def test_trim_to_playhead_syncs_linked_bounds_and_poll_queue_skips_destroyed(self):
        root = tk.Tk()
        root.geometry("900x700+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
            a_media = audio_media_from_video(v_media)
            v_clip = Clip(v_media, source_in=0.0, source_out=6.0, timeline_start=0.0, use_source_audio=False, link_group="g1")
            # Audio ligeramente más corto al final (5.5s): no puede recortarse por la izquierda más allá de 5.49s
            a_clip = Clip(a_media, source_in=0.0, source_out=5.5, timeline_start=0.0, link_group="g1")
            panel.project.tracks[0].clips = [v_clip]
            panel.project.tracks[1].clips = [a_clip]
            panel.selected_clip = v_clip
            panel.playhead_timeline = 5.8
            panel.trim_to_playhead("left")

            v_trimmed = panel.project.tracks[0].clips[0]
            a_trimmed = panel.project.tracks[1].clips[0]
            self.assertAlmostEqual(v_trimmed.timeline_start, a_trimmed.timeline_start, places=3)
            self.assertAlmostEqual(v_trimmed.timeline_start, 5.49, places=2)

            panel.ui_queue.put(("waveform", "v.mp4", [0.1, 0.2]))
        finally:
            panel.destroy()
            panel._poll_queue()
            panel = None
            gc.collect()
            root.destroy()

    def test_empty_click_clears_preview_duplicate_commits_spinbox_and_vproc_cleans_on_ffplay_error(self):
        root = tk.Tk()
        root.geometry("1200x800+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
            v_clip = Clip(v_media, source_out=3.0, timeline_start=0.0)
            panel.project.tracks[0].clips = [v_clip]
            panel.selected_clip = v_clip
            panel.selected_media = v_media
            panel.preview_photo = object()
            panel._update_inspector()
            panel.redraw_timeline()
            root.update()

            # Clic en un hueco vacío (t = 8.0s) limpia el fotograma anterior del visor
            x_gap = panel._x_at_time(8.0)
            y_first = panel._layout()[0][0] + 15
            panel.timeline.event_generate("<ButtonPress-1>", x=int(x_gap), y=int(y_first))
            root.update()
            self.assertIsNone(panel.preview_photo)

            # Editar volume_spin y duplicar directamente confirma el volumen antes de clonar
            panel.selected_clip = v_clip
            panel.selected_media = v_media
            panel._update_inspector()
            root.update()
            panel.volume_spin.focus_force()
            root.update()
            panel.volume_spin.delete(0, "end")
            panel.volume_spin.insert(0, "140")
            panel.duplicate_clip()
            self.assertEqual(len(panel.project.tracks[0].clips), 2)
            self.assertAlmostEqual(panel.project.tracks[0].clips[0].volume, 1.4, places=2)
            self.assertAlmostEqual(panel.project.tracks[0].clips[1].volume, 1.4, places=2)

            # Si ffplay falla tras lanzar ffmpeg en toggle_play, v_proc se termina en finally
            with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp_vid:
                vid = MediaInfo(path=tmp_vid.name, duration=4.0, has_video=True, has_audio=True)
                panel.selected_clip = None
                panel.selected_media = vid
                panel.mark_in, panel.mark_out = 0.0, 4.0
                panel.cursor_time = 0.0
                fake_vproc = mock.MagicMock()
                fake_vproc.poll.return_value = None
                fake_vproc.stdout = mock.MagicMock()
                fake_vproc.stderr = mock.MagicMock()
                calls = {"n": 0}

                def popen_side_effect(*_args, **_kwargs):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return fake_vproc
                    raise OSError("missing ffplay")

                with mock.patch("simple_editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                     mock.patch("simple_editor_panel.subprocess.Popen", side_effect=popen_side_effect):
                    panel.toggle_play()
                    import time
                    deadline = time.monotonic() + 2.0
                    while not fake_vproc.terminate.called and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertTrue(fake_vproc.terminate.called)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_short_clip_right_handle_wheel_zero_delta_link_uniqueness_and_track_selection(self):
        root = tk.Tk()
        root.geometry("1200x800+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            v_media = MediaInfo(path="v.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
            a_media = audio_media_from_video(v_media)

            # 1. En un clip corto (14px), pulsar cerca del borde derecho selecciona resize_side == "right"
            short = Clip(v_media, source_out=0.2, timeline_start=0.0)
            panel.project.tracks[0].clips = [short]
            panel.redraw_timeline()
            root.update()
            x1 = panel._x_at_time(0.0)
            y_row0 = panel._layout()[0][0] + 15
            panel.timeline.event_generate("<ButtonPress-1>", x=int(x1 + 10), y=int(y_row0))
            root.update()
            self.assertEqual(panel.resize_side, "right")
            panel.drag_clip = None
            panel.resize_side = None

            # 2. _timeline_wheel con delta == 0 no cambia el zoom ni la posición de scroll
            zoom_before = panel.zoom
            fake_evt = type("E", (), {"delta": 0, "state": 0x0008, "x": 200, "y": 40})()
            self.assertEqual(panel._timeline_wheel(fake_evt), "break")
            self.assertEqual(panel.zoom, zoom_before)

            # 3. _new_link_group evita colisiones con link-1 o link-{id(clip)}
            v2 = Clip(v_media, source_out=4.0, timeline_start=2.0, use_source_audio=True)
            other = Clip(a_media, source_out=1.0, timeline_start=8.0, link_group="link-" + str(id(v2)))
            extra = Clip(a_media, source_out=1.0, timeline_start=9.0, link_group="link-1")
            panel.project.tracks[0].clips = [v2]
            panel.project.tracks[1].clips = [other, extra]
            panel.selected_clip = v2
            panel.toggle_linked_media()
            self.assertIsNotNone(v2.link_group)
            self.assertNotIn(v2.link_group, {other.link_group, extra.link_group})

            # 4. _shift_linked_clip creando pista auxiliar no roba selected_track_index
            panel.project.selected_track_index = 0
            blocker = Clip(a_media, source_out=2.0, timeline_start=5.0)
            linked = Clip(a_media, source_out=2.0, timeline_start=0.0, link_group="g-shift")
            panel.project.tracks[1].clips = [blocker, linked]
            panel._shift_linked_clip(linked, 5.5)
            self.assertEqual(panel.project.selected_track_index, 0)
            self.assertGreaterEqual(len(panel.project.tracks), 3)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_corrupt_frames_keep_poll_alive_audio_click_clears_preview_and_cross_track_refresh(self):
        root = tk.Tk()
        root.geometry("1200x800+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            a_media = MediaInfo(path="song.mp3", duration=8.0, has_video=False, has_audio=True)
            v2 = panel.add_track("video")  # Vídeo 2

            # 1. Al insertar o hacer clic en un clip de solo audio se limpia preview_photo anterior
            panel.preview_photo = object()
            panel.media_library = [a_media]
            panel.media_list.insert("end", "♫ song.mp3")
            panel.media_list.selection_set(0)
            panel._insert_selected_media()
            self.assertIsNone(panel.preview_photo)
            self.assertIn("song.mp3", panel.preview.cget("text"))

            panel.preview_photo = object()
            panel.redraw_timeline()
            root.update()
            a_row = panel._layout()[1][0] + 15
            ax = panel._x_at_time(1.0)
            panel.timeline.event_generate("<ButtonPress-1>", x=int(ax), y=int(a_row))
            root.update()
            self.assertIsNone(panel.preview_photo)
            panel.drag_clip = None

            # 2. Al soltar un clip movido a otra pista se actualiza track_name en la UI
            v_media = MediaInfo(path="clip.mp4", duration=6.0, has_video=True, has_audio=False, width=640, height=360, fps=30.0)
            vclip = Clip(v_media, source_out=3.0, timeline_start=0.0)
            panel.project.tracks[0].clips = [vclip]
            panel.redraw_timeline()
            root.update()
            panel.drag_clip = vclip
            panel.drag_track = panel.project.tracks[0]
            panel.drag_offset = 5.0
            v2_y = panel._layout()[2][0] + 15
            panel._move_drag(panel.project.tracks[0], panel._x_at_time(1.0), v2_y, alt=True)
            panel._timeline_release(type("E", (), {"x": 200, "y": int(v2_y)})())
            self.assertIn(vclip, v2.clips)
            self.assertEqual(panel.track_name.get(), v2.name)

            # 3. _restore limpia drag_track, resize_side y _pending_snapshot
            panel.drag_track = v2
            panel.resize_side = "left"
            panel._pending_snapshot = ("dummy",)
            panel._restore(panel._snapshot())
            self.assertIsNone(panel.drag_track)
            self.assertIsNone(panel.resize_side)
            self.assertIsNone(panel._pending_snapshot)

            # 4. _poll_queue resiste fotogramas corruptos o incompletos y reagenda el temporizador
            panel.frame_token = 9
            panel.export_duration = 10.0
            panel.ui_queue.put(("frame", 9, b"not-a-png"))
            panel.ui_queue.put(("raw_frame", 9, b"short", 1.0))
            panel.ui_queue.put(("progress", "time=00:00:05.00"))
            if panel._poll_after_id:
                panel.after_cancel(panel._poll_after_id)
                panel._poll_after_id = None
            panel._poll_queue()
            self.assertIsNotNone(panel._poll_after_id)
            self.assertIn("50%", panel.status.cget("text"))
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_fallback_editor_panel_fixes(self):
        from editor_panel import EditorPanel

        root = tk.Tk()
        root.geometry("1100x700+0+0")
        panel = EditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            # 1. Eliminar todas las pistas mantiene selected_track_index >= 0
            while panel.project.tracks:
                panel.delete_track()
            self.assertEqual(panel.project.selected_track_index, 0)

            # 2. split_clip conserva tono/volumen, recorta fades y no repite sufijos "A A"
            track = panel.add_track("video", "Vídeo 1")
            media = MediaInfo(path="demo.mp4", duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
            clip = Clip(
                media, source_in=0.0, source_out=6.0, timeline_start=0.0,
                pitch_semitones=-3, volume=1.4, fade_in=5.0, fade_out=5.0,
            )
            track.clips.append(clip)
            panel.selected_clip = clip
            panel.refresh()
            with mock.patch("editor_panel.simpledialog.askstring", return_value="2.0"):
                panel.split_clip()
            self.assertEqual(len(track.clips), 2)
            left, right = track.clips
            self.assertEqual(left.pitch_semitones, -3)
            self.assertEqual(right.pitch_semitones, -3)
            self.assertAlmostEqual(left.volume, 1.4)
            self.assertAlmostEqual(right.volume, 1.4)
            self.assertAlmostEqual(left.fade_in, 2.0)
            self.assertAlmostEqual(right.fade_out, 4.0)
            self.assertEqual(left.name, "demo · 1")

            # Volver a dividir el primer tramo no acumula "A A"
            panel.selected_clip = left
            with mock.patch("editor_panel.simpledialog.askstring", return_value="1.0"):
                panel.split_clip()
            self.assertEqual(track.clips[0].name, "demo · 1")
            self.assertEqual(track.clips[1].name, "demo · 2")

            # 3. Hacer clic sobre la etiqueta de texto del clip lo selecciona en el timeline
            panel.selected_clip = None
            panel.redraw_timeline()
            root.update()
            text_items = [
                item_id for item_id in panel.timeline.find_withtag("clip_text")
                if panel.timeline_items.get(item_id, (None, None))[1] is track.clips[0]
            ]
            self.assertTrue(text_items)
            tx1, ty1, _tx2, ty2 = panel.timeline.bbox(text_items[0])
            panel.timeline.event_generate("<ButtonPress-1>", x=int(tx1 + 4), y=int((ty1 + ty2) / 2))
            root.update()
            self.assertIs(panel.selected_clip, track.clips[0])
            panel.drag_clip = None

            # 4. _start_export bloquea exportaciones concurrentes y destroy cancela el timer
            panel.exporting = True
            panel._start_export("/tmp/out.mp4", preview=False)
            self.assertIn("en curso", panel.status.cget("text"))
            self.assertIsNotNone(panel._poll_after_id)
        finally:
            panel.destroy()
            self.assertIsNone(panel._poll_after_id)
            panel = None
            gc.collect()
            root.destroy()

    def test_audio_props_clamp_library_cleanup_capture_and_short_playback(self):
        root = tk.Tk()
        root.geometry("1200x800+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                a_path = os.path.join(tmp, "short.mp3")
                v_path = os.path.join(tmp, "vid.mp4")
                open(a_path, "wb").close()
                open(v_path, "wb").close()
                a_media = MediaInfo(path=a_path, duration=5.0, has_video=False, has_audio=True)
                v_media = MediaInfo(path=v_path, duration=5.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
                vclip = Clip(v_media, source_in=0.0, source_out=3.0, timeline_start=0.0)
                panel.project.tracks[0].clips = [vclip]
                panel.selected_clip = vclip
                panel.selected_media = v_media

                # 1. _apply_audio_props sincroniza spinbox aunque exceda límites o sea NaN/texto inválido
                panel.volume_var.set(350)
                panel.fade_in_var.set(9.0)
                panel.fade_out_var.set(0.0)
                panel._apply_audio_props()
                self.assertEqual(panel.volume_var.get(), 200)
                self.assertAlmostEqual(vclip.volume, 2.0)
                self.assertAlmostEqual(panel.fade_in_var.get(), 3.0)

                panel.volume_var.set(999)
                panel._apply_audio_props()
                self.assertEqual(panel.volume_var.get(), 200)

                panel.fade_in_var.set(float("nan"))
                panel._apply_audio_props()
                self.assertAlmostEqual(panel.fade_in_var.get(), 3.0)

                # 2. load_project_dialog bloqueado durante exportación activa
                panel.exporting = True
                with mock.patch("simple_editor_panel.filedialog.askopenfilename") as ask_open:
                    panel.load_project_dialog()
                    ask_open.assert_not_called()
                panel.exporting = False

                # 3. delete_track ajusta playhead_timeline al nuevo máximo del proyecto
                panel.playhead_timeline = 2.8
                panel.project.selected_track_index = 0
                panel.delete_track()
                self.assertEqual(panel.playhead_timeline, 0.0)

                # 4. remove_selected_library_media limpia preview_photo y detiene reproducción
                panel.media_library = [a_media]
                panel.media_list.insert("end", "♫ short.mp3")
                panel.media_list.selection_set(0)
                panel.selected_media = a_media
                panel.preview_photo = object()
                panel.playing = True
                panel.remove_selected_library_media()
                self.assertFalse(panel.playing)
                self.assertIsNone(panel.preview_photo)

                # 5. capture_frame falla si ffmpeg termina sin crear imagen no vacía
                panel.selected_media = v_media
                panel.cursor_time = 5.0
                out_png = os.path.join(tmp, "empty.png")
                with mock.patch("simple_editor_panel.filedialog.asksaveasfilename", return_value=out_png), \
                     mock.patch("simple_editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                     mock.patch("simple_editor_panel.subprocess.run", return_value=mock.Mock(returncode=0)), \
                     mock.patch("simple_editor_panel.messagebox.showerror") as show_err:
                    panel.capture_frame()
                    show_err.assert_called_once()

                # 6. _play_audio_only usa MIN_CLIP_DURATION en clips menores a 0.1 s
                short_clip = Clip(a_media, source_in=1.0, source_out=1.04, timeline_start=0.0)
                panel.selected_clip = short_clip
                panel.cursor_time = 1.0
                panel.mark_in = 1.0
                panel.mark_out = 1.04
                fake_proc = mock.Mock()
                fake_proc.poll.return_value = None
                fake_proc.wait.return_value = 0
                with mock.patch("simple_editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                     mock.patch("simple_editor_panel.subprocess.Popen", return_value=fake_proc) as popen_mock:
                    panel._play_audio_only(a_media, 1.0, 1.04)
                    cmd = popen_mock.call_args.args[0]
                    t_idx = cmd.index("-t") + 1
                    self.assertEqual(cmd[t_idx], "0.040")
                    panel.stop_playback()
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_trim_to_playhead_magnetic_sync_and_missing_preview_clears_stale_frame(self):
        root = tk.Tk()
        root.geometry("1200x800+0+0")
        panel = SimpleEditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                v_path = os.path.join(tmp, "clip.mp4")
                open(v_path, "wb").close()
                v_media = MediaInfo(path=v_path, duration=10.0, has_video=True, has_audio=True, width=640, height=360, fps=30.0)
                clip = Clip(v_media, source_in=0.0, source_out=10.0, timeline_start=0.0)
                panel.project.tracks[0].clips = [clip]
                panel.magnetic_main.set(True)
                panel.selected_clip = clip
                panel.selected_media = v_media
                panel.playhead_timeline = 4.0

                # 1. trim_to_playhead("left") con pista magnética compacta a 0.0 y sincroniza cabezal y cursor
                panel.trim_to_playhead("left")
                trimmed = panel.selected_clip
                self.assertAlmostEqual(trimmed.timeline_start, 0.0)
                self.assertAlmostEqual(trimmed.source_in, 4.0)
                self.assertAlmostEqual(panel.playhead_timeline, 0.0)
                self.assertAlmostEqual(panel.cursor_time, 4.0)

                # 2. Al desaparecer el archivo de vídeo en disco, seleccionar/buscar limpia preview_photo
                os.remove(v_path)
                panel.preview_photo = object()
                panel._seek_at_time(1.0)
                self.assertIsNone(panel.preview_photo)
                self.assertIn("no encontrado", panel.preview.cget("text"))
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()

    def test_fallback_editor_audio_preview_and_property_validation(self):
        from editor_panel import EditorPanel

        root = tk.Tk()
        panel = EditorPanel(root)
        panel.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            track = panel.add_track("audio", "Audio 1")
            audio = MediaInfo(path="song.mp3", duration=10.0, has_video=False, has_audio=True)
            clip = Clip(audio, source_in=0.0, source_out=5.0, timeline_start=0.0, fade_in=2.0, fade_out=2.0)
            track.clips.append(clip)
            panel.selected_clip = clip
            panel.refresh()
            panel._load_clip_properties()

            # 1. preview() en proyecto solo audio usa extensión de audio (.mp3) y pasa build_export_plan
            captured = {}
            def fake_start(output, preview):
                captured["output"] = output
                captured["preview"] = preview
                from media_editor import build_export_plan
                captured["plan"] = build_export_plan(panel.project, panel._mode_key(), panel.project.selected_track_index, output, preview=preview)
            with mock.patch("editor_panel.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
                 mock.patch.object(panel, "_start_export", side_effect=fake_start):
                panel.preview()
            self.assertTrue(captured["output"].endswith("previsualizacion.mp3"))
            self.assertFalse(captured["plan"]["has_video"])

            # 2. apply_clip_properties rechaza duraciones < 0.01 s y ajusta fades al acortar
            panel.in_var.set("1.0")
            panel.out_var.set("1.005")
            with mock.patch("editor_panel.messagebox.showerror") as show_err:
                panel.apply_clip_properties()
                show_err.assert_called_once()
            self.assertAlmostEqual(clip.source_out, 5.0)

            panel.in_var.set("0.0")
            panel.out_var.set("1.0")
            panel.apply_clip_properties()
            self.assertAlmostEqual(clip.duration, 1.0)
            self.assertLessEqual(clip.fade_in + clip.fade_out, 1.0 + 1e-6)
        finally:
            panel.destroy()
            panel = None
            gc.collect()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
