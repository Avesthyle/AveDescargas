import unittest
import os
import tempfile

from media_grabber import (
    detect_platform,
    apply_clip_to_targets,
    build_output_template,
    clip_download_options,
    download_scope_options,
    has_playlist_reference,
    playlist_entries_from_info,
    prepare_download_targets,
    parse_timecode,
    unsupported_download_reason,
    validate_clip_range,
    load_settings,
    save_settings,
    preferred_download_folder,
    sanitize_output_name,
    is_youtube_auth_challenge,
    chrome_is_running_from_tasklist,
    chrome_cookie_options,
    youtube_profile_has_cookies,
    authenticated_retry_options,
    build_video_format,
    video_audio_description,
    youtube_extractor_options,
    heavy_clip_notice,
)


class SettingsAndNamingTests(unittest.TestCase):
    def test_settings_round_trip_remembers_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "settings.json")
            save_settings(path, {"download_folder": folder})
            self.assertEqual(load_settings(path)["download_folder"], folder)

    def test_missing_or_damaged_settings_are_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = os.path.join(folder, "missing.json")
            damaged = os.path.join(folder, "damaged.json")
            with open(damaged, "w", encoding="utf-8") as handle:
                handle.write("not-json")
            self.assertEqual(load_settings(missing), {})
            self.assertEqual(load_settings(damaged), {})

    def test_preferred_folder_only_uses_existing_saved_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(preferred_download_folder({"download_folder": folder}, "fallback"), folder)
            self.assertEqual(preferred_download_folder({"download_folder": os.path.join(folder, "gone")}, "fallback"), "fallback")

    def test_custom_name_is_windows_safe(self):
        self.assertEqual(sanitize_output_name('  Mi: canción?  '), "Mi canción")
        self.assertEqual(sanitize_output_name("CON"), "")
        self.assertEqual(sanitize_output_name("   ..."), "")

    def test_custom_name_changes_template_without_extension(self):
        folder = r"C:\Descargas"
        target = {"url": "https://example.test/video", "custom_name": "Mi canción"}
        self.assertEqual(build_output_template(folder, target), os.path.join(folder, "Mi canción.%(ext)s"))


class VideoAudioModeTests(unittest.TestCase):
    def test_video_with_audio_remains_the_default(self):
        selected = build_video_format("1080p (Full HD)")
        self.assertIn("[height<=1080]", selected)
        self.assertIn("bestaudio", selected)

    def test_video_without_audio_never_requests_an_audio_track(self):
        selected = build_video_format("720p (HD)", include_audio=False)
        self.assertIn("[height<=720]", selected)
        self.assertNotIn("bestaudio", selected)
        self.assertNotIn("acodec!=none", selected)
        self.assertIn("acodec=none", selected)

    def test_log_description_clearly_identifies_both_modes(self):
        self.assertEqual(video_audio_description(True), "Con sonido")
        self.assertEqual(video_audio_description(False), "Sin sonido")

    def test_silent_video_uses_youtube_client_that_exposes_video_only_formats(self):
        self.assertEqual(youtube_extractor_options("mp4", False), {})
        self.assertEqual(
            youtube_extractor_options("mp4", True),
            {"extractor_args": {"youtube": {"player_client": ["android"]}}},
        )


class HeavyClipNoticeTests(unittest.TestCase):
    def test_warns_before_downloading_a_1080p_clip(self):
        notice = heavy_clip_notice(
            {"section_start": 0, "section_end": 300},
            "1080p (Full HD)",
        )
        self.assertIsNotNone(notice)
        self.assertIn("1080p", notice)
        self.assertIn("fragmento", notice.lower())
        self.assertIn("no se está descargando el vídeo completo", notice.lower())

    def test_does_not_warn_for_a_complete_download(self):
        self.assertIsNone(heavy_clip_notice({}, "1080p (Full HD)"))

    def test_does_not_warn_for_a_720p_clip(self):
        self.assertIsNone(
            heavy_clip_notice(
                {"section_start": 0, "section_end": 300},
                "720p (HD)",
            )
        )


class YouTubeAuthenticationTests(unittest.TestCase):
    def test_detects_bot_confirmation_and_sign_in_errors(self):
        self.assertTrue(is_youtube_auth_challenge("Sign in to confirm you’re not a bot"))
        self.assertTrue(is_youtube_auth_challenge("Use --cookies-from-browser for authentication"))
        self.assertTrue(is_youtube_auth_challenge("Sign in to confirm your age"))

    def test_does_not_prompt_for_unrelated_errors(self):
        self.assertFalse(is_youtube_auth_challenge("Video unavailable"))
        self.assertFalse(is_youtube_auth_challenge("Requested format is not available"))

    def test_detects_chrome_in_windows_process_list(self):
        self.assertTrue(chrome_is_running_from_tasklist('"chrome.exe","1234"'))
        self.assertFalse(chrome_is_running_from_tasklist('"explorer.exe","1234"'))

    def test_authenticated_retry_uses_chrome_cookies(self):
        profile = r"C:\AuthProfile"
        self.assertEqual(
            chrome_cookie_options(profile),
            {"cookiesfrombrowser": ("chrome", profile)},
        )

    def test_youtube_cookie_retry_does_not_force_android_client(self):
        base = {
            "quiet": True,
            "extractor_args": {"youtube": {"player_client": ["android"]}},
        }
        result = authenticated_retry_options(base, "chrome", "youtube", r"C:\AuthProfile")
        self.assertNotIn("extractor_args", result)
        self.assertEqual(result["cookiesfrombrowser"], ("chrome", r"C:\AuthProfile"))

    def test_detects_saved_youtube_session_in_special_profile(self):
        with tempfile.TemporaryDirectory() as profile:
            self.assertFalse(youtube_profile_has_cookies(profile))
            cookie_folder = os.path.join(profile, "Default", "Network")
            os.makedirs(cookie_folder)
            with open(os.path.join(cookie_folder, "Cookies"), "wb") as handle:
                handle.write(b"sqlite")
            self.assertTrue(youtube_profile_has_cookies(profile))


class SpotifyUrlTests(unittest.TestCase):
    def test_spotify_track_is_rejected_with_clear_drm_explanation(self):
        url = (
            "https://open.spotify.com/intl-es/track/0sKS1f30oQdGw903RR7fBN"
            "?si=I8RJ_5h7S9CRHBpZXvPWeg&utm_source=whatsapp&nd=1"
        )
        self.assertEqual(detect_platform(url), "spotify")
        self.assertEqual(
            unsupported_download_reason(url),
            (
                "Spotify protege sus canciones con DRM y no permite que AveDescargas "
                "las descargue directamente. Puedes escucharla o descargarla para uso "
                "sin conexión desde la aplicación oficial de Spotify."
            ),
        )


class ClipDownloadTests(unittest.TestCase):
    def test_timecodes_accept_seconds_minutes_and_hours(self):
        self.assertEqual(parse_timecode("90"), 90)
        self.assertEqual(parse_timecode("01:30"), 90)
        self.assertEqual(parse_timecode("02:01:30"), 7290)

    def test_invalid_or_reversed_ranges_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "formato"):
            parse_timecode("una hora")
        with self.assertRaisesRegex(ValueError, "posterior"):
            validate_clip_range("10:00", "09:59")

    def test_clip_range_is_attached_to_every_selected_target(self):
        targets = [
            {"url": "https://www.youtube.com/watch?v=one", "allow_playlist": False},
            {"url": "https://www.youtube.com/watch?v=two", "allow_playlist": False},
        ]
        self.assertEqual(
            apply_clip_to_targets(targets, 3600, 3660),
            [
                {
                    "url": targets[0]["url"],
                    "allow_playlist": False,
                    "section_start": 3600,
                    "section_end": 3660,
                },
                {
                    "url": targets[1]["url"],
                    "allow_playlist": False,
                    "section_start": 3600,
                    "section_end": 3660,
                },
            ],
        )

    def test_python_api_receives_only_the_requested_range(self):
        target = {
            "url": "https://www.youtube.com/watch?v=one",
            "section_start": 5400,
            "section_end": 5700,
        }
        options = clip_download_options(target, "mp4")
        self.assertTrue(options["force_keyframes_at_cuts"])
        self.assertEqual(
            list(options["download_ranges"]({"duration": 36000}, None)),
            [{"start_time": 5400, "end_time": 5700}],
        )

    def test_clip_filename_does_not_collide_with_full_download(self):
        folder = r"C:\Descargas"
        full = {"url": "https://example.test/video"}
        clip = {**full, "section_start": 90, "section_end": 150}
        self.assertEqual(
            build_output_template(folder, full),
            os.path.join(folder, "%(title).150B [%(id)s].%(ext)s"),
        )
        self.assertEqual(
            build_output_template(folder, clip),
            os.path.join(folder, "%(title).150B [%(id)s] [fragmento 90-150].%(ext)s"),
        )


class PlaylistHandlingTests(unittest.TestCase):
    def setUp(self):
        self.playlist_video = (
            "https://www.youtube.com/watch?v=abc123&list=PL987&index=4"
        )

    def test_detects_youtube_video_that_belongs_to_playlist(self):
        self.assertTrue(has_playlist_reference(self.playlist_video))
        self.assertFalse(
            has_playlist_reference("https://www.youtube.com/watch?v=abc123")
        )

    def test_single_choice_disables_playlist_download(self):
        self.assertEqual(
            prepare_download_targets(self.playlist_video, "single"),
            [{"url": self.playlist_video, "allow_playlist": False}],
        )

    def test_all_choice_explicitly_allows_playlist_download(self):
        self.assertEqual(
            prepare_download_targets(self.playlist_video, "all"),
            [{"url": self.playlist_video, "allow_playlist": True}],
        )

    def test_download_scope_reaches_ytdlp_as_noplaylist_option(self):
        self.assertEqual(
            download_scope_options({"url": self.playlist_video, "allow_playlist": False}),
            {"noplaylist": True},
        )
        self.assertEqual(
            download_scope_options({"url": self.playlist_video, "allow_playlist": True}),
            {"noplaylist": False},
        )

    def test_selected_choice_downloads_only_selected_entries(self):
        selected = [
            "https://www.youtube.com/watch?v=one",
            "https://www.youtube.com/watch?v=two",
        ]
        self.assertEqual(
            prepare_download_targets(self.playlist_video, "selected", selected),
            [
                {"url": selected[0], "allow_playlist": False},
                {"url": selected[1], "allow_playlist": False},
            ],
        )

    def test_selected_choice_requires_at_least_one_entry(self):
        with self.assertRaisesRegex(ValueError, "al menos una canción"):
            prepare_download_targets(self.playlist_video, "selected", [])

    def test_playlist_entries_keep_titles_and_build_watch_urls(self):
        info = {
            "title": "Lista de prueba",
            "entries": [
                {"id": "one", "title": "Primera", "url": "one"},
                {
                    "id": "two",
                    "title": "Segunda",
                    "webpage_url": "https://www.youtube.com/watch?v=two",
                },
                None,
            ],
        }
        self.assertEqual(
            playlist_entries_from_info(info),
            [
                {
                    "title": "Primera",
                    "url": "https://www.youtube.com/watch?v=one",
                },
                {
                    "title": "Segunda",
                    "url": "https://www.youtube.com/watch?v=two",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()

