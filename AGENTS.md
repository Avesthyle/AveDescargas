# AGENTS.md — Guía del proyecto AveDescargas

> **Idioma obligatorio:** responde siempre al usuario en **español**, con enlaces clicables `file://` a archivos y símbolos modificados.

Este archivo contiene las instrucciones operativas para cualquier sesión nueva sobre **AveDescargas** (descargador de vídeo/audio + editor multipista para Windows).

---

## 1. Protocolo de arranque en cada conversación nueva

Antes de proponer o editar código, sigue exactamente estos pasos:

1. **Comprobar rama y estado Git:**
   - Ejecuta `git status -sb` y `git branch -a`.
   - Todo el trabajo acumulado hasta la **Ronda 31** vive en `feature/timeline-magnetica` (PR #1, commit `890bdef`).
   - Si el entorno clonó `main` y el PR #1 aún **no** ha sido mergeado, cámbiate primero a `feature/timeline-magnetica` (o crea una rama nueva desde ella si el usuario lo pide).
2. **Leer solo la parte reciente de [`TAREAS.md`](TAREAS.md):**
   - **Lee únicamente las primeras ~90 líneas** de [`TAREAS.md`](TAREAS.md) (*Estado actual*, *Pendientes* y *Últimas rondas*).
   - **No cargues ni resumas el archivo histórico inferior** salvo que necesites investigar un bug antiguo concreto; usa `git log -n 10 --oneline` como complemento rápido.
3. **Preparar y verificar el entorno de pruebas (Linux VM):**
   - En esta VM (`/usr/bin/python3` no trae las dependencias del proyecto), comprueba primero si existe `/home/antigravity/.venv-ave/bin/python`.
   - Si no existe un entorno con dependencias en esta VM nueva, créalo en espacio de usuario (nunca con `sudo`/`apt`):
     - `python3 -m venv ~/.venv-ave`
     - `~/.venv-ave/bin/pip install -r requirements.txt pyflakes`
     - Asegúrate de que `python3-tk` / `ImageTk` y `ffmpeg` / `xvfb-run` están disponibles en el sistema (`which ffmpeg ffprobe ffplay xvfb-run`).
4. **Ejecutar línea base antes de editar:**
   ```bash
   ~/.venv-ave/bin/pyflakes media_editor.py simple_editor_panel.py editor_panel.py media_grabber.py media_grabber_editor.py
   xvfb-run -a ~/.venv-ave/bin/python -m unittest discover -s . -p "test_*.py" -v
   ```
   - Nota: ejecuta `pyflakes` sobre los **5 módulos de producción** indicados arriba (los archivos `test_*.py` pueden tener imports auxiliares heredados).

---

## 2. Arquitectura del repositorio

| Archivo | Responsabilidad |
|---|---|
| `media_grabber_editor.py` | Punto de entrada principal del ejecutable Windows (`AveDescargasEditor.exe`). Inyecta `SimpleEditorPanel` en `media_grabber.EditorPanel` y lanza `TkinterDnD.Tk()` si está disponible. |
| `media_grabber.py` | Interfaz principal de descarga (`yt-dlp`), detección de playlists, descarga de fragmentos, validación de URLs/nombres, reintentos autenticados en Instagram/YouTube, cola UI (`ui_queue`) y pestaña embebida del editor. |
| `media_editor.py` | Motor puro sin Tkinter: dataclasses (`MediaInfo`, `Clip`, `Track`, `EditorProject`), análisis `ffprobe`, generación de planes `ffmpeg` (`build_export_plan`), `timeline_to_source_time`, `split_clip`, `clamp_fades`, snapping, edición magnética (`compact_clips`, `close_gap`, `ripple_delete`) y serialización atómica `.avep` (`project_from_dict` / `save_project` / `load_project`). |
| `simple_editor_panel.py` | Editor visual completo: visor con fotogramas/previsualización FFmpeg+FFplay, biblioteca de medios con drag & drop, inspector de clip (volumen, fades, tono, audio original, desvincular), timeline multipista con imán, pista principal magnética, ondas de audio y historial deshacer/rehacer (`_snapshot` / `_restore`). |
| `editor_panel.py` | Editor multipista clásico alternativo (`EditorPanel`, Treeviews + timeline básica). |
| `AveDescargasEditor.spec` | Especificación de PyInstaller para Windows (`assets/`, `tkinterdnd2`, submódulos de `yt_dlp.extractor`). |
| `.github/workflows/build-windows.yml` | CI oficial `Compilar para Windows`: instala dependencias, descarga FFmpeg portable, ejecuta los 4 archivos de tests, compila `.exe`, prueba arranque 15 s y sube `AveDescargasEditor-exe` y `AveDescargasEditor-portable`. |
| `test_*.py` | Suites de regresión (`test_magnetic_timeline.py`, `test_media_editor.py`, `test_media_grabber.py`, `test_editor_panel_gui.py`). **188 tests** pasando tras la Ronda 31. |

---

## 3. Reglas e invariantes críticas (NO romper lo ya arreglado)

A lo largo de 31 rondas se resolvieron fallos sutiles. Respeta estas invariantes siempre que edites:

### `media_editor.py` (Núcleo y exportación)
1. **Validación numérica estricta:** cualquier entrada temporal o numérica (`timeline_to_source_time`, `split_clip`, `clamp_fades`, `avoid_clip_overlap`, `snap_*`, `zoom`, `Clip`, `MediaInfo`) debe rechazar o sanear `NaN`, `Inf` y valores fuera de rango. No uses `round()` sin corregir luego que `fade_in + fade_out <= duration`.
2. **`probe_media`:** ignora carátulas embebidas (`attached_pic`), maneja `streams`/`format` nulos, aplica timeout de 30 s, y **lanza `ValueError`** si no existe ninguna pista de vídeo ni de audio o la duración no es finita > 0.
3. **JSON `.avep` (`project_from_dict`):** rechaza `bool` donde se esperan `int`/`float` (`media_idx`, `pitch_semitones`, `width`, `height`, `selected_track_index`), captura `OverflowError` y **bloquea `clip_has_video=True`** si `track.kind == "audio"`.
4. **Exportación y filtros FFmpeg:**
   - Mantén dimensiones pares para `yuv420p` y usa `yuva420p` + `format=rgba/yuva420p` en overlays de capas superiores para respetar transparencia (incluso sin fades).
   - Normaliza el audio antes de aplicar `asetrate`/cambio de tono y valida todas las extensiones soportadas (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, `.ogg`).
   - En `ripple_delete` / operaciones sobre listas de clips con valores iguales, identifica el clip por **identidad (`is`)**, nunca solo por igualdad (`==`).

### `simple_editor_panel.py` y `editor_panel.py` (GUI multipista)
1. **Controles antes de operar (`Spinbox`):** llama a `self._commit_inspector_edits()` antes de dividir, copiar, duplicar, exportar, reproducir o cambiar de clip para no perder cambios escritos en volumen/fades sin pulsar Enter.
2. **Clips en pistas de audio:** al soltar un vídeo en pista de audio (`_drop_library_media`) o recargar proyectos, convierte `Clip.media` con `audio_media_from_video(media)` para que no conserve `has_video=True`.
3. **Vínculos (`link_group`):**
   - Genera IDs únicos con `_next_link_group(...)`.
   - Al dividir (`split_at_playhead` o `cut_selected_clip`), **no** asignes `link_group` al fragmento derecho si ninguna pareja vinculada se dividió también en ese punto.
   - Al mutear una pista vinculada o desactivar audio, respeta el estado de cada pista/clip en `_linked_audio_source`, `_clip_at_playhead` (prioriza audio audible sobre pistas mudas) y `_next_clip_after`.
   - Al borrar pistas o clips vinculados (`delete_track`, `delete_clip`, `ripple_delete_clip`), limpia `link_group` huérfanos y protege `close_gap_at` frente a conflictos con enlaces bloqueados.
4. **Visor y reproducción:**
   - Detén procesos previos (`stop_playback` / `_terminate_processes`) antes de exportar, capturar, deshacer, saltar o cerrar.
   - Limpia `preview_photo` y muestra título/estado cuando cambies a un hueco, un clip solo audio (`_play_audio_only`) o un archivo ausente (`_request_clip_or_missing_preview`).
   - En `_preview_fade_filters`, acota `rem_in` y `rem_out` a `duration - st`.
   - En `_poll_queue` al terminar (`play_done`) el último clip en modo timeline, avanza `cursor_time` y `playhead_timeline` al final del tramo y actualiza código de tiempo/canvas.
5. **Deshacer / Rehacer (`_snapshot` / `_restore`):**
   - El snapshot incluye `(tracks, selected_track_index, track_heights, media_library, _missing_media_paths)` (manteniendo compatibilidad al leer 3-tuplas en `_restore`).
   - Llama a `self._push_undo()` **antes** de auto-crear una pista faltante (`paste_clip`, `_insert_selected_media`) o quitar de biblioteca (`remove_selected_library_media`).
   - Al deseleccionar un clip en `_update_inspector()`, restablece `inspector_audio_btn` a `"Audio original ON/OFF"`.
6. **`EditorPanel.import_media`:** informa correctamente si fallaron todos o parte de los archivos al sondear, sin mostrar `"Medios importados."` en verde si `imported == 0`.
7. **Timers y cierre:** cancela siempre `_poll_after_id` y `_frame_after_id` en `destroy()` y evita referencias fuertes desde hilos hacia `self` (usa `weakref.ref(self)`).

### `media_grabber.py` (Descargador)
1. **Validación de URLs y nombres:**
   - `is_valid_http_url`: exige esquema `http`/`https`, sin espacios internos y con `parsed.hostname` real (no solo `netloc`).
   - `sanitize_output_name`: limpia caracteres inválidos, trunca a 150, elimina `. ` finales y bloquea nombres reservados de Windows **incluso con extensión** (`CON.foo`, `NUL.bar`).
   - `unsupported_download_reason` (ej. Spotify DRM): rechaza la descarga y **evita** lanzar `load_title_async`.
2. **Playlists y títulos:**
   - `playlist_entries_from_info`: tolera `entries` nulos, strings/dicts malformados o entradas sin `webpage_url` usando `url` HTTP/HTTPS cuando aplique.
   - En `on_download`, **no** asignes un único `custom_name` cuando `allow_playlist=True` (descarga `"Toda la playlist"`).
   - Limpia `"Obteniendo título…"` y `_auto_title_value` obsoleto cuando cambia la URL o se descarga en lote.
3. **Concurrencia, cancelación y UI:**
   - Bloquea doble clic con `IS_DOWNLOADING` y protégelo siempre con `try/finally` antes de emitir `'complete'`.
   - Comprueba `CANCEL_REQUESTED` al entrar en cada intento de navegador, tras `extract_info`, al fallar un intento y antes de abrir Chrome para login de YouTube (terminando el subproceso si se cancela).
   - Sanea porcentajes `NaN`/`Inf` en `progress_hook` y `check_queue`.
   - `save_settings` crea carpetas padre solo si no es vacío y escribe atómicamente.

---

## 4. Flujo de trabajo por cada cambio o ronda

1. **Solo cambios con fundamento:** si el usuario pide "más rondas de fallos", corrige únicamente **bugs reales y verificables**. Si ya no quedan fallos genuinos, dilo con transparencia y detente sin inventar cambios cosméticos.
2. **Pruebas obligatorias:** por cada cambio en código, añade/actualiza tests en `test_*.py` y verifica que pasan **todos** los tests + `pyflakes`.
3. **Actualizar [`TAREAS.md`](TAREAS.md):**
   - Añade arriba del todo la nueva ronda (en *Últimas rondas*) y actualiza el *Estado actual*.
   - Si en *Últimas rondas* se acumulan más de **8 rondas**, mueve las más antiguas debajo de la línea separadora `<!-- HISTORIAL ANTIGUO -->` para mantener ligero el arranque de futuras conversaciones.
4. **Git y CI en Windows:**
   - Haz `git commit` con mensaje claro en español (`Ronda N: ...` o descripción concreta) y `git push`.
   - Comprueba la ejecución en GitHub Actions (`gh run list` y `gh run watch <id> --exit-status`) para confirmar que `Compilar para Windows` termina en `success` con los artefactos `.exe` y `.zip`.
5. **Formato de respuesta:** breve, en español, reportando únicamente resultados realmente ejecutados (número exacto de tests, SHA del commit e ID de CI) y enlaces `file://`.
