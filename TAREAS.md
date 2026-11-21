# TAREAS.md — Estado y registro de rondas

> **Para conversaciones nuevas:** lee **solo hasta la línea `HISTORIAL ANTIGUO`** (aprox. las primeras 75 líneas). Debajo se guarda el índice histórico de rondas anteriores para no cargar contexto innecesario.

## Estado actual
- **Rama activa:** `main` (Rondas 1–32 consolidadas · versión `v1.0.1`).
- **Última ronda completada:** `v1.0.1` — selector deslizante `🎬 Vídeo` / `🎵 MP3` con opciones de vídeo ocultables en [media_grabber.py](media_grabber.py).
- **Tests:** **190 tests en verde** (`unittest` bajo `xvfb-run`) y `pyflakes` limpio en los 5 módulos de producción.
- **CI Windows (`Compilar para Windows`):** ejecución `#44469674403` (`success`, artefactos `AveDescargasEditor-exe` y `AveDescargasEditor-portable`).

## Pendientes / Próximas tareas
- [ ] Nuevas funcionalidades o mejoras que solicite el usuario en próximas conversaciones.

---

## Últimas rondas (más reciente arriba)

- **Ronda 32 — Audio en separación vinculada, Spinbox al insertar/soltar y duración mínima 0.01 s**
  - [`simple_editor_panel.py`](simple_editor_panel.py): `toggle_linked_media` conserva `use_source_audio=clip.use_source_audio` al separar audio de un vídeo silenciado; `_drop_library_media` e `_insert_selected_media` llaman a `_commit_inspector_edits()` antes de cambiar el clip seleccionado.
  - [`editor_panel.py`](editor_panel.py): `EditorPanel.apply_clip_properties` usa `MIN_CLIP_DURATION - 1e-9` para no rechazar clips válidos de `0.01` s por precisión IEEE 754 (p. ej. `10.01 - 10.0`).
  - Tests: `test_ronda32_toggle_linked_preserves_mute_library_insert_commits_inspector_and_min_duration` ([`test_editor_panel_gui.py`](test_editor_panel_gui.py)).

- **Ronda 31 (`890bdef`) — Biblioteca en deshacer/rehacer, inspector y estado de importación**
  - [`simple_editor_panel.py`](simple_editor_panel.py): `_snapshot`/`_restore` incluyen `media_library` y `_missing_media_paths` reconstruyendo `media_list` con `_sync_media_listbox`; `remove_selected_library_media` registra `_push_undo()`; `_update_inspector` restablece `inspector_audio_btn` al deseleccionar clip.
  - [`editor_panel.py`](editor_panel.py): `EditorPanel.import_media` informa fallos totales o parciales al sondear medios.
  - Tests: `test_ronda31_snapshot_restores_media_library_inspector_audio_reset_and_editor_import_status` ([`test_editor_panel_gui.py`](test_editor_panel_gui.py)).

- **Ronda 30 (`f26d968`) — Cortes vinculados, fades en preview, audio audible y fin de reproducción**
  - [`simple_editor_panel.py`](simple_editor_panel.py): `split_at_playhead` y `cut_selected_clip` evitan `link_group` huérfanos cuando la pareja no cubre el corte (`cut_selected_clip` refresca preview); `_preview_fade_filters` acota `rem_in`/`rem_out` a `duration - st`; `_clip_at_playhead` prioriza audio audible frente a pistas mudas; `_poll_queue` (`play_done`) actualiza el cabezal al terminar el último clip.
  - Tests: `test_ronda30_split_link_group_fade_clamp_clip_at_playhead_and_play_done` ([`test_editor_panel_gui.py`](test_editor_panel_gui.py)).

- **Ronda 29 (`d1c7a58`) — Pistas de audio, visor y deshacer pistas auto-creadas**
  - [`simple_editor_panel.py`](simple_editor_panel.py): `_drop_library_media` aplica `audio_media_from_video` al soltar en pista de audio; `_play_audio_only` limpia `preview_photo` previo; `paste_clip` y `_insert_selected_media` graban `_push_undo()` antes de auto-crear pista y usan `_request_clip_or_missing_preview`.
  - Tests: `test_ronda29_drop_audio_strips_video_audio_playback_clears_frame_and_paste_undo` ([`test_editor_panel_gui.py`](test_editor_panel_gui.py)).

- **Ronda 28 (`8f8311a`) — Playlists completas y URLs incompatibles**
  - [`media_grabber.py`](media_grabber.py): `on_download` evita forzar un `custom_name` único en `"Toda la playlist"` (`allow_playlist=True`); `load_title_async` omite búsqueda de título en URLs incompatibles (p. ej. Spotify DRM) y limpia estado previo.
  - Tests: `test_ronda28_full_playlist_skips_custom_name_and_unsupported_skips_title_lookup` ([`test_media_grabber.py`](test_media_grabber.py)).

- **Ronda 27 (`65bce79`) — Cancelación en reintentos, validación de URLs y nombres en Windows**
  - [`media_grabber.py`](media_grabber.py): `run_downloads_thread` respeta `CANCEL_REQUESTED` antes/durante/después de reintentos y antes de abrir login; `is_valid_http_url` exige `parsed.hostname`; `sanitize_output_name` bloquea reservados con extensión (`CON.foo`); `playlist_entries_from_info` valida tipo de `entries` y usa `entry["url"]` HTTP si falta `webpage_url`/`id`.
  - Tests: `test_ronda27_cancel_during_retry_empty_host_reserved_dot_and_playlist_entries` ([`test_media_grabber.py`](test_media_grabber.py)).

- **Ronda 26 (`dc8a463`) — Cortes no finitos, redondeo de fades y streams vacíos**
  - [`media_editor.py`](media_editor.py): `timeline_to_source_time` y `split_clip` rechazan `NaN`/`Inf`; `clamp_fades` corrige exceso tras redondeo; `probe_media` exige vídeo o audio; `project_from_dict` impide `has_video=True` en pistas `audio`.
  - Tests: `test_ronda26_nonfinite_cut_clamp_fades_rounding_and_streamless_probe` ([`test_media_editor.py`](test_media_editor.py)).

---

<!-- HISTORIAL ANTIGUO: las conversaciones nuevas NO necesitan leer debajo de esta línea salvo investigación puntual -->

## Índice histórico (Rondas 1–25)
- `5d33345` Ronda 25: validar URLs sin espacios internos, limpiar título obsoleto y omitir pistas mudas al encadenar
- `39f254a` Ronda 24: confirmar spinbox antes de editar/reproducir, refrescar restaurados y proteger close_gap_at
- `9d5fb47` Ronda 23: proteger probe_media ante JSON nulo y sanear NaN en fades y snap
- `62b2ad2` Ronda 22: identificar clips por identidad y validar zoom no finito
- `9969611` Ronda 21: rechaza partes negativas en tiempos, sanea NaN/Inf en ondas, blinda entradas de playlist y sincroniza audio revinculado
- `d019179` Ronda 20: exige duración mínima en Clip, omite fades sub-milisegundo y rechaza booleanos en JSON
- `e1051af` Ronda 19: ajusta cabezal al borrar clips, refresca visor al soltar medios y evita nombres repetidos en EditorPanel
- `7caa8ff` Ronda 18: evita :60.00 al redondear tiempos, IndexError en waveform_segment y save_settings sin carpeta
- `716d602` Ronda 17: sanea porcentajes NaN/Inf, wraplength de playlist y orden de IS_DOWNLOADING
- `660e553` Ronda 16: previsualización de solo audio y validación de duración/fundidos en EditorPanel
- `95da649` Ronda 15: corrige reinicio de rango en playback_range, sincronía de cabezal al recortar y visor en archivos ausentes
- `f19f1e3` Ronda 14: sanitiza nombres tras truncar, valida host real y blinda diálogos/cola UI
- `eaf5f57` Ronda 13: transparencia en pistas superiores sin fade, validación finita en Clip, timeout en ffprobe y OverflowError en carga
- `6639107` Ronda 12: corrige límites de audio, carga durante exportación, borrado de pista/librería, captura y reproducción corta
- `0d49f0b` Ronda 11: corrige EditorPanel alternativo (pistas, división, clics en texto y exportación)
- `eb5189f` Ronda 10: valida esquema HTTP/HTTPS, limpia login Chrome al cancelar y protege cierre de playlist
- `1733f7a` Ronda 9: blinda _poll_queue ante fotogramas corruptos y limpia visor en clips de audio
- `1353a0b` Ronda 8: ignora carátulas attached_pic, valida valores finitos y extensiones de exportación
- `0f10b5f` Ronda 7: selecciona tirador cercano en clips cortos, link_group único y selección de pista
- `249dfe9` Ronda 6: bloqueo de doble clic al descargar, control de OSError en carpeta, carrera de cancelación y scroll de lista
- `9f61455` Ronda 5: refrescar visor en huecos y audio, confirmar inspector al operar y limpiar v_proc/estado de arrastre
- `915ce86` Ronda 4: remuestreo previo al tono, códecs y formatos de exportación, fps seguro y validación/guardado atómico .avep
- `c38aa2e` Ronda 3: fundidos alfa en capas superiores, limpieza de ffmpeg abortado, recorte vinculado bidireccional y cierre seguro de colas
- `876b5d8` Ronda 2: evitar fuga de audio al mutear pista vinculada, proteger Popen de audio, limpiar atajos y centrar/desplazar ventana en pantallas escaladas
- `54eb2a2` Ronda 1: evitar colisiones link_group al cortar, guardar cambios de spinbox antes de cambiar de clip, validar .avep/ffprobe y parar preview al exportar/capturar
