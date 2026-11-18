"""Pruebas de interfaz del editor con Tk real (ratón y teclado simulados).

Se saltan automáticamente si no hay pantalla disponible. En Linux se pueden
ejecutar con:  xvfb-run -a python -m unittest test_editor_panel_gui
"""

import os
import shutil
import subprocess
import tempfile
import unittest

try:
    import tkinter as tk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    HAS_DISPLAY = True
except Exception:  # pragma: no cover - depende del entorno
    HAS_DISPLAY = False

if HAS_DISPLAY:
    from media_editor import Clip, MediaInfo, Track, build_export_plan, probe_media, run_export
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
        self.root.destroy()

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


if __name__ == "__main__":
    unittest.main()
