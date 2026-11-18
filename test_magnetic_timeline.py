"""Tests de la línea de tiempo magnética (funciones puras de media_editor)."""

import unittest

from media_editor import (
    Clip,
    MediaInfo,
    Track,
    clamp_track_height,
    close_gap,
    collect_snap_points,
    compact_clips,
    find_gap,
    ripple_delete,
    snap_point,
    snap_threshold,
    snap_timeline_position,
    track_boundary_at,
    track_index_at,
    track_layout,
    unique_track_name,
    MAX_TRACK_HEIGHT,
    MIN_TRACK_HEIGHT,
)


def media(duration=60.0, path="clip.mp4"):
    return MediaInfo(path=path, duration=duration, has_video=True, has_audio=False)


def clip(start, length, path="clip.mp4"):
    return Clip(media(path=path), source_in=0, source_out=length, timeline_start=start)


class SnapTests(unittest.TestCase):
    def test_threshold_is_constant_in_pixels(self):
        self.assertAlmostEqual(snap_threshold(24, 12), 0.5)
        self.assertAlmostEqual(snap_threshold(120, 12), 0.1)
        self.assertGreater(snap_threshold(100000, 12), 0)

    def test_snap_point_picks_nearest_candidate_within_threshold(self):
        self.assertEqual(snap_point(4.9, [0, 5, 10], 0.3), (5.0, 5.0))
        self.assertEqual(snap_point(4.4, [0, 5, 10], 0.3), (4.4, None))
        self.assertEqual(snap_point(9.8, [9.7, 10.0], 0.5), (9.7, 9.7))

    def test_snap_point_never_negative(self):
        value, _ = snap_point(-3, [], 0.5)
        self.assertEqual(value, 0.0)

    def test_collect_points_includes_origin_playhead_and_edges_but_excludes_dragged(self):
        dragged = clip(20, 4)
        other = clip(2, 3)
        tracks = [Track("V1", "video", clips=[dragged, other])]
        points = collect_snap_points(tracks, exclude=[dragged], playhead=7.5)
        self.assertEqual(points, [0.0, 2.0, 5.0, 7.5])

    def test_moving_clip_end_snaps_to_next_clip_start(self):
        # Clip de 4 s soltado en 5.8 -> su final (9.8) se pega al clip que empieza en 10.
        start, target = snap_timeline_position(5.8, 4, [0, 10, 14], 0.5)
        self.assertAlmostEqual(start, 6.0)
        self.assertEqual(target, 10)

    def test_moving_clip_start_snaps_to_previous_clip_end(self):
        start, target = snap_timeline_position(5.3, 2, [0, 5], 0.5)
        self.assertEqual((start, target), (5.0, 5.0))


class GapTests(unittest.TestCase):
    def test_find_gap(self):
        clips = [clip(0, 2), clip(5, 2)]
        self.assertEqual(find_gap(clips, 3), (2.0, 5.0))
        self.assertIsNone(find_gap(clips, 1))   # dentro de un clip
        self.assertIsNone(find_gap(clips, 9))   # después del último clip

    def test_find_gap_at_project_start(self):
        self.assertEqual(find_gap([clip(3, 2)], 1), (0.0, 3.0))

    def test_close_gap_shifts_only_following_clips(self):
        first, second, third = clip(0, 2), clip(5, 2), clip(8, 1)
        shift = close_gap([first, second, third], 3)
        self.assertEqual(shift, 3.0)
        self.assertEqual([c.timeline_start for c in (first, second, third)], [0.0, 2.0, 5.0])

    def test_close_gap_without_gap_does_nothing(self):
        first, second = clip(0, 2), clip(2, 2)
        self.assertEqual(close_gap([first, second], 1), 0.0)
        self.assertEqual(second.timeline_start, 2.0)

    def test_ripple_delete_closes_hole(self):
        first, middle, last = clip(0, 2), clip(2, 3), clip(6, 2)
        clips = [first, middle, last]
        self.assertEqual(ripple_delete(clips, middle), 3.0)
        self.assertEqual(clips, [first, last])
        self.assertEqual(last.timeline_start, 3.0)

    def test_ripple_delete_never_overlaps_previous_clip(self):
        first, middle, last = clip(0, 2), clip(4, 1), clip(5, 2)
        clips = [first, middle, last]
        ripple_delete(clips, middle)
        self.assertGreaterEqual(last.timeline_start, first.timeline_end)

    def test_compact_clips_packs_in_order_from_zero(self):
        a, b, c = clip(7, 2), clip(1, 3), clip(20, 1)
        deltas = compact_clips([a, b, c])
        self.assertEqual((b.timeline_start, a.timeline_start, c.timeline_start), (0.0, 3.0, 5.0))
        self.assertEqual(deltas[id(b)], -1.0)
        self.assertEqual(deltas[id(c)], -15.0)

    def test_compact_clips_reorders_when_dragged_past_neighbour(self):
        a, b = clip(0, 4), clip(4, 2)
        a.timeline_start = 5  # arrastrado por detrás de b
        compact_clips([a, b])
        self.assertEqual((b.timeline_start, a.timeline_start), (0.0, 2.0))


class TrackLayoutTests(unittest.TestCase):
    def test_layout_accumulates_variable_heights(self):
        self.assertEqual(track_layout([40, 100, 60], top=24), [(24, 64), (64, 164), (164, 224)])

    def test_track_index_at(self):
        layout = track_layout([40, 100], top=24)
        self.assertIsNone(track_index_at(layout, 10))
        self.assertEqual(track_index_at(layout, 30), 0)
        self.assertEqual(track_index_at(layout, 100), 1)
        self.assertIsNone(track_index_at(layout, 200))

    def test_boundary_detection_uses_real_positions(self):
        layout = track_layout([40, 100, 60], top=24)
        self.assertEqual(track_boundary_at(layout, 66), 0)
        self.assertEqual(track_boundary_at(layout, 160), 1)
        self.assertEqual(track_boundary_at(layout, 226), 2)   # también la última pista
        self.assertIsNone(track_boundary_at(layout, 110))

    def test_clamp_track_height(self):
        self.assertEqual(clamp_track_height(1), MIN_TRACK_HEIGHT)
        self.assertEqual(clamp_track_height(9999), MAX_TRACK_HEIGHT)
        self.assertEqual(clamp_track_height(80), 80)

    def test_unique_track_name_after_deleting(self):
        self.assertEqual(unique_track_name(["Vídeo 2"], "video"), "Vídeo 1")
        self.assertEqual(unique_track_name(["Vídeo 1", "Vídeo 2"], "video"), "Vídeo 3")
        self.assertEqual(unique_track_name(["Vídeo 1"], "audio"), "Audio 1")


class ProjectAndExportTests(unittest.TestCase):
    def test_parse_ffmpeg_time(self):
        from media_editor import parse_ffmpeg_time
        self.assertAlmostEqual(parse_ffmpeg_time("frame=12 time=00:01:23.45 bitrate=1000kbits/s"), 83.45)
        self.assertIsNone(parse_ffmpeg_time("frame=12"))

    def test_project_save_load_roundtrip(self):
        import os
        import tempfile
        from media_editor import EditorProject, load_project, save_project

        m = MediaInfo(path="demo.mp4", duration=12.0, has_video=True, has_audio=True, width=1280, height=720, fps=30.0)
        c = Clip(m, source_in=1.0, source_out=6.0, timeline_start=2.0, volume=1.5, fade_in=0.5, fade_out=0.75)
        proj = EditorProject(tracks=[Track("Vídeo 1", "video", clips=[c])], selected_track_index=0)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.avep")
            save_project(path, proj, {"Vídeo 1": 80.0})
            loaded, heights, media_list = load_project(path)
        self.assertEqual(len(media_list), 1)
        self.assertEqual(heights["Vídeo 1"], 80.0)
        lc = loaded.tracks[0].clips[0]
        self.assertAlmostEqual(lc.volume, 1.5)
        self.assertAlmostEqual(lc.fade_in, 0.5)
        self.assertAlmostEqual(lc.fade_out, 0.75)

    def test_export_includes_setsar_and_volume_fades(self):
        from media_editor import EditorProject, build_export_plan

        m = MediaInfo(path="demo.mp4", duration=10.0, has_video=True, has_audio=True, width=1280, height=720, fps=30.0)
        c = Clip(m, source_in=0.0, source_out=4.0, timeline_start=0.0, volume=0.5, fade_in=0.5, fade_out=0.5)
        proj = EditorProject(tracks=[Track("V1", "video", clips=[c])])
        plan = build_export_plan(proj, "active", None, "out.mp4")
        fc = plan["filter_complex"]
        self.assertIn("setsar=1", fc)
        self.assertIn("aformat=channel_layouts=stereo", fc)
        self.assertIn("volume=0.5", fc)
        self.assertIn("fade=t=in", fc)
        self.assertIn("afade=t=out", fc)


if __name__ == "__main__":
    unittest.main()

