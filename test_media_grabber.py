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

    def test_clean_folder_preserves_user_media_and_images(self):
        from media_grabber import clean_folder
        with tempfile.TemporaryDirectory() as folder:
            keep = ["export.mkv", "clip.webm", "cancion.m4a", "foto.jpg", "logo.png", "portada.webp"]
            remove = ["video.mp4.part", "video.ytdl", "video.f251.webm", "video.temp.mp4"]
            for name in keep + remove:
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(b"data")
            deleted = clean_folder(folder)
            self.assertEqual(deleted, len(remove))
            for name in keep:
                self.assertTrue(os.path.exists(os.path.join(folder, name)), name)
            for name in remove:
                self.assertFalse(os.path.exists(os.path.join(folder, name)), name)

    def test_load_title_async_preserves_custom_name(self):
        import types
        import media_grabber

        class DummyVar:
            def __init__(self, value=""):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value

        class DummyText:
            def __init__(self, content):
                self.content = content
            def get(self, *_args):
                return self.content

        url = "https://www.youtube.com/watch?v=abc123"
        dummy = types.SimpleNamespace(
            url_input=DummyText(url),
            custom_name_var=DummyVar("Mi nombre propio"),
            title_lookup_url=None,
            root=types.SimpleNamespace(after=lambda *_a: None),
            check_queue=lambda: None,
        )
        media_grabber.MediaGrabberApp.load_title_async(dummy)
        self.assertEqual(dummy.custom_name_var.get(), "Mi nombre propio")

        # Si el usuario escribe su propio nombre mientras yt-dlp consulta, check_queue no lo borra
        dummy.title_lookup_url = url
        media_grabber.ui_queue.put({"type": "detected_title", "url": url, "title": "Titulo Youtube"})
        media_grabber.MediaGrabberApp.check_queue(dummy)
        self.assertEqual(dummy.custom_name_var.get(), "Mi nombre propio")

        from unittest import mock
        with mock.patch("media_grabber.threading.Thread") as thread_mock:
            media_grabber.MediaGrabberApp.load_title_async(dummy, force=True)
        self.assertEqual(dummy.custom_name_var.get(), "Obteniendo título…")
        thread_mock.assert_called_once()

        dummy.url_input = types.SimpleNamespace(delete=lambda *_a: None)
        media_grabber.MediaGrabberApp.clear_urls(dummy)
        self.assertEqual(dummy.custom_name_var.get(), "")
        self.assertIsNone(dummy.title_lookup_url)

    def test_run_downloads_thread_resets_is_downloading_on_unexpected_error(self):
        from unittest import mock
        import media_grabber
        with mock.patch("media_grabber.get_ffmpeg_path", side_effect=RuntimeError("boom")):
            media_grabber.run_downloads_thread([{"url": "https://example.com"}], "/tmp", "mp4", "1080p (Full HD)")
        self.assertFalse(media_grabber.IS_DOWNLOADING)

    def test_on_close_confirms_export_and_cleans_up(self):
        import types
        from unittest import mock
        import media_grabber

        destroyed = []
        canceled = []
        panel_destroyed = []
        dummy = types.SimpleNamespace(
            _queue_after_id="after#1",
            root=types.SimpleNamespace(
                after_cancel=lambda aid: canceled.append(aid),
                destroy=lambda: destroyed.append(True),
            ),
            editor_panel=types.SimpleNamespace(
                exporting=True,
                destroy=lambda: panel_destroyed.append(True),
            ),
        )
        with mock.patch("media_grabber.messagebox.askyesno", return_value=False):
            media_grabber.MediaGrabberApp.on_close(dummy)
        self.assertEqual(destroyed, [])
        self.assertEqual(panel_destroyed, [])

        with mock.patch("media_grabber.messagebox.askyesno", return_value=True):
            media_grabber.MediaGrabberApp.on_close(dummy)
        self.assertEqual(canceled, ["after#1"])
        self.assertEqual(panel_destroyed, [True])
        self.assertEqual(destroyed, [True])

    def test_auto_title_clears_when_url_changes_and_mp3_checks_ffmpeg(self):
        import types
        from unittest import mock
        import media_grabber

        class DummyVar:
            def __init__(self, value=""):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value

        class DummyText:
            def __init__(self, content):
                self.content = content
            def get(self, *_args):
                return self.content

        url1 = "https://www.youtube.com/watch?v=one"
        url2 = "https://www.youtube.com/watch?v=two"
        dummy = types.SimpleNamespace(
            url_input=DummyText(url1),
            custom_name_var=DummyVar(""),
            title_lookup_url=url1,
            _auto_title_value="Titulo Viejo",
        )
        dummy.custom_name_var.set("Titulo Viejo")
        dummy.url_input = DummyText(url2)
        with mock.patch("media_grabber.threading.Thread") as thread_mock:
            media_grabber.MediaGrabberApp.load_title_async(dummy)
        self.assertEqual(dummy.custom_name_var.get(), "Obteniendo título…")
        thread_mock.assert_called_once()

        # Si el usuario pega una playlist tras detectar un título automático, se limpia
        dummy.custom_name_var.set("Titulo Viejo")
        dummy._auto_title_value = "Titulo Viejo"
        dummy.url_input = DummyText("https://www.youtube.com/watch?v=two&list=PL123")
        media_grabber.MediaGrabberApp.load_title_async(dummy)
        self.assertEqual(dummy.custom_name_var.get(), "")

        # Sin FFmpeg, el modo MP3 falla antes de invocar yt_dlp
        while not media_grabber.ui_queue.empty():
            media_grabber.ui_queue.get_nowait()
        with mock.patch("media_grabber.get_ffmpeg_path", return_value=None), \
             mock.patch("media_grabber.yt_dlp.YoutubeDL") as ydl_mock:
            media_grabber.run_downloads_thread([{"url": url2}], "/tmp", "mp3", "1080p (Full HD)")
        ydl_mock.assert_not_called()
        events = []
        while not media_grabber.ui_queue.empty():
            events.append(media_grabber.ui_queue.get_nowait())
        self.assertTrue(any(e.get("type") == "log" and "MP3" in e.get("text", "") for e in events))
        self.assertTrue(any(e.get("type") == "complete" and e.get("err_count") == 1 for e in events))

    def test_center_window_clamps_negative_offsets_on_small_screen(self):
        import types
        import media_grabber
        geometries = []
        fake_root = types.SimpleNamespace(
            winfo_screenwidth=lambda: 800,
            winfo_screenheight=lambda: 540,
            geometry=lambda spec: geometries.append(spec),
        )
        dummy = types.SimpleNamespace(root=fake_root)
        media_grabber.MediaGrabberApp.center_window(dummy, 1100, 720)
        self.assertEqual(geometries[-1], "760x480+20+30")

    def test_download_folder_error_double_start_lock_cancel_race_and_playlist_scroll(self):
        import types
        import tkinter as tk
        from unittest import mock
        import media_grabber

        class DummyVar:
            def __init__(self, value=""):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value

        class DummyBtn:
            def __init__(self):
                self.state = "normal"
            def configure(self, **kw):
                if "state" in kw:
                    self.state = kw["state"]

        prev_downloading = media_grabber.IS_DOWNLOADING
        prev_cancel = media_grabber.CANCEL_REQUESTED
        try:
            media_grabber.IS_DOWNLOADING = False
            media_grabber.CANCEL_REQUESTED = False
            btn_dl = DummyBtn()
            btn_cancel = DummyBtn()
            lbl_status = types.SimpleNamespace(configure=lambda **_kw: None, text="")
            errors = []
            dummy = types.SimpleNamespace(
                url_input=types.SimpleNamespace(get=lambda *_a: "https://example.com/v\n"),
                clip_enabled_var=DummyVar(False),
                resolve_download_targets=lambda urls: [{"url": u, "allow_playlist": False} for u in urls],
                folder_var=DummyVar("/ruta/inaccesible"),
                settings={},
                custom_name_var=DummyVar(""),
                fmt_var=DummyVar("mp4"),
                quality_var=DummyVar("1080p (Full HD)"),
                video_audio_var=DummyVar("with_audio"),
                progress_bar={"value": 0},
                lbl_status=lbl_status,
                clear_log=lambda: None,
                log_to_terminal=lambda *_a: None,
                btn_download=btn_dl,
                btn_cancel=btn_cancel,
            )
            with mock.patch("media_grabber.os.makedirs", side_effect=OSError("perm denied")), \
                 mock.patch("media_grabber.messagebox.showerror", side_effect=lambda *a: errors.append(a)), \
                 mock.patch("media_grabber.threading.Thread") as thread_mock:
                media_grabber.MediaGrabberApp.on_download(dummy)
            self.assertEqual(len(errors), 1)
            thread_mock.assert_not_called()
            self.assertFalse(media_grabber.IS_DOWNLOADING)

            # Con carpeta válida, on_download bloquea IS_DOWNLOADING y deshabilita botón antes de lanzar el hilo
            with tempfile.TemporaryDirectory() as folder:
                dummy.folder_var.set(folder)
                started_flags = []

                class FakeThread:
                    def __init__(self, **_kw):
                        pass
                    def start(self):
                        started_flags.append((media_grabber.IS_DOWNLOADING, btn_dl.state))

                with mock.patch("media_grabber.save_settings", side_effect=OSError("read only")), \
                     mock.patch("media_grabber.threading.Thread", FakeThread):
                    media_grabber.MediaGrabberApp.on_download(dummy)
                self.assertEqual(started_flags, [(True, "disabled")])

            # Si la descarga terminó mientras el cuadro de confirmación estaba abierto, on_cancel no activa CANCEL_REQUESTED
            def finish_while_asking(*_a):
                media_grabber.IS_DOWNLOADING = False
                return True

            media_grabber.IS_DOWNLOADING = True
            media_grabber.CANCEL_REQUESTED = False
            with mock.patch("media_grabber.messagebox.askyesno", side_effect=finish_while_asking):
                media_grabber.MediaGrabberApp.on_cancel(dummy)
            self.assertFalse(media_grabber.CANCEL_REQUESTED)

            # select_playlist_entries enlaza rueda del ratón en las casillas Checkbutton
            root = tk.Tk()
            root.withdraw()
            try:
                checked_bindings = []
                wheel_bindings = []

                def close_when_populated():
                    if checked_bindings and checked_bindings[0].bind("<Button-4>"):
                        cb = checked_bindings[0]
                        wheel_bindings.append((bool(cb.bind("<Button-4>")), bool(cb.bind("<MouseWheel>"))))
                        cb.winfo_toplevel().destroy()
                    else:
                        root.after(30, close_when_populated)

                root.after(50, close_when_populated)
                orig_checkbutton = tk.Checkbutton

                class SpyCheckbutton(orig_checkbutton):
                    def __init__(self, master=None, **kw):
                        super().__init__(master, **kw)
                        checked_bindings.append(self)

                with mock.patch("media_grabber.fetch_playlist_entries", return_value=[{"title": "Track 1", "url": "https://example.com/1"}]), \
                     mock.patch("media_grabber.tk.Checkbutton", SpyCheckbutton):
                    media_grabber.select_playlist_entries(root, "https://example.com/list")
                self.assertEqual(wheel_bindings, [(True, True)])
            finally:
                root.destroy()
        finally:
            media_grabber.IS_DOWNLOADING = prev_downloading
            media_grabber.CANCEL_REQUESTED = prev_cancel

    def test_url_scheme_validation_login_cancel_cleanup_playlist_close_and_download_exit_confirm(self):
        import threading
        import types
        import tkinter as tk
        from unittest import mock
        import media_grabber

        prev_downloading = media_grabber.IS_DOWNLOADING
        prev_cancel = media_grabber.CANCEL_REQUESTED
        try:
            # 1. Validación estricta HTTP/HTTPS insensible a mayúsculas
            self.assertTrue(media_grabber.is_valid_http_url("HTTPS://youtu.be/abc123"))
            self.assertTrue(media_grabber.is_valid_http_url("http://example.com/video"))
            self.assertFalse(media_grabber.is_valid_http_url("httpfoo"))
            self.assertFalse(media_grabber.is_valid_http_url("https://"))
            self.assertFalse(media_grabber.is_valid_http_url("ftp://example.com/file"))

            # 2. on_download acepta HTTPS en mayúsculas sin rechazarlo como inválido
            media_grabber.IS_DOWNLOADING = False
            media_grabber.CANCEL_REQUESTED = False
            errors = []
            resolved_urls = []
            dummy = types.SimpleNamespace(
                url_input=types.SimpleNamespace(get=lambda *_a: "HTTPS://example.com/v\n"),
                clip_enabled_var=types.SimpleNamespace(get=lambda: False),
                resolve_download_targets=lambda u: (resolved_urls.extend(u), None)[1],
                lbl_status=types.SimpleNamespace(configure=lambda **_kw: None),
            )
            with mock.patch("media_grabber.messagebox.showerror", side_effect=lambda *a: errors.append(a)):
                media_grabber.MediaGrabberApp.on_download(dummy)
            self.assertEqual(errors, [])
            self.assertEqual(resolved_urls, ["HTTPS://example.com/v"])

            # 3. Cancelar mientras Chrome de YouTube está abierto termina login_process
            terminated = []
            fake_proc = types.SimpleNamespace(
                poll=lambda: None,
                terminate=lambda: terminated.append(True),
            )

            class AuthYDL:
                def __init__(self, _opts):
                    pass
                def __enter__(self):
                    return self
                def __exit__(self, *_a):
                    return False
                def extract_info(self, *_a, **_kw):
                    raise RuntimeError("Sign in to confirm you're not a bot")

            def open_chrome(_url):
                media_grabber.CANCEL_REQUESTED = True
                return fake_proc

            with mock.patch("media_grabber.get_ffmpeg_path", return_value="ffmpeg"), \
                 mock.patch("media_grabber.yt_dlp.YoutubeDL", AuthYDL), \
                 mock.patch("media_grabber.youtube_profile_has_cookies", return_value=False), \
                 mock.patch("media_grabber.open_youtube_login_browser", side_effect=open_chrome):
                media_grabber.run_downloads_thread(
                    [{"url": "https://www.youtube.com/watch?v=abc"}], "/tmp", "mp4", "1080p (Full HD)"
                )
            self.assertEqual(terminated, [True])

            # 4. on_close confirma también cuando IS_DOWNLOADING está activo
            destroyed = []
            dummy_close = types.SimpleNamespace(
                _queue_after_id=None,
                editor_panel=None,
                root=types.SimpleNamespace(destroy=lambda: destroyed.append(True)),
            )
            media_grabber.IS_DOWNLOADING = True
            with mock.patch("media_grabber.messagebox.askyesno", return_value=False):
                media_grabber.MediaGrabberApp.on_close(dummy_close)
            self.assertEqual(destroyed, [])

            # 5. Cerrar select_playlist_entries antes de recibir payload no muestra errores sobre diálogo destruido
            root = tk.Tk()
            root.withdraw()
            shown_errors = []
            release_fetch = threading.Event()
            try:
                def slow_fetch(_url):
                    release_fetch.wait(timeout=1.0)
                    return []

                def close_early():
                    for win in root.winfo_children():
                        if isinstance(win, tk.Toplevel):
                            win.destroy()
                            return
                    root.after(20, close_early)

                root.after(30, close_early)
                with mock.patch("media_grabber.fetch_playlist_entries", side_effect=slow_fetch), \
                     mock.patch("media_grabber.messagebox.showerror", side_effect=lambda *a, **_k: shown_errors.append(a)):
                    res = media_grabber.select_playlist_entries(root, "https://example.com/list")
                    release_fetch.set()
                    root.update()
                self.assertIsNone(res)
                self.assertEqual(shown_errors, [])
            finally:
                release_fetch.set()
                root.destroy()
        finally:
            media_grabber.IS_DOWNLOADING = prev_downloading
            media_grabber.CANCEL_REQUESTED = prev_cancel

    def test_truncation_hostname_matching_and_resilient_check_queue(self):
        import types
        import media_grabber

        # 1. sanitize_output_name quita puntos y espacios tras truncar a 150 caracteres
        long_name = ("a" * 148) + ". ..extra"
        sanitized = sanitize_output_name(long_name)
        self.assertEqual(sanitized, "a" * 148)

        # 2. detect_platform valida el host real y no subcadenas en query/ruta
        self.assertEqual(detect_platform("https://example.com/?next=https://youtube.com/watch?v=1"), "general")
        self.assertEqual(detect_platform("https://evil-youtube.com/watch?v=1"), "general")
        self.assertEqual(detect_platform("https://m.youtube.com/watch?v=1"), "youtube")
        self.assertEqual(detect_platform("https://youtu.be/abc123"), "youtube")

        # 3. check_queue sobrevive campos None y reagenda el temporizador aunque un mensaje falle
        while not media_grabber.ui_queue.empty():
            media_grabber.ui_queue.get_nowait()
        media_grabber.ui_queue.put({"type": "active_url", "index": None, "url": None, "platform": None})
        media_grabber.ui_queue.put({"type": "log", "text": "boom", "level": "normal"})

        scheduled = []
        dummy_app = types.SimpleNamespace(
            _queue_after_id=None,
            progress_bar={"value": 0},
            lbl_status=types.SimpleNamespace(configure=lambda **_kw: None),
            btn_download=types.SimpleNamespace(configure=lambda **_kw: None),
            btn_cancel=types.SimpleNamespace(configure=lambda **_kw: None),
            log_to_terminal=lambda text, *_a: (_ for _ in ()).throw(RuntimeError("ui")) if text == "boom" else None,
            root=types.SimpleNamespace(
                winfo_exists=lambda: True,
                after=lambda ms, fn: scheduled.append((ms, fn)) or "after-1",
            ),
        )
        dummy_app.check_queue = lambda: media_grabber.MediaGrabberApp.check_queue(dummy_app)
        with self.assertRaises(RuntimeError):
            media_grabber.MediaGrabberApp.check_queue(dummy_app)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(dummy_app._queue_after_id, "after-1")

    def test_nonfinite_progress_and_complete_flag_ordering(self):
        import types
        from unittest import mock
        import media_grabber

        while not media_grabber.ui_queue.empty():
            media_grabber.ui_queue.get_nowait()

        # 1. progress_hook normaliza nan/inf y porcentajes fuera de 0..100
        media_grabber.CANCEL_REQUESTED = False
        media_grabber.progress_hook({"status": "downloading", "_percent_str": "nan%", "_speed_str": "", "_eta_str": "", "filename": "x.mp4"})
        msg_nan = media_grabber.ui_queue.get_nowait()
        self.assertEqual(msg_nan["percent"], 0.0)
        media_grabber.progress_hook({"status": "downloading", "_percent_str": "140%", "_speed_str": "", "_eta_str": "", "filename": "x.mp4"})
        msg_hi = media_grabber.ui_queue.get_nowait()
        self.assertEqual(msg_hi["percent"], 100.0)

        # 2. check_queue sanea percent NaN sin lanzar TclError en progress_bar
        media_grabber.ui_queue.put({"type": "progress", "percent": float("nan"), "percent_str": None, "speed": None, "eta": None})
        dummy_app = types.SimpleNamespace(
            _queue_after_id=None,
            progress_bar={"value": 0},
            lbl_status=types.SimpleNamespace(configure=lambda **_kw: None),
            btn_download=types.SimpleNamespace(configure=lambda **_kw: None),
            btn_cancel=types.SimpleNamespace(configure=lambda **_kw: None),
            log_to_terminal=lambda *_a: None,
            root=types.SimpleNamespace(winfo_exists=lambda: False),
        )
        media_grabber.MediaGrabberApp.check_queue(dummy_app)
        self.assertEqual(dummy_app.progress_bar["value"], 0.0)

        # 3. run_downloads_thread apaga IS_DOWNLOADING antes de enviar el mensaje complete
        observed_flags = []
        orig_put = media_grabber.ui_queue.put

        def inspect_put(item):
            if isinstance(item, dict) and item.get("type") == "complete":
                observed_flags.append(media_grabber.IS_DOWNLOADING)
            return orig_put(item)

        with mock.patch("media_grabber.get_ffmpeg_path", return_value=None), \
             mock.patch.object(media_grabber.ui_queue, "put", side_effect=inspect_put):
            media_grabber.run_downloads_thread([], ".", "mp4", "1080p (Full HD)")
        self.assertEqual(observed_flags, [False])

    def test_save_settings_relative_path(self):
        import os
        import tempfile
        import media_grabber

        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.chdir(tmpdir)
                media_grabber.save_settings("settings.json", {"ok": True})
                self.assertEqual(media_grabber.load_settings("settings.json"), {"ok": True})
            finally:
                os.chdir(cwd)

    def test_non_dict_entries_in_playlist_and_has_audio(self):
        import media_grabber

        entries = media_grabber.playlist_entries_from_info({"entries": ["bad", None, {"id": "abc", "title": "Song"}]})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Song")
        self.assertTrue(media_grabber.has_audio({"entries": ["bad", {"acodec": "aac"}]}))
        self.assertFalse(media_grabber.has_audio({"requested_formats": [None, {"acodec": "none"}]}))

    def test_whitespace_url_rejection_and_stale_title_reset(self):
        import tempfile
        import types
        from unittest import mock
        import media_grabber

        self.assertFalse(media_grabber.is_valid_http_url("https://youtube.com/watch?v=1 https://youtube.com/watch?v=2"))
        self.assertFalse(media_grabber.is_valid_http_url("https://youtube.com/watch?v=1\t2"))

        prev_downloading = media_grabber.IS_DOWNLOADING
        prev_cancel = media_grabber.CANCEL_REQUESTED
        try:
            with tempfile.TemporaryDirectory() as tmp:
                media_grabber.IS_DOWNLOADING = False
                media_grabber.CANCEL_REQUESTED = False
                spawned = {}

                class DummyVar:
                    def __init__(self, val):
                        self._val = val
                    def get(self):
                        return self._val
                    def set(self, val):
                        self._val = val

                class DummyThread:
                    def __init__(self, target=None, args=(), daemon=False):
                        spawned["targets"] = args[0]
                    def start(self):
                        pass

                dummy = types.SimpleNamespace(
                    url_input=types.SimpleNamespace(get=lambda *_a: "https://example.com/new\n"),
                    clip_enabled_var=types.SimpleNamespace(get=lambda: False),
                    resolve_download_targets=lambda u: [{"url": u[0], "allow_playlist": False}],
                    folder_var=DummyVar(tmp),
                    settings={},
                    custom_name_var=DummyVar("Obteniendo título…"),
                    _auto_title_value=None,
                    title_lookup_url="https://example.com/old",
                    fmt_var=DummyVar("mp4"),
                    quality_var=DummyVar("1080p (Full HD)"),
                    video_audio_var=DummyVar("with_audio"),
                    progress_bar={"value": 0},
                    lbl_status=types.SimpleNamespace(configure=lambda **_kw: None),
                    clear_log=lambda: None,
                    log_to_terminal=lambda *_a: None,
                    btn_download=types.SimpleNamespace(configure=lambda **_kw: None),
                )
                with mock.patch("media_grabber.threading.Thread", DummyThread), \
                     mock.patch("media_grabber.save_settings"):
                    media_grabber.MediaGrabberApp.on_download(dummy)
                self.assertIsNone(dummy.title_lookup_url)
                self.assertEqual(dummy.custom_name_var.get(), "")
                self.assertNotIn("custom_name", spawned["targets"][0])
        finally:
            media_grabber.IS_DOWNLOADING = prev_downloading
            media_grabber.CANCEL_REQUESTED = prev_cancel

    def test_ronda27_cancel_during_retry_empty_host_reserved_dot_and_playlist_entries(self):
        from unittest import mock
        import media_grabber

        self.assertFalse(media_grabber.is_valid_http_url("https://:8080/video"))
        self.assertFalse(media_grabber.is_valid_http_url("https://user@/"))
        self.assertEqual(media_grabber.sanitize_output_name("CON.foo"), "")
        self.assertEqual(media_grabber.sanitize_output_name("NUL.tar.gz"), "")
        self.assertEqual(media_grabber.sanitize_output_name("Mi.Video"), "Mi.Video")

        self.assertEqual(media_grabber.playlist_entries_from_info({"entries": {"bad": 1}}), [])
        entries = media_grabber.playlist_entries_from_info({
            "entries": [{"title": "Track", "url": "https://soundcloud.com/artist/track"}]
        })
        self.assertEqual(entries, [{"title": "Track", "url": "https://soundcloud.com/artist/track"}])

        # Cancel requested while an attempt fails prevents browser retries on Instagram
        prev_cancel = media_grabber.CANCEL_REQUESTED
        attempts_called = []

        class FailingYDL:
            def __init__(self, opts):
                attempts_called.append(opts.get("cookiesfrombrowser"))
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def extract_info(self, url, download=True):
                media_grabber.CANCEL_REQUESTED = True
                raise RuntimeError("fallo temporal")

        try:
            while not media_grabber.ui_queue.empty():
                media_grabber.ui_queue.get_nowait()
            media_grabber.CANCEL_REQUESTED = False
            with mock.patch("media_grabber.get_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
                 mock.patch("media_grabber.yt_dlp.YoutubeDL", FailingYDL):
                media_grabber.run_downloads_thread(
                    [{"url": "https://www.instagram.com/reel/abc/", "allow_playlist": False}],
                    ".", "mp4", "1080p (Full HD)",
                )
            self.assertEqual(len(attempts_called), 1)
            msgs = []
            while not media_grabber.ui_queue.empty():
                msgs.append(media_grabber.ui_queue.get_nowait())
            complete = [m for m in msgs if isinstance(m, dict) and m.get("type") == "complete"]
            self.assertEqual(len(complete), 1)
            self.assertTrue(complete[0]["cancelled"])
        finally:
            media_grabber.CANCEL_REQUESTED = prev_cancel

    def test_ronda28_full_playlist_skips_custom_name_and_unsupported_skips_title_lookup(self):
        import tempfile
        import types
        from unittest import mock
        import media_grabber

        class DummyVar:
            def __init__(self, val):
                self._val = val
            def get(self):
                return self._val
            def set(self, val):
                self._val = val

        # 1. load_title_async omite URLs no compatibles (p.ej. Spotify) y limpia estado previo
        dummy_spotify = types.SimpleNamespace(
            url_input=types.SimpleNamespace(get=lambda *_a: "https://open.spotify.com/track/123\n"),
            custom_name_var=DummyVar("Obteniendo título…"),
            _auto_title_value=None,
            title_lookup_url="https://example.com/old",
        )
        with mock.patch("media_grabber.threading.Thread") as mocked_thread:
            media_grabber.MediaGrabberApp.load_title_async(dummy_spotify)
        mocked_thread.assert_not_called()
        self.assertIsNone(dummy_spotify.title_lookup_url)
        self.assertEqual(dummy_spotify.custom_name_var.get(), "")

        # 2. on_download no fuerza custom_name único cuando allow_playlist=True
        prev_downloading = media_grabber.IS_DOWNLOADING
        prev_cancel = media_grabber.CANCEL_REQUESTED
        try:
            with tempfile.TemporaryDirectory() as tmp:
                media_grabber.IS_DOWNLOADING = False
                media_grabber.CANCEL_REQUESTED = False
                spawned = {}

                class DummyThread:
                    def __init__(self, target=None, args=(), daemon=False):
                        spawned["targets"] = args[0]
                    def start(self):
                        pass

                dummy = types.SimpleNamespace(
                    url_input=types.SimpleNamespace(get=lambda *_a: "https://www.youtube.com/playlist?list=PL123\n"),
                    clip_enabled_var=types.SimpleNamespace(get=lambda: False),
                    resolve_download_targets=lambda u: [{"url": u[0], "allow_playlist": True}],
                    folder_var=DummyVar(tmp),
                    settings={},
                    custom_name_var=DummyVar("Mi Playlist"),
                    _auto_title_value=None,
                    title_lookup_url=None,
                    fmt_var=DummyVar("mp4"),
                    quality_var=DummyVar("1080p (Full HD)"),
                    video_audio_var=DummyVar("with_audio"),
                    progress_bar={"value": 0},
                    lbl_status=types.SimpleNamespace(configure=lambda **_kw: None),
                    clear_log=lambda: None,
                    log_to_terminal=lambda *_a: None,
                    btn_download=types.SimpleNamespace(configure=lambda **_kw: None),
                )
                with mock.patch("media_grabber.threading.Thread", DummyThread), \
                     mock.patch("media_grabber.save_settings"):
                    media_grabber.MediaGrabberApp.on_download(dummy)
                self.assertNotIn("custom_name", spawned["targets"][0])
        finally:
            media_grabber.IS_DOWNLOADING = prev_downloading
            media_grabber.CANCEL_REQUESTED = prev_cancel


if __name__ == "__main__":
    unittest.main()

