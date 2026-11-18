import pathlib
import tempfile
import unittest
from unittest.mock import patch

from media_editor import (
    Clip,
    EditorProject,
    MediaInfo,
    Track,
    build_export_plan,
    parse_editor_time,
    clamp_media_time,
    normalize_selection,
    resize_clip_left,
    resize_clip_right,
    split_clip,
    build_pitch_filter,
    pitch_factor,
    snap_timeline_position,
    unlink_video_audio,
    waveform_samples,
    waveform_from_values,
    playback_range,
    waveform_segment,
    avoid_clip_overlap,
)


def video_info(path="video.mp4", duration=20.0, audio=True):
    return MediaInfo(
        path=path,
        duration=duration,
        has_video=True,
        has_audio=audio,
        width=1920,
        height=1080,
        fps=30.0,
        format_name="mp4",
        video_codec="h264",
        audio_codec="aac" if audio else None,
    )


def audio_info(path="audio.mp3", duration=12.0):
    return MediaInfo(
        path=path,
        duration=duration,
        has_video=False,
        has_audio=True,
        format_name="mp3",
        audio_codec="mp3",
    )


class TimeAndModelTests(unittest.TestCase):
    def test_timeline_cursor_maps_to_clip_source_time(self):
        from media_editor import timeline_to_source_time
        clip = Clip(video_info(duration=20), source_in=5, source_out=15, timeline_start=12)
        self.assertEqual(timeline_to_source_time(clip, 15), 8.0)
        self.assertIsNone(timeline_to_source_time(clip, 25))

    def test_playback_starts_at_mark_in_and_ends_at_mark_out(self):
        self.assertEqual(playback_range(25, 10, 40, 60, from_mark=True), (10.0, 40.0))
        self.assertEqual(playback_range(25, 10, 40, 60), (25.0, 40.0))
        self.assertEqual(playback_range(0, 0, 40, 60), (0.0, 40.0))
    def test_visual_cursor_and_selection_stay_inside_media_duration(self):
        self.assertEqual(clamp_media_time(-4, 120), 0)
        self.assertEqual(clamp_media_time(145, 120), 120)
        self.assertEqual(normalize_selection(90, 20, 120), (20, 90))
        self.assertEqual(normalize_selection(90, 90, 120), (90, 120))

    def test_editor_time_accepts_seconds_and_clock_formats(self):
        self.assertEqual(parse_editor_time("90"), 90.0)
        self.assertEqual(parse_editor_time("01:30"), 90.0)
        self.assertEqual(parse_editor_time("01:02:03.5"), 3723.5)

    def test_clip_validates_bounds_and_project_duration(self):
        clip = Clip(video_info(), source_in=2, source_out=8, timeline_start=5)
        self.assertEqual(clip.duration, 6)
        project = EditorProject([Track("Vídeo 1", "video", clips=[clip])])
        self.assertEqual(project.duration(), 11)
        with self.assertRaisesRegex(ValueError, "posterior"):
            Clip(video_info(), source_in=8, source_out=2)

    def test_export_scope_excludes_disabled_tracks_or_uses_selected(self):
        enabled = Track("V1", "video", clips=[Clip(video_info("one.mp4"))])
        disabled = Track("V2", "video", enabled=False, clips=[Clip(video_info("two.mp4"))])
        selected = Track("A1", "audio", clips=[Clip(audio_info("music.mp3"))])
        project = EditorProject([enabled, disabled, selected], selected_track_index=2)
        self.assertEqual(project.export_tracks("active"), [enabled, selected])
        self.assertEqual(project.export_tracks("selected"), [selected])


class ClipEditingTests(unittest.TestCase):
    def test_move_is_clamped_to_front_or_back_of_neighbor_without_overlap(self):
        ranges = [(0.0, 5.0), (10.0, 15.0)]
        self.assertEqual(avoid_clip_overlap(4.0, 3.0, ranges), 5.0)
        self.assertEqual(avoid_clip_overlap(9.0, 3.0, ranges), 7.0)
        self.assertEqual(avoid_clip_overlap(6.0, 3.0, ranges), 6.0)

    def test_waveform_segment_uses_source_in_and_out_without_changing_tempo(self):
        source = [float(index) for index in range(10)]
        segment = waveform_segment(source, 2, 6, 10, 4)
        self.assertEqual(segment, [2.0, 3.0, 4.0, 5.0])

    def test_waveform_from_values_creates_visible_rms_shape(self):
        samples = waveform_from_values([0.0, 0.0, 0.5, 0.5, 1.0, 1.0], 3)
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0], 0.0)
        self.assertGreater(samples[2], samples[1])

    def test_unlink_video_audio_preserves_timing_and_removes_link(self):
        video = Clip(video_info(duration=30), source_in=4, source_out=12, timeline_start=7, link_group="g1")
        audio = Clip(audio_info(duration=30), source_in=4, source_out=12, timeline_start=7, link_group="g1")
        detached_video, detached_audio = unlink_video_audio(video, audio)
        self.assertEqual((detached_video.timeline_start, detached_audio.timeline_start), (7, 7))
        self.assertEqual((detached_video.duration, detached_audio.duration), (8, 8))
        self.assertIsNone(detached_video.link_group)
        self.assertIsNone(detached_audio.link_group)

    @patch("media_editor.discover_ffmpeg_tools", side_effect=FileNotFoundError)
    def test_waveform_has_safe_center_line_fallback(self, _tools):
        self.assertEqual(waveform_samples("missing.wav", 5), [0.0] * 5)

    def test_clip_pitch_is_kept_and_pitch_filter_preserves_duration(self):
        clip = Clip(audio_info(), pitch_semitones=3)
        self.assertEqual(clip.pitch_semitones, 3)
        self.assertAlmostEqual(pitch_factor(12), 2.0)
        filter_text = build_pitch_filter(3)
        self.assertIn("asetrate=44100", filter_text)
        self.assertIn("atempo=", filter_text)

    def test_pitch_is_limited_to_twelve_semitones(self):
        with self.assertRaises(ValueError):
            Clip(audio_info(), pitch_semitones=13)
        self.assertIsNone(build_pitch_filter(0))

    def test_snap_aligns_start_or_end_to_nearest_candidate(self):
        self.assertEqual(snap_timeline_position(4.92, 2.0, [5.0, 9.0], 0.12), (5.0, 5.0))
        self.assertEqual(snap_timeline_position(7.08, 2.0, [5.0, 9.0], 0.12), (7.0, 9.0))
        self.assertEqual(snap_timeline_position(4.5, 2.0, [5.0], 0.1), (4.5, None))

    def test_split_clip_preserves_source_mapping_and_timeline_position(self):
        clip = Clip(video_info(duration=30), source_in=10, source_out=20, timeline_start=5, name="Plano")

        left, right = split_clip(clip, 7)

        self.assertEqual((left.source_in, left.source_out, left.timeline_start), (10, 12, 5))
        self.assertEqual((right.source_in, right.source_out, right.timeline_start), (12, 20, 7))
        self.assertEqual(left.duration + right.duration, clip.duration)

    def test_split_clip_ignores_positions_at_or_outside_clip_edges(self):
        clip = Clip(video_info(duration=30), source_in=10, source_out=20, timeline_start=5)

        self.assertIsNone(split_clip(clip, 5))
        self.assertIsNone(split_clip(clip, 15))
        self.assertIsNone(split_clip(clip, 25))

    def test_resize_left_edge_can_shrink_or_recover_original_source(self):
        clip = Clip(video_info(duration=30), source_in=10, source_out=20, timeline_start=5)

        shrunk = resize_clip_left(clip, 7)
        expanded = resize_clip_left(clip, 2)

        self.assertEqual((shrunk.source_in, shrunk.source_out, shrunk.timeline_start), (12, 20, 7))
        self.assertEqual((expanded.source_in, expanded.source_out, expanded.timeline_start), (7, 20, 2))

    def test_resize_right_edge_can_shrink_or_recover_original_source(self):
        clip = Clip(video_info(duration=30), source_in=10, source_out=20, timeline_start=5)

        shrunk = resize_clip_right(clip, 8)
        expanded = resize_clip_right(clip, 40)

        self.assertEqual((shrunk.source_in, shrunk.source_out, shrunk.timeline_end), (10, 13, 8))
        self.assertEqual((expanded.source_in, expanded.source_out, expanded.timeline_end), (10, 30, 25))


class ExportPlanTests(unittest.TestCase):
    def test_video_tracks_are_composited_bottom_to_top(self):
        top = Track("Superior", "video", clips=[Clip(video_info("top.mp4"), source_out=5)])
        bottom = Track("Inferior", "video", clips=[Clip(video_info("bottom.mp4"), source_out=5)])
        plan = build_export_plan(EditorProject([top, bottom]), "active", None, "salida.mp4")
        self.assertEqual(plan["video_track_order"], ["Inferior", "Superior"])
        self.assertIn("overlay", plan["filter_complex"])

    def test_muted_track_and_video_without_source_audio_are_not_mixed(self):
        muted = Track("Mudo", "audio", muted=True, clips=[Clip(audio_info("muted.mp3"))])
        no_audio = Track(
            "Vídeo",
            "video",
            clips=[Clip(video_info("silent-choice.mp4"), use_source_audio=False)],
        )
        plan = build_export_plan(EditorProject([muted, no_audio]), "active", None, "salida.mp4")
        self.assertEqual(plan["audio_labels"], [])

    def test_audio_tracks_are_delayed_and_mixed_at_timeline_positions(self):
        first = Track("A1", "audio", clips=[Clip(audio_info("a.mp3"), source_out=4, timeline_start=0)])
        second = Track("A2", "audio", clips=[Clip(audio_info("b.mp3"), source_out=3, timeline_start=2.5)])
        plan = build_export_plan(EditorProject([first, second]), "active", None, "salida.wav")
        self.assertIn("adelay=2500|2500", plan["filter_complex"])
        self.assertIn("aresample=async=1:first_pts=0", plan["filter_complex"])
        self.assertIn("amix=inputs=2", plan["filter_complex"])
        self.assertFalse(plan["has_video"])

    def test_audio_pitch_is_present_in_export_filter(self):
        track = Track("A1", "audio", clips=[Clip(audio_info("voice.mp3"), source_out=4, pitch_semitones=-2)])
        plan = build_export_plan(EditorProject([track]), "active", None, "salida.mp3")
        self.assertIn("asetrate=44100", plan["filter_complex"])
        self.assertIn("atempo=", plan["filter_complex"])

    def test_selected_export_contains_only_selected_track_inputs(self):
        video = Track("V", "video", clips=[Clip(video_info("video.mp4"))])
        audio = Track("A", "audio", clips=[Clip(audio_info("audio.mp3"))])
        project = EditorProject([video, audio], selected_track_index=1)
        plan = build_export_plan(project, "selected", None, "salida.mp3")
        self.assertEqual(plan["inputs"], ["audio.mp3"])

    def test_odd_video_dimensions_are_rounded_to_even_for_libx264(self):
        odd_media = MediaInfo(path="odd.mp4", duration=5.0, has_video=True, has_audio=False, width=853, height=405, fps=30.0)
        track = Track("V", "video", clips=[Clip(odd_media, source_out=5)])
        plan = build_export_plan(EditorProject([track]), "active", None, "salida.mp4")
        self.assertEqual(plan["width"] % 2, 0)
        self.assertEqual(plan["height"] % 2, 0)

    def test_split_clip_clears_inner_fades(self):
        clip = Clip(video_info("v.mp4", duration=10), source_out=10, fade_in=1.5, fade_out=2.0)
        left, right = split_clip(clip, 5.0)
        self.assertEqual(left.fade_in, 1.5)
        self.assertEqual(left.fade_out, 0.0)
        self.assertEqual(right.fade_in, 0.0)
        self.assertEqual(right.fade_out, 2.0)

    def test_project_to_dict_preserves_unused_media_library(self):
        from media_editor import project_to_dict, project_from_dict
        unused = video_info("unused.mp4", duration=8)
        payload = project_to_dict(EditorProject([Track("V1", "video")]), media_library=[unused])
        _proj, _heights, media_list = project_from_dict(payload)
        self.assertEqual([m.path for m in media_list], ["unused.mp4"])

    def test_resize_clamps_overlapping_fades(self):
        clip = Clip(video_info("v.mp4", duration=10), source_out=10, fade_in=3.0, fade_out=3.0)
        shrunk = resize_clip_right(clip, 2.0)
        self.assertLessEqual(shrunk.fade_in + shrunk.fade_out, shrunk.duration + 1e-6)

    def test_extreme_vfr_fps_is_clamped_to_safe_default(self):
        vfr = MediaInfo(path="vfr.webm", duration=5.0, has_video=True, has_audio=False, width=640, height=360, fps=1000.0)
        plan = build_export_plan(EditorProject([Track("V", "video", clips=[Clip(vfr, source_out=5)])]), "active", None, "salida.mp4")
        self.assertEqual(plan["fps"], 30.0)

    def test_split_clip_clamps_fades_and_avoids_accumulating_suffixes(self):
        clip = Clip(video_info("v.mp4", duration=10), source_out=10, name="clip · 1", fade_in=5.0, fade_out=4.0)
        left, right = split_clip(clip, 2.0)
        self.assertEqual(left.name, "clip · 1")
        self.assertEqual(right.name, "clip · 2")
        self.assertLessEqual(left.fade_in, left.duration + 1e-6)
        self.assertEqual(right.fade_out, 4.0)

    def test_build_export_plan_respects_use_source_audio_on_audio_track(self):
        muted_audio = Clip(audio_info("a.mp3", duration=5), source_out=5, use_source_audio=False)
        active_audio = Clip(audio_info("b.mp3", duration=5), source_out=5, timeline_start=5, use_source_audio=True)
        plan = build_export_plan(EditorProject([Track("A1", "audio", clips=[muted_audio, active_audio])]), "active", None, "salida.mp3")
        self.assertEqual(len(plan["audio_labels"]), 1)

    def test_build_export_plan_rejects_fully_muted_audio_only_project(self):
        muted_clip = Clip(audio_info("a.mp3", duration=5), source_out=5, use_source_audio=False)
        with self.assertRaisesRegex(ValueError, "silenciadas"):
            build_export_plan(EditorProject([Track("A1", "audio", clips=[muted_clip])]), "active", None, "salida.mp3")
        active_clip = Clip(audio_info("a.mp3", duration=5), source_out=5, use_source_audio=True)
        with self.assertRaisesRegex(ValueError, "silenciadas"):
            build_export_plan(EditorProject([Track("A1", "audio", muted=True, clips=[active_clip])]), "active", None, "salida.mp3")

    def test_build_export_plan_falls_back_to_720p_when_video_dimensions_missing(self):
        v = MediaInfo(path="nodim.mp4", duration=5.0, has_video=True, has_audio=False, width=0, height=0, fps=30.0)
        plan = build_export_plan(EditorProject([Track("V1", "video", clips=[Clip(v, source_out=5)])]), "active", None, "salida.mp4")
        self.assertEqual((plan["width"], plan["height"]), (1280, 720))

    def test_probe_media_skips_empty_or_zero_duration(self):
        import json
        from media_editor import probe_media
        payload = json.dumps({
            "format": {"duration": "", "format_name": "mp4"},
            "streams": [
                {"codec_type": "video", "width": 1280, "height": 720, "avg_frame_rate": "30/1", "duration": "0"},
                {"codec_type": "audio", "duration": "12.5", "codec_name": "aac"},
            ],
        })
        fake = type("R", (), {"returncode": 0, "stdout": payload, "stderr": ""})()
        with patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
             patch("media_editor.subprocess.run", return_value=fake):
            info = probe_media("test.mp4")
        self.assertAlmostEqual(info.duration, 12.5, places=3)

    def test_build_export_plan_normalizes_clip_fps_for_mixed_rates(self):
        v24 = MediaInfo(path="a24.mp4", duration=4.0, has_video=True, has_audio=False, width=1280, height=720, fps=24.0)
        v60 = MediaInfo(path="b60.mp4", duration=4.0, has_video=True, has_audio=False, width=1280, height=720, fps=60.0)
        track = Track("V1", "video", clips=[Clip(v24, source_out=4, timeline_start=0), Clip(v60, source_out=4, timeline_start=4)])
        plan = build_export_plan(EditorProject([track]), "active", None, "salida.mp4")
        self.assertEqual(plan["fps"], 60.0)
        self.assertGreaterEqual(plan["filter_complex"].count("fps=60"), 2)

    def test_project_round_trip_deduplicates_separated_audio_media(self):
        from media_editor import audio_media_from_video, project_from_dict, project_to_dict
        v = video_info("shared.mp4", duration=10.0)
        a = audio_media_from_video(v)
        proj = EditorProject([
            Track("Vídeo 1", "video", clips=[Clip(v, source_out=6.0, use_source_audio=False, link_group="g1")]),
            Track("Audio 1", "audio", clips=[Clip(a, source_out=6.0, link_group="g1")]),
        ])
        payload = project_to_dict(proj, media_library=[v])
        self.assertEqual(len(payload["media"]), 1)
        restored, _heights, restored_media = project_from_dict(payload)
        self.assertEqual(len(restored_media), 1)
        self.assertTrue(restored.tracks[0].clips[0].media.has_video)
        self.assertFalse(restored.tracks[1].clips[0].media.has_video)

    def test_probe_media_handles_null_dimensions_and_project_from_dict_validates_bounds(self):
        import json
        from media_editor import probe_media, project_from_dict
        payload = json.dumps({
            "format": {"duration": "8.0", "format_name": "mp4"},
            "streams": [{"codec_type": "video", "width": None, "height": None, "avg_frame_rate": "30/1"}],
        })
        fake = type("R", (), {"returncode": 0, "stdout": payload, "stderr": ""})()
        with patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
             patch("media_editor.subprocess.run", return_value=fake):
            info = probe_media("null_dim.mp4")
        self.assertEqual((info.width, info.height), (0, 0))

        restored, _, _ = project_from_dict({
            "media": [{"path": "a.mp4", "duration": 5.0, "has_video": True, "has_audio": False, "width": None, "height": None, "fps": None}],
            "tracks": [{"name": "V1", "kind": "video", "clips": [{"media_idx": 0, "source_in": 0, "source_out": 5, "timeline_start": 0, "volume": None}]}],
            "selected_track_index": 99,
            "track_heights": None,
        })
        self.assertEqual(restored.selected_track_index, 0)
        self.assertEqual(restored.tracks[0].clips[0].volume, 1.0)
        with self.assertRaises(ValueError):
            project_from_dict({"media": [], "tracks": [{"name": "V1", "kind": "video", "clips": [{"media_idx": 3, "source_in": 0, "source_out": 1, "timeline_start": 0}]}]})

    def test_upper_overlay_track_fades_use_alpha_and_run_export_cleans_aborted_process(self):
        from media_editor import run_export
        top = Track("Superior", "video", clips=[Clip(video_info("top.mp4"), source_out=5, fade_in=1.0)])
        bottom = Track("Inferior", "video", clips=[Clip(video_info("bottom.mp4"), source_out=5, fade_in=1.0)])
        plan = build_export_plan(EditorProject([top, bottom]), "active", None, "salida.mp4")
        fc = plan["filter_complex"]
        self.assertIn("format=yuva420p,fade=t=in:st=0:d=1:alpha=1", fc)
        self.assertIn(":color=black@0", fc)

        fake_proc = type("Proc", (), {})()
        terminated = []
        fake_proc.stdout = type("Stdout", (), {
            "__iter__": lambda self: iter(["time=00:00:01.00\n"]),
            "close": lambda self: None,
        })()
        fake_proc.poll = lambda: None
        fake_proc.terminate = lambda: terminated.append(True)
        fake_proc.wait = lambda timeout=None: 0
        with patch("media_editor.subprocess.Popen", return_value=fake_proc):
            with self.assertRaises(RuntimeError):
                run_export({"command": ["ffmpeg"], "output_path": "/tmp/out_abort.mp4"}, lambda _l: (_ for _ in ()).throw(RuntimeError("abort")))
        self.assertEqual(terminated, [True])

    def test_pitch_resamples_first_fps_handles_zero_and_project_validation(self):
        import os
        from media_editor import _parse_fps, project_from_dict, save_project, load_project

        pf = build_pitch_filter(4)
        self.assertTrue(pf.startswith("aresample=44100,asetrate=44100*"))

        self.assertEqual(_parse_fps("30/0"), 0.0)
        self.assertEqual(_parse_fps("N/A"), 0.0)

        vtrack = Track("V1", "video", clips=[Clip(video_info("v.mp4"), source_out=5)])
        for ext in (".m4a", ".flac", ".ogg"):
            with self.assertRaisesRegex(ValueError, "MP4"):
                build_export_plan(EditorProject([vtrack]), "active", None, f"out{ext}")

        atrack = Track("A1", "audio", clips=[Clip(audio_info("a.mp3"), source_out=5)])
        flac_plan = build_export_plan(EditorProject([atrack]), "active", None, "out.flac")
        ogg_plan = build_export_plan(EditorProject([atrack]), "active", None, "out.ogg")
        self.assertIn("flac", flac_plan["command"])
        self.assertIn("libopus", ogg_plan["command"])

        with self.assertRaises(ValueError):
            project_from_dict({"media": [{"path": "x.mp4"}], "tracks": []})
        proj, heights, _ = project_from_dict({"media": [], "tracks": [], "track_heights": ["bad"]})
        self.assertEqual(heights, {})

        with tempfile.TemporaryDirectory() as folder:
            proj_path = os.path.join(folder, "sub", "demo.avep")
            save_project(proj_path, EditorProject([atrack]), {"A1": 70.0})
            self.assertFalse(os.path.exists(f"{proj_path}.tmp"))
            loaded, loaded_h, _ = load_project(proj_path)
            self.assertEqual(len(loaded.tracks), 1)
            self.assertEqual(loaded_h["A1"], 70.0)

    def test_attached_pic_ignored_finite_validation_export_ext_and_waveform_timeout(self):
        import json
        import subprocess
        from media_editor import probe_media, project_from_dict, clamp_track_height

        # 1. Carátula embebida (attached_pic=1) en MP3 no convierte la canción en vídeo
        payload = json.dumps({
            "format": {"duration": "120.5", "format_name": "mp3"},
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3", "duration": "120.5"},
                {"codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500, "disposition": {"attached_pic": 1}},
            ],
        })
        fake = type("R", (), {"returncode": 0, "stdout": payload, "stderr": ""})()
        with patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
             patch("media_editor.subprocess.run", return_value=fake):
            info = probe_media("cover.mp3")
        self.assertFalse(info.has_video)
        self.assertTrue(info.has_audio)

        # 2. waveform_samples devuelve silencio si FFmpeg agota el timeout
        with patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
             patch("media_editor.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 30)):
            self.assertEqual(waveform_samples("slow.mp4", 4), [0.0, 0.0, 0.0, 0.0])

        # 3. Exportar vídeo rechaza extensiones no válidas y limita faststart a mp4/mov
        vtrack = Track("V1", "video", clips=[Clip(video_info("v.mp4"), source_out=5)])
        with self.assertRaisesRegex(ValueError, "MP4"):
            build_export_plan(EditorProject([vtrack]), "active", None, "salida.txt")
        mkv_plan = build_export_plan(EditorProject([vtrack]), "active", None, "salida.mkv")
        mp4_plan = build_export_plan(EditorProject([vtrack]), "active", None, "salida.mp4")
        self.assertNotIn("-movflags", mkv_plan["command"])
        self.assertIn("-movflags", mp4_plan["command"])

        # 4. parse_editor_time, clamp_track_height y project_from_dict rechazan NaN/Inf/bool
        with self.assertRaises(ValueError):
            parse_editor_time("nan")
        with self.assertRaises(ValueError):
            parse_editor_time(True)
        with self.assertRaises(ValueError):
            clamp_track_height(float("inf"))
        with self.assertRaises(ValueError):
            project_from_dict({"media": [{"path": "x.mp4", "duration": float("nan"), "has_video": True, "has_audio": False}], "tracks": []})

    def test_upper_overlay_transparent_pad_without_fades_clip_finite_probe_timeout_and_overflow(self):
        import subprocess
        from media_editor import probe_media, project_from_dict

        # 1. Capa superior sin fade también usa yuva420p y color=black@0 para no tapar la pista inferior
        top = Track("Superior", "video", clips=[Clip(video_info("top.mp4"), source_out=4)])
        bottom = Track("Inferior", "video", clips=[Clip(video_info("bottom.mp4"), source_out=4)])
        plan = build_export_plan(EditorProject([top, bottom]), "active", None, "salida.mp4")
        self.assertIn("format=yuva420p", plan["filter_complex"])
        self.assertIn(":color=black@0", plan["filter_complex"])

        # 2. Clip rechaza volume/fade_in/fade_out no finitos
        with self.assertRaises(ValueError):
            Clip(video_info("v.mp4"), source_out=2.0, volume=float("nan"))
        with self.assertRaises(ValueError):
            Clip(video_info("v.mp4"), source_out=2.0, fade_in=float("inf"))

        # 3. probe_media captura TimeoutExpired con ValueError
        with patch("media_editor.discover_ffmpeg_tools", return_value=("ffmpeg", "ffprobe", "ffplay")), \
             patch("media_editor.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 30)):
            with self.assertRaisesRegex(ValueError, "agotado"):
                probe_media("hang.mp4")

        # 4. project_from_dict controla OverflowError por float('nan')/'inf' en campos enteros
        with self.assertRaises(ValueError):
            project_from_dict({
                "media": [{"path": "a.mp4", "duration": 5.0, "has_video": True, "has_audio": False}],
                "tracks": [{"name": "V1", "kind": "video", "clips": [{"media_idx": 0, "source_in": 0, "source_out": 2, "timeline_start": 0, "pitch_semitones": float("inf")}]}],
            })
        proj, _, _ = project_from_dict({"media": [], "tracks": [], "selected_track_index": float("nan")})
        self.assertEqual(proj.selected_track_index, 0)


if __name__ == "__main__":
    unittest.main()

