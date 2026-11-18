# -*- coding: utf-8 -*-
"""AveDescargas — Descargador Universal de Medios
Permite descargar videos y audios de YouTube, Instagram y Facebook de forma profesional.
Uso: python media_grabber.py
"""

import os
import sys
import re
import json
import math
import subprocess
import threading
import ctypes
import queue
import shutil
import time
import tkinter as tk
from urllib.parse import parse_qs, urlparse
from tkinter import ttk, filedialog, messagebox, scrolledtext
import yt_dlp
from yt_dlp.utils import download_range_func
try:
    from simple_editor_panel import SimpleEditorPanel as EditorPanel
except Exception:
    from editor_panel import EditorPanel

# --- DWM Immersive Dark Mode for Windows 10/11 ---
def set_dark_title_bar(window):
    """Aplica el tema oscuro a la barra de título estándar de Windows 10/11."""
    try:
        window.update()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 es para Windows 11/10 20H1+, 19 para versiones anteriores
        rendering_policy = ctypes.c_int(2)
        for attribute in [20, 19]:
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(rendering_policy), ctypes.sizeof(rendering_policy)
            )
            if result == 0:
                break
    except Exception:
        pass

# --- FFmpeg Discovery ---
def get_ffmpeg_path():
    """Detecta la ubicación de FFmpeg en ubicaciones comunes o en el PATH de Windows."""
    app_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    FFMPEG_LOCATIONS = [
        os.path.join(app_dir, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(app_dir, "ffmpeg", "ffmpeg.exe"),
        os.path.join(app_dir, "ffmpeg.exe"),
        "C:\\ffmpeg_shared4\\ffmpeg-master-latest-win64-gpl-shared\\bin\\ffmpeg.exe",
        "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
    ]
    for loc in FFMPEG_LOCATIONS:
        if os.path.exists(loc):
            return loc
    # Fallback to shutil which looks at PATH
    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return path_ffmpeg
    return None

# --- Constants & State ---
DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads", "MediaGrabber")
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MediaGrabber", "settings.json")
YOUTUBE_CHROME_PROFILE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MediaGrabber", "ChromeProfile")
IS_DOWNLOADING = False
CANCEL_REQUESTED = False
ui_queue = queue.Queue()

VIDEO_QUALITIES = {
    "Máxima disponible": None,
    "1080p (Full HD)": 1080,
    "720p (HD)": 720,
    "480p": 480,
    "360p": 360,
}


def load_settings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_settings(path: str, settings: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def preferred_download_folder(settings: dict, default: str) -> str:
    saved = settings.get("download_folder") if isinstance(settings, dict) else None
    return saved if isinstance(saved, str) and os.path.isdir(saved) else default


def sanitize_output_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (value or "")).strip()[:150].rstrip(". ")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if not cleaned or cleaned.upper() in reserved:
        return ""
    return cleaned


def is_youtube_auth_challenge(error) -> bool:
    text = str(error).lower().replace("’", "'")
    markers = (
        "sign in to confirm you're not a bot",
        "sign in to confirm you’re not a bot",
        "sign in to confirm your age",
        "this video is age-restricted",
        "cookies-from-browser",
        "for the authentication",
    )
    return any(marker in text for marker in markers)


def chrome_is_running_from_tasklist(output: str) -> bool:
    return "chrome.exe" in (output or "").lower()


def chrome_is_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return chrome_is_running_from_tasklist(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return False


def chrome_cookie_options(profile_path: str = YOUTUBE_CHROME_PROFILE) -> dict:
    return {"cookiesfrombrowser": ("chrome", profile_path)}


def authenticated_retry_options(base_options: dict, browser: str, platform: str,
                                profile_path: str = YOUTUBE_CHROME_PROFILE) -> dict:
    options = dict(base_options)
    if platform == "youtube" and browser.startswith("chrome"):
        # Los clientes móviles (especialmente Android) no aceptan cookies.
        options.pop("extractor_args", None)
        options.update(chrome_cookie_options(profile_path))
    else:
        options["cookiesfrombrowser"] = (browser,)
    return options


def youtube_profile_has_cookies(profile_path: str = YOUTUBE_CHROME_PROFILE) -> bool:
    cookie_file = os.path.join(profile_path, "Default", "Network", "Cookies")
    try:
        return os.path.isfile(cookie_file) and os.path.getsize(cookie_file) > 0
    except OSError:
        return False


def find_chrome_executable():
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


def open_youtube_login_browser(url: str = "https://www.youtube.com/",
                               profile_path: str = YOUTUBE_CHROME_PROFILE):
    executable = find_chrome_executable()
    if not executable:
        return None
    os.makedirs(profile_path, exist_ok=True)
    try:
        return subprocess.Popen([
            executable,
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            url,
        ])
    except OSError:
        return None


def close_chrome_gracefully(timeout_seconds: int = 15) -> bool:
    if not chrome_is_running():
        return True
    try:
        subprocess.run(
            ["taskkill", "/IM", "chrome.exe", "/T"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not chrome_is_running():
            return True
        time.sleep(0.5)
    return False


def reopen_chrome() -> bool:
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    executable = next((path for path in candidates if path and os.path.isfile(path)), None)
    if not executable:
        return False
    try:
        subprocess.Popen([executable], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False


def resource_path(relative_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def build_video_format(quality_label: str, include_audio: bool = True) -> str:
    """Selecciona vídeo dentro de la calidad indicada, con audio o sin él."""
    max_height = VIDEO_QUALITIES.get(quality_label)
    height_filter = f"[height<={max_height}]" if max_height else ""
    if not include_audio:
        options = []
        if height_filter:
            options += [
                f"bestvideo{height_filter}[ext=mp4][acodec=none]",
                f"bestvideo{height_filter}[acodec=none]",
            ]
        options += [
            "bestvideo[ext=mp4][acodec=none]",
            "bestvideo[acodec=none]",
        ]
        return "/".join(options)

    options = []
    if height_filter:
        options += [
            f"bestvideo{height_filter}+bestaudio",
            f"best{height_filter}[vcodec!=none][acodec!=none]",
        ]
    # Respaldo: máxima calidad disponible (Instagram a veces no informa códecs/altura)
    options += [
        "bestvideo*+bestaudio",
        "best[vcodec!=none][acodec!=none]",
        "best[acodec!=none]",
        # Último recurso: la publicación no tiene audio -> solo vídeo
        "bestvideo*",
        "best",
    ]
    return "/".join(options)


def video_audio_description(include_audio: bool) -> str:
    return "Con sonido" if include_audio else "Sin sonido"


def heavy_clip_notice(target: dict, video_quality: str):
    """Avisa de una posible espera sin alterar la descarga solicitada."""
    if target.get("section_start") is None or VIDEO_QUALITIES.get(video_quality) != 1080:
        return None
    return (
        "⏳ Aviso: este fragmento se descargará en 1080p y puede tardar si el vídeo "
        "tiene muchos fotogramas o un tamaño elevado. No se está descargando el vídeo "
        "completo; solo el intervalo seleccionado."
    )


def youtube_extractor_options(fmt: str, include_audio: bool) -> dict:
    """Usa el cliente normal cuando hacen falta formatos de vídeo separado."""
    if fmt == "mp4" and not include_audio:
        return {}
    return {
        'extractor_args': {
            'youtube': {
                'player_client': ['android']
            }
        }
    }

def has_audio(info) -> bool:
    """Indica si los formatos elegidos por yt-dlp incluyen pista de audio."""
    if not info:
        return True
    if info.get("entries"):
        return all(has_audio(e) for e in info["entries"] if e)
    formats = info.get("requested_formats") or [info]
    return any(f.get("acodec") != "none" for f in formats)

# --- Custom Exception for Cancellation ---
class DownloadCancelledException(Exception):
    pass

# --- Helpers ---
def is_valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def detect_platform(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return "general"
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return "general"

    def matches(*domains):
        return any(host == dom or host.endswith(f".{dom}") for dom in domains)

    if matches("open.spotify.com", "spotify.link"):
        return "spotify"
    if matches("youtube.com", "youtu.be", "youtube-nocookie.com"):
        return "youtube"
    if matches("instagram.com", "instagr.am"):
        return "instagram"
    if matches("facebook.com", "fb.watch", "fb.com"):
        return "facebook"
    return "general"


def unsupported_download_reason(url: str):
    """Explica por qué una URL conocida no se puede descargar."""
    if detect_platform(url) == "spotify":
        return (
            "Spotify protege sus canciones con DRM y no permite que AveDescargas "
            "las descargue directamente. Puedes escucharla o descargarla para uso "
            "sin conexión desde la aplicación oficial de Spotify."
        )
    return None


def has_playlist_reference(url: str) -> bool:
    """Indica si un enlace de YouTube hace referencia a una playlist."""
    if detect_platform(url) != "youtube":
        return False
    return bool(parse_qs(urlparse(url).query).get("list"))


def prepare_download_targets(url: str, choice: str, selected_urls=None):
    """Convierte una decisión de playlist en objetivos de descarga explícitos."""
    if choice == "single":
        return [{"url": url, "allow_playlist": False}]
    if choice == "all":
        return [{"url": url, "allow_playlist": True}]
    if choice == "selected":
        selected_urls = list(selected_urls or [])
        if not selected_urls:
            raise ValueError("Selecciona al menos una canción.")
        return [
            {"url": selected_url, "allow_playlist": False}
            for selected_url in selected_urls
        ]
    raise ValueError(f"Opción de playlist desconocida: {choice}")


def download_scope_options(target):
    """Traduce el alcance elegido al ajuste que entiende yt-dlp."""
    return {"noplaylist": not bool(target.get("allow_playlist"))}


def parse_timecode(value: str) -> int:
    """Convierte SS, MM:SS o HH:MM:SS a segundos."""
    text = (value or "").strip()
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}", text):
        raise ValueError("Usa un formato de tiempo como HH:MM:SS, MM:SS o segundos.")
    parts = [int(part) for part in text.split(":")]
    if len(parts) > 1 and any(part >= 60 for part in parts[1:]):
        raise ValueError("Usa un formato de tiempo válido; minutos y segundos deben ser menores de 60.")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def validate_clip_range(start_value: str, end_value: str):
    """Valida y devuelve el intervalo elegido en segundos."""
    start = parse_timecode(start_value)
    end = parse_timecode(end_value)
    if end <= start:
        raise ValueError("La hora final debe ser posterior a la hora inicial.")
    return start, end


def apply_clip_to_targets(targets, start: int, end: int):
    """Añade el mismo intervalo a objetivos individuales sin mutarlos."""
    return [
        {**target, "section_start": start, "section_end": end}
        for target in targets
    ]


def clip_download_options(target, fmt: str):
    """Construye las opciones Python de yt-dlp para descargar solo un tramo."""
    start = target.get("section_start")
    end = target.get("section_end")
    if start is None or end is None:
        return {}
    return {
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": fmt == "mp4",
    }


def build_output_template(output_dir: str, target):
    """Genera un nombre distinto para el fragmento y la descarga completa."""
    start = target.get("section_start")
    end = target.get("section_end")
    custom_name = sanitize_output_name(target.get("custom_name", ""))
    base = custom_name or "%(title).150B [%(id)s]"
    if start is not None and end is not None:
        base += f" [fragmento {start}-{end}]"
    return os.path.join(output_dir, f"{base}.%(ext)s")


def playlist_entries_from_info(info):
    """Normaliza las entradas planas devueltas por yt-dlp."""
    entries = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        entry_id = entry.get("id")
        entry_url = entry.get("webpage_url")
        if not entry_url and entry_id:
            entry_url = f"https://www.youtube.com/watch?v={entry_id}"
        if not entry_url:
            continue
        entries.append({
            "title": entry.get("title") or entry_id or "Canción sin título",
            "url": entry_url,
        })
    return entries


def fetch_playlist_entries(url: str):
    """Carga títulos y URLs de una playlist sin descargar contenido."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 15,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return playlist_entries_from_info(info)

def clean_folder(folder: str):
    """Elimina archivos temporales dejados por descargas incompletas o canceladas."""
    temp_exts = {".part", ".ytdl", ".download", ".temp"}
    deleted_count = 0
    try:
        for f in os.listdir(folder):
            lower = f.lower()
            _, ext = os.path.splitext(lower)
            is_temp = (
                ext in temp_exts
                or ".temp." in lower
                or bool(re.search(r"\.f\d+\.[a-z0-9]{2,5}$", lower))
            )
            if is_temp:
                try:
                    os.remove(os.path.join(folder, f))
                    deleted_count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return deleted_count

# --- yt-dlp Logger and Progress Hook ---
class YtDlpLogger:
    def debug(self, msg):
        # Filter raw progress lines or debugging junk from showing in main logs
        if not msg.startswith('[debug]') and not any(x in msg for x in ['[download]', 'ETA']):
            ui_queue.put({'type': 'log', 'text': msg, 'level': 'normal'})

    def info(self, msg):
        ui_queue.put({'type': 'log', 'text': msg, 'level': 'normal'})

    def warning(self, msg):
        ui_queue.put({'type': 'log', 'text': msg, 'level': 'warning'})

    def error(self, msg):
        ui_queue.put({'type': 'log', 'text': msg, 'level': 'error'})

def progress_hook(d):
    if CANCEL_REQUESTED:
        raise DownloadCancelledException("Cancelado por el usuario")

    status = d.get('status')
    if status == 'downloading':
        percent_str = d.get('_percent_str', '').strip()
        speed_str = d.get('_speed_str', '').strip()
        eta_str = d.get('_eta_str', '').strip()
        filename = os.path.basename(d.get('filename', ''))

        # Strip ANSI escape codes
        percent_str = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', percent_str)
        speed_str = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', speed_str)
        eta_str = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', eta_str)

        try:
            percent_val = float(percent_str.replace('%', '').strip())
            if not math.isfinite(percent_val):
                percent_val = 0.0
            else:
                percent_val = max(0.0, min(100.0, percent_val))
        except (TypeError, ValueError):
            percent_val = 0.0

        ui_queue.put({
            'type': 'progress',
            'percent': percent_val,
            'percent_str': percent_str,
            'speed': speed_str,
            'eta': eta_str,
            'filename': filename
        })
    elif status == 'finished':
        ui_queue.put({
            'type': 'finished_download',
            'filename': os.path.basename(d.get('filename', ''))
        })

# --- Background Worker Thread ---
def run_downloads_thread(download_targets, output_dir, fmt, video_quality, include_audio=True):
    global IS_DOWNLOADING, CANCEL_REQUESTED
    IS_DOWNLOADING = True
    CANCEL_REQUESTED = False

    ok_count = 0
    err_count = 0
    cancelled = False

    try:
        ffmpeg_path = get_ffmpeg_path()
        ui_queue.put({'type': 'start', 'total': len(download_targets)})

        for index, target in enumerate(download_targets):
            if CANCEL_REQUESTED:
                cancelled = True
                break

            url = target['url'].strip()
            if not url:
                continue

            platform = detect_platform(url)
            ui_queue.put({'type': 'active_url', 'index': index, 'url': url, 'platform': platform})
            if fmt == "mp4":
                notice = heavy_clip_notice(target, video_quality)
                if notice:
                    ui_queue.put({'type': 'log', 'text': notice, 'level': 'warning'})

            node_path = shutil.which("node")
            ydl_opts = {
                # El ID evita que dos vídeos con el mismo título se sobrescriban.
                'outtmpl': build_output_template(output_dir, target),
                'overwrites': False,
                'logger': YtDlpLogger(),
                'progress_hooks': [progress_hook],
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'no_color': True,
                'quiet': True,
                'remote_components': ['ejs:github'],
            }
            ydl_opts.update(download_scope_options(target))
            ydl_opts.update(clip_download_options(target, fmt))

            if node_path:
                ydl_opts['js_runtimes'] = {
                    'node': {
                        'path': node_path
                    }
                }

            if ffmpeg_path:
                ydl_opts['ffmpeg_location'] = ffmpeg_path

            if target.get('section_start') is not None and not ffmpeg_path:
                ui_queue.put({
                    'type': 'log',
                    'text': '❌ FFmpeg no está instalado o no se encuentra. Es necesario para descargar solo un fragmento.',
                    'level': 'error'
                })
                err_count += 1
                continue

            if fmt == "mp3":
                if not ffmpeg_path:
                    ui_queue.put({
                        'type': 'log',
                        'text': '❌ FFmpeg no está instalado o no se encuentra. Es necesario para convertir el audio a MP3.',
                        'level': 'error'
                    })
                    err_count += 1
                    continue
                ydl_opts.update({
                    'format': 'bestaudio[ext=m4a]/bestaudio/best',
                    'postprocessors': [
                        {
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '320',
                        },
                        {
                            'key': 'EmbedThumbnail',
                        }
                    ],
                    'writethumbnail': True,
                })
            else: # mp4
                if include_audio and not ffmpeg_path:
                    ui_queue.put({
                        'type': 'log',
                        'text': '❌ FFmpeg no está instalado o no se encuentra. Es necesario para unir vídeo y sonido.',
                        'level': 'error'
                    })
                    err_count += 1
                    continue
                ydl_opts.update({
                    'format': build_video_format(video_quality, include_audio),
                    'merge_output_format': 'mp4',
                })
                if not include_audio:
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegVideoRemuxer',
                        'preferedformat': 'mp4',
                    }]

            if platform == 'youtube':
                ydl_opts.update(youtube_extractor_options(fmt, include_audio))
            # Instagram solo entrega el audio de algunas publicaciones a sesiones
            # iniciadas: si falla sin sesión, se reintenta con cookies del navegador.
            attempts = [None]
            if platform == 'instagram':
                attempts += ['firefox', 'edge', 'chrome', 'brave', 'opera']
            try:
                last_error = None
                login_browser_opened = False
                for browser in attempts:
                    opts = dict(ydl_opts)
                    if browser:
                        opts = authenticated_retry_options(ydl_opts, browser, platform)
                        service = 'YouTube' if platform == 'youtube' else 'Instagram'
                        shown_browser = 'chrome' if browser.startswith('chrome') else browser
                        ui_queue.put({'type': 'log', 'text': f'🔑 Reintentando {service} con la sesión de {shown_browser}...', 'level': 'warning'})
                    try:
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(url, download=True)
                        if fmt == "mp4" and include_audio and not has_audio(info):
                            ui_queue.put({'type': 'log', 'text': 'ℹ️ Esta publicación no tiene audio disponible: se ha guardado solo el vídeo.', 'level': 'warning'})
                        last_error = None
                        break
                    except DownloadCancelledException:
                        raise
                    except Exception as attempt_error:
                        # Conserva el error real (sin audio) y no el de cookies
                        if last_error is None:
                            last_error = attempt_error
                        if platform == 'youtube' and is_youtube_auth_challenge(attempt_error):
                            if browser is None and youtube_profile_has_cookies():
                                attempts.append('chrome')
                                continue
                            if not login_browser_opened:
                                login_browser_opened = True
                                ui_queue.put({'type': 'youtube_login_opened'})
                                login_process = open_youtube_login_browser(url)
                                if login_process is None:
                                    ui_queue.put({'type': 'log', 'text': '❌ No se pudo abrir Chrome para iniciar sesión en YouTube.', 'level': 'error'})
                                    break
                                while login_process.poll() is None and not CANCEL_REQUESTED:
                                    time.sleep(0.5)
                                if CANCEL_REQUESTED:
                                    if login_process.poll() is None:
                                        try:
                                            login_process.terminate()
                                        except Exception:
                                            pass
                                    raise DownloadCancelledException("Cancelado por el usuario")
                                if youtube_profile_has_cookies():
                                    attempts.append('chrome_refreshed')
                                    continue
                        if platform != 'instagram':
                            break
                if last_error:
                    raise last_error
                ok_count += 1
            except DownloadCancelledException:
                cancelled = True
                ui_queue.put({'type': 'log', 'text': '🚫 Proceso interrumpido por el usuario.', 'level': 'warning'})
                break
            except Exception as e:
                err_msg = str(e)
                err_msg = re.sub(r'ERROR:\s*', '', err_msg)
                if fmt == "mp4" and include_audio and "Requested format is not available" in err_msg:
                    err_msg = (
                        "Instagram no entrega el audio de esta publicación sin sesión iniciada. "
                        "Inicia sesión en Instagram en Firefox, Edge o Chrome (y cierra el navegador) "
                        "y vuelve a intentarlo; no se guardará un vídeo sin sonido."
                    )
                ui_queue.put({'type': 'log', 'text': f"❌ Error: {err_msg}", 'level': 'error'})
                err_count += 1
    except Exception as exc:
        err_count += 1
        ui_queue.put({'type': 'log', 'text': f"❌ Error inesperado: {exc}", 'level': 'error'})
    finally:
        IS_DOWNLOADING = False
        ui_queue.put({
            'type': 'complete',
            'ok_count': ok_count,
            'err_count': err_count,
            'cancelled': cancelled
        })


def ask_playlist_action(parent):
    """Pregunta qué debe hacerse con un enlace que pertenece a una playlist."""
    dialog = tk.Toplevel(parent)
    dialog.title("Enlace de playlist detectado")
    dialog.configure(bg="#1a1a1a")
    dialog.transient(parent)
    dialog.resizable(False, False)
    result = {"value": None}

    tk.Label(
        dialog,
        text="Este enlace pertenece a una playlist de YouTube",
        font=("Segoe UI", 12, "bold"),
        fg="#ffffff",
        bg="#1a1a1a",
    ).pack(padx=24, pady=(22, 8))
    tk.Label(
        dialog,
        text="¿Qué quieres descargar?",
        font=("Segoe UI", 9),
        fg="#bdbdbd",
        bg="#1a1a1a",
    ).pack(padx=24, pady=(0, 16))

    buttons = tk.Frame(dialog, bg="#1a1a1a")
    buttons.pack(fill="x", padx=24, pady=(0, 14))

    def choose(value):
        result["value"] = value
        dialog.destroy()

    choices = [
        ("Solo esta canción", "single", "#0078d4"),
        ("Toda la playlist", "all", "#383838"),
        ("Elegir canciones...", "selected", "#383838"),
    ]
    for label, value, color in choices:
        tk.Button(
            buttons,
            text=label,
            font=("Segoe UI", 9, "bold" if value == "single" else "normal"),
            bg=color,
            fg="#ffffff",
            activebackground="#005a9e" if value == "single" else "#4a4a4a",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            pady=8,
            command=lambda selected=value: choose(selected),
        ).pack(fill="x", pady=3)

    tk.Button(
        dialog,
        text="Cancelar",
        font=("Segoe UI", 9),
        bg="#1a1a1a",
        fg="#bdbdbd",
        activebackground="#1a1a1a",
        activeforeground="#ffffff",
        relief="flat",
        bd=0,
        command=lambda: choose(None),
    ).pack(pady=(0, 14))

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    dialog.update_idletasks()
    screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    req_w = dialog.winfo_reqwidth()
    req_h = dialog.winfo_reqheight()
    x = max(0, min(screen_w - req_w, parent.winfo_rootx() + (parent.winfo_width() - req_w) // 2))
    y = max(0, min(screen_h - req_h, parent.winfo_rooty() + (parent.winfo_height() - req_h) // 2))
    dialog.geometry(f"+{x}+{y}")
    dialog.grab_set()
    parent.wait_window(dialog)
    return result["value"]


def select_playlist_entries(parent, url):
    """Carga una playlist y permite elegir entradas mediante casillas."""
    dialog = tk.Toplevel(parent)
    dialog.title("Seleccionar canciones de la playlist")
    screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    win_w = min(650, max(360, screen_w - 40))
    win_h = min(520, max(300, screen_h - 60))
    pos_x = max(0, min(screen_w - win_w, parent.winfo_rootx() + (parent.winfo_width() - win_w) // 2))
    pos_y = max(0, min(screen_h - win_h, parent.winfo_rooty() + (parent.winfo_height() - win_h) // 2))
    dialog.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
    dialog.minsize(min(520, win_w), min(380, win_h))
    dialog.configure(bg="#1a1a1a")
    dialog.transient(parent)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(1, weight=1)
    result = {"value": None}
    load_queue = queue.Queue()

    header = tk.Label(
        dialog,
        text="Cargando canciones de la playlist...",
        font=("Segoe UI", 11, "bold"),
        fg="#ffffff",
        bg="#1a1a1a",
        anchor="w",
    )
    header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
    loading = ttk.Progressbar(dialog, mode="indeterminate")
    loading.grid(row=1, column=0, sticky="ew", padx=16, pady=16)
    loading.start(12)

    poll_after_id = {"id": None}

    def close(value=None):
        result["value"] = value
        if poll_after_id["id"]:
            try:
                dialog.after_cancel(poll_after_id["id"])
            except (AttributeError, tk.TclError, ValueError):
                pass
            poll_after_id["id"] = None
        try:
            if dialog.winfo_exists():
                dialog.destroy()
        except tk.TclError:
            pass

    def populate(entries):
        loading.stop()
        loading.destroy()
        header.configure(text=f"Elige las canciones que quieres descargar ({len(entries)})")

        list_frame = tk.Frame(dialog, bg="#262626")
        list_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        canvas = tk.Canvas(list_frame, bg="#262626", highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#262626")
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

        def scroll_playlist(e, step=None):
            if step is None:
                delta = getattr(e, "delta", 0)
                step = -1 if delta > 0 else (1 if delta < 0 else 0)
            if step:
                try:
                    canvas.yview_scroll(step, "units")
                except tk.TclError:
                    pass

        def bind_scroll(widget):
            widget.bind("<MouseWheel>", scroll_playlist)
            widget.bind("<Button-4>", lambda e: scroll_playlist(e, -1))
            widget.bind("<Button-5>", lambda e: scroll_playlist(e, 1))

        bind_scroll(canvas)
        bind_scroll(inner)

        selections = []
        for index, entry in enumerate(entries, 1):
            selected = tk.BooleanVar(value=True)
            selections.append((selected, entry["url"]))
            cb = tk.Checkbutton(
                inner,
                text=f"{index}. {entry['title']}",
                variable=selected,
                font=("Segoe UI", 9),
                fg="#ffffff",
                bg="#262626",
                activebackground="#262626",
                activeforeground="#ffffff",
                selectcolor="#1e1e1e",
                anchor="w",
                justify="left",
                wraplength=max(240, win_w - 110),
            )
            cb.pack(fill="x", padx=10, pady=3)
            bind_scroll(cb)

        controls = tk.Frame(dialog, bg="#1a1a1a")
        controls.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))

        def set_all(value):
            for selected, _entry_url in selections:
                selected.set(value)

        def confirm():
            selected_urls = [entry_url for selected, entry_url in selections if selected.get()]
            if not selected_urls:
                messagebox.showwarning(
                    "Sin selección",
                    "Selecciona al menos una canción.",
                    parent=dialog,
                )
                return
            close(selected_urls)

        tk.Button(
            controls, text="Seleccionar todas", command=lambda: set_all(True),
            font=("Segoe UI", 8), bg="#383838", fg="#ffffff", relief="flat", bd=0, padx=8, pady=5,
        ).pack(side="left")
        tk.Button(
            controls, text="Quitar todas", command=lambda: set_all(False),
            font=("Segoe UI", 8), bg="#383838", fg="#ffffff", relief="flat", bd=0, padx=8, pady=5,
        ).pack(side="left", padx=6)
        tk.Button(
            controls, text="Cancelar", command=lambda: close(None),
            font=("Segoe UI", 9), bg="#383838", fg="#ffffff", relief="flat", bd=0, padx=12, pady=6,
        ).pack(side="right")
        tk.Button(
            controls, text="Descargar seleccionadas", command=confirm,
            font=("Segoe UI", 9, "bold"), bg="#0078d4", fg="#ffffff", relief="flat", bd=0, padx=12, pady=6,
        ).pack(side="right", padx=6)

    def poll_loader():
        poll_after_id["id"] = None
        try:
            if not dialog.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            status, payload = load_queue.get_nowait()
        except queue.Empty:
            try:
                if dialog.winfo_exists():
                    poll_after_id["id"] = dialog.after(100, poll_loader)
            except tk.TclError:
                pass
            return
        if status == "error":
            messagebox.showerror(
                "No se pudo cargar la playlist",
                str(payload),
                parent=dialog,
            )
            close(None)
            return
        if not payload:
            messagebox.showerror(
                "Playlist vacía",
                "No se encontraron canciones disponibles en la playlist.",
                parent=dialog,
            )
            close(None)
            return
        populate(payload)

    def loader():
        try:
            load_queue.put(("ok", fetch_playlist_entries(url)))
        except Exception as exc:
            load_queue.put(("error", exc))

    dialog.protocol("WM_DELETE_WINDOW", lambda: close(None))
    dialog.grab_set()
    threading.Thread(target=loader, daemon=True).start()
    poll_after_id["id"] = dialog.after(100, poll_loader)
    parent.wait_window(dialog)
    return result["value"]


# --- GUI Application ---
class MediaGrabberApp:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings(SETTINGS_PATH)
        self.title_lookup_url = None
        self._auto_title_value = None
        self.root.title("AveDescargas — Descargador Universal")
        self.root.geometry("1100x720")
        self.root.minsize(650, 480)
        self.root.configure(bg="#1a1a1a")
        try:
            self.app_icon = tk.PhotoImage(file=resource_path(os.path.join("assets", "mediagrabber-icon.png")))
            self.root.iconphoto(True, self.app_icon)
        except (tk.TclError, OSError):
            self.app_icon = None

        # Center Window
        self.center_window(1100, 720)

        # Style Configuration
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Style Progressbar
        self.style.configure("TProgressbar",
                             thickness=14,
                             troughcolor='#262626',
                             background='#0078d4',
                             darkcolor='#0078d4',
                             lightcolor='#0078d4',
                             bordercolor='#1a1a1a')

        self.setup_ui()
        set_dark_title_bar(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Start queue checking loop
        self._queue_after_id = self.root.after(100, self.check_queue)

    def on_close(self):
        global CANCEL_REQUESTED
        editor_panel = getattr(self, "editor_panel", None)
        if getattr(editor_panel, "exporting", False):
            if not messagebox.askyesno(
                "Exportación en curso",
                "Hay una exportación en curso.\n\n¿Seguro que quieres cerrar la aplicación y cancelar la exportación?",
            ):
                return
        if IS_DOWNLOADING:
            if not messagebox.askyesno(
                "Descarga en curso",
                "Hay una descarga en curso.\n\n¿Seguro que quieres cerrar la aplicación y cancelar la descarga?",
            ):
                return
        CANCEL_REQUESTED = True
        after_id = getattr(self, "_queue_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError, ValueError):
                pass
            self._queue_after_id = None
        if editor_panel is not None:
            try:
                if hasattr(editor_panel, "destroy"):
                    editor_panel.destroy()
                elif hasattr(editor_panel, "stop_playback"):
                    editor_panel.stop_playback()
            except Exception:
                pass
        self.root.destroy()

    def center_window(self, width, height):
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        actual_w = min(width, max(650, screen_width - 40))
        actual_h = min(height, max(480, screen_height - 60))
        x = max(0, (screen_width - actual_w) // 2)
        y = max(0, (screen_height - actual_h) // 2)
        self.root.geometry(f"{actual_w}x{actual_h}+{x}+{y}")

    def setup_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.download_tab = tk.Frame(self.notebook, bg="#1a1a1a")
        self.editor_tab = tk.Frame(self.notebook, bg="#1a1a1a")
        self.notebook.add(self.download_tab, text="  Descargar  ")
        self.notebook.add(self.editor_tab, text="  Editar  ")

        # Los controles de descarga quedan siempre visibles. El resto del
        # contenido puede desplazarse cuando Windows usa una escala grande.
        self.bottom_container = tk.Frame(self.download_tab, bg="#1a1a1a", padx=14)
        self.bottom_container.pack(side="bottom", fill="x", pady=(5, 10))

        body = tk.Frame(self.download_tab, bg="#1a1a1a")
        body.pack(side="top", fill="both", expand=True)
        self.body_canvas = tk.Canvas(body, bg="#1a1a1a", highlightthickness=0, bd=0)
        self.body_scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.body_canvas.yview)
        self.body_canvas.configure(yscrollcommand=self.body_scrollbar.set)
        self.body_scrollbar.pack(side="right", fill="y")
        self.body_canvas.pack(side="left", fill="both", expand=True)

        self.main_container = tk.Frame(self.body_canvas, bg="#1a1a1a", padx=14, pady=10)
        self.body_window = self.body_canvas.create_window(
            (0, 0), window=self.main_container, anchor="nw"
        )

        def update_scroll_region(_event=None):
            self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))

        def resize_body(event):
            requested_height = self.main_container.winfo_reqheight()
            self.body_canvas.itemconfigure(
                self.body_window,
                width=event.width,
                height=max(event.height, requested_height),
            )
            update_scroll_region()

        def scroll_body(event, step=None):
            widget = getattr(event, "widget", None)
            if not widget or not hasattr(widget, "winfo_toplevel"):
                return
            try:
                if widget.winfo_toplevel() != self.root:
                    return
                if self.notebook.select() != str(self.download_tab):
                    return
            except tk.TclError:
                return
            if widget in (self.terminal, self.url_input):
                return
            if step is None:
                delta = getattr(event, "delta", 0)
                step = -1 if delta > 0 else (1 if delta < 0 else 0)
            if step and self.body_canvas.bbox("all"):
                self.body_canvas.yview_scroll(step, "units")

        self.main_container.bind("<Configure>", update_scroll_region)
        self.body_canvas.bind("<Configure>", resize_body)
        self.body_canvas.bind_all("<MouseWheel>", scroll_body)
        self.body_canvas.bind_all("<Button-4>", lambda event: scroll_body(event, -1))
        self.body_canvas.bind_all("<Button-5>", lambda event: scroll_body(event, 1))

        # 1. Header Frame
        header = tk.Frame(self.main_container, bg="#1a1a1a")
        header.pack(fill="x", pady=(0, 7))

        lbl_title = tk.Label(header, text="AveDescargas", font=("Segoe UI", 16, "bold"), fg="#ffffff", bg="#1a1a1a")
        lbl_title.pack(anchor="w")

        lbl_sub = tk.Label(header, text="Descarga música y vídeos en alta calidad de YouTube, Instagram y Facebook", 
                           font=("Segoe UI", 9), fg="#8c8c8c", bg="#1a1a1a")
        lbl_sub.pack(anchor="w", pady=(2, 0))

        # 2. URLs Card
        card_urls = tk.Frame(self.main_container, bg="#262626", bd=1, relief="flat", highlightbackground="#333333", highlightthickness=1)
        card_urls.pack(fill="x", expand=False, pady=(0, 8))

        lbl_urls = tk.Label(card_urls, text="Enlaces de los medios (uno por línea):", font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#262626")
        lbl_urls.pack(anchor="w", padx=10, pady=(7, 3))

        # Custom ScrolledText for URLs
        url_container = tk.Frame(card_urls, bg="#262626", height=78)
        url_container.pack(fill="x", expand=False, padx=10, pady=(0, 5))
        url_container.pack_propagate(False)

        self.url_input = tk.Text(url_container, height=4, font=("Segoe UI", 9), bg="#1e1e1e", fg="#ffffff", 
                                 insertbackground="#ffffff", relief="flat", bd=0, wrap="word")
        self.url_input.pack(side="left", fill="both", expand=True)

        url_scroll = ttk.Scrollbar(url_container, orient="vertical", command=self.url_input.yview)
        url_scroll.pack(side="right", fill="y")
        self.url_input.config(yscrollcommand=url_scroll.set)
        self.url_input.bind("<FocusOut>", lambda _event: self.load_title_async())

        # Clear URLs button
        btn_clear_urls = tk.Button(card_urls, text="Limpiar enlaces", font=("Segoe UI", 8), bg="#383838", fg="#ffffff",
                                   activebackground="#4a4a4a", activeforeground="#ffffff", relief="flat", bd=0, 
                                   padx=8, pady=3, command=self.clear_urls)
        btn_clear_urls.pack(anchor="e", padx=10, pady=(0, 6))

        # 3. Settings Card (Folder & Format)
        card_settings = tk.Frame(self.main_container, bg="#262626", bd=1, relief="flat", highlightbackground="#333333", highlightthickness=1)
        card_settings.pack(fill="x", pady=(0, 8))

        # Destination Folder Frame (Grid layout)
        card_settings.columnconfigure(0, weight=3)
        card_settings.columnconfigure(1, weight=1)

        folder_frame = tk.Frame(card_settings, bg="#262626")
        folder_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        lbl_folder = tk.Label(folder_frame, text="Carpeta de destino:", font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#262626")
        lbl_folder.pack(anchor="w", pady=(0, 4))

        entry_container = tk.Frame(folder_frame, bg="#262626")
        entry_container.pack(fill="x")

        self.folder_var = tk.StringVar(value=preferred_download_folder(self.settings, DEFAULT_FOLDER))
        self.folder_entry = tk.Entry(entry_container, textvariable=self.folder_var, font=("Segoe UI", 9), 
                                     bg="#1e1e1e", fg="#ffffff", relief="flat", bd=0, insertbackground="#ffffff")
        self.folder_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))

        btn_browse = tk.Button(entry_container, text="Examinar...", font=("Segoe UI", 9), bg="#383838", fg="#ffffff",
                               activebackground="#4a4a4a", activeforeground="#ffffff", relief="flat", bd=0, 
                               padx=10, command=self.select_folder)
        btn_browse.pack(side="right")

        # Format Frame
        format_frame = tk.Frame(card_settings, bg="#262626")
        format_frame.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=8)

        lbl_fmt = tk.Label(format_frame, text="Formato:", font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#262626")
        lbl_fmt.pack(anchor="w", pady=(0, 4))

        self.fmt_var = tk.StringVar(value="mp4")
        self.quality_var = tk.StringVar(value="1080p (Full HD)")
        self.video_audio_var = tk.StringVar(value="with_audio")

        radio_container = tk.Frame(format_frame, bg="#262626")
        radio_container.pack(anchor="w")

        rb_mp4 = tk.Radiobutton(radio_container, text="🎬 MP4 Video", variable=self.fmt_var, value="mp4",
                                font=("Segoe UI", 9), fg="#ffffff", bg="#262626", activebackground="#262626",
                                activeforeground="#ffffff", selectcolor="#1e1e1e")
        rb_mp4.pack(side="left", padx=(0, 10))

        rb_mp3 = tk.Radiobutton(radio_container, text="🎵 MP3 Audio", variable=self.fmt_var, value="mp3",
                                font=("Segoe UI", 9), fg="#ffffff", bg="#262626", activebackground="#262626",
                                activeforeground="#ffffff", selectcolor="#1e1e1e")
        rb_mp3.pack(side="left")

        sound_container = tk.Frame(format_frame, bg="#262626")
        sound_container.pack(anchor="w", pady=(7, 0))

        self.rb_video_with_audio = tk.Radiobutton(
            sound_container, text="🔊 Con sonido", variable=self.video_audio_var,
            value="with_audio", font=("Segoe UI", 9), fg="#ffffff", bg="#262626",
            activebackground="#262626", activeforeground="#ffffff", selectcolor="#1e1e1e",
        )
        self.rb_video_with_audio.pack(side="left", padx=(0, 8))

        self.rb_video_without_audio = tk.Radiobutton(
            sound_container, text="🔇 Sin sonido", variable=self.video_audio_var,
            value="without_audio", font=("Segoe UI", 9), fg="#ffffff", bg="#262626",
            activebackground="#262626", activeforeground="#ffffff", selectcolor="#1e1e1e",
        )
        self.rb_video_without_audio.pack(side="left")

        rb_mp4.configure(command=self.update_video_option_state)
        rb_mp3.configure(command=self.update_video_option_state)

        quality_container = tk.Frame(format_frame, bg="#262626")
        quality_container.pack(anchor="w", fill="x", pady=(8, 0))

        lbl_quality = tk.Label(quality_container, text="Calidad de vídeo:", font=("Segoe UI", 9, "bold"),
                               fg="#ffffff", bg="#262626")
        lbl_quality.pack(anchor="w", pady=(0, 4))

        self.quality_combo = ttk.Combobox(
            quality_container,
            textvariable=self.quality_var,
            values=list(VIDEO_QUALITIES.keys()),
            state="readonly",
            width=19,
            font=("Segoe UI", 9),
        )
        self.quality_combo.pack(anchor="w")

        # Optional partial download controls
        clip_frame = tk.Frame(card_settings, bg="#262626")
        clip_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 9))

        self.clip_enabled_var = tk.BooleanVar(value=False)
        self.clip_start_var = tk.StringVar(value="00:00")
        self.clip_end_var = tk.StringVar(value="05:00")

        clip_toggle = tk.Checkbutton(
            clip_frame,
            text="Descargar solo un fragmento",
            variable=self.clip_enabled_var,
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg="#262626",
            activebackground="#262626",
            activeforeground="#ffffff",
            selectcolor="#1e1e1e",
        )
        clip_toggle.pack(side="left", padx=(0, 14))

        tk.Label(
            clip_frame, text="Desde:", font=("Segoe UI", 9),
            fg="#ffffff", bg="#262626",
        ).pack(side="left", padx=(0, 4))
        self.clip_start_entry = tk.Entry(
            clip_frame, textvariable=self.clip_start_var, width=9,
            font=("Segoe UI", 9), bg="#1e1e1e", fg="#ffffff",
            insertbackground="#ffffff", relief="flat", bd=0,
            disabledbackground="#303030", disabledforeground="#8c8c8c",
        )
        self.clip_start_entry.pack(side="left", ipady=3, padx=(0, 10))

        tk.Label(
            clip_frame, text="Hasta:", font=("Segoe UI", 9),
            fg="#ffffff", bg="#262626",
        ).pack(side="left", padx=(0, 4))
        self.clip_end_entry = tk.Entry(
            clip_frame, textvariable=self.clip_end_var, width=9,
            font=("Segoe UI", 9), bg="#1e1e1e", fg="#ffffff",
            insertbackground="#ffffff", relief="flat", bd=0,
            disabledbackground="#303030", disabledforeground="#8c8c8c",
        )
        self.clip_end_entry.pack(side="left", ipady=3, padx=(0, 10))

        tk.Label(
            clip_frame, text="HH:MM:SS o MM:SS", font=("Segoe UI", 8),
            fg="#8c8c8c", bg="#262626",
        ).pack(side="left")

        def update_clip_controls():
            state = "normal" if self.clip_enabled_var.get() else "disabled"
            self.clip_start_entry.configure(state=state)
            self.clip_end_entry.configure(state=state)

        clip_toggle.configure(command=update_clip_controls)
        update_clip_controls()

        name_frame = tk.Frame(card_settings, bg="#262626")
        name_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 9))
        tk.Label(name_frame, text="Nombre final:", font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#262626").pack(side="left", padx=(0, 7))
        self.custom_name_var = tk.StringVar()
        self.custom_name_entry = tk.Entry(name_frame, textvariable=self.custom_name_var, font=("Segoe UI", 9), bg="#1e1e1e", fg="#ffffff", insertbackground="#ffffff", relief="flat", bd=0)
        self.custom_name_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 7))
        tk.Button(name_frame, text="Obtener título", font=("Segoe UI", 8), bg="#383838", fg="#ffffff", activebackground="#4a4a4a", activeforeground="#ffffff", relief="flat", bd=0, padx=8, command=lambda: self.load_title_async(force=True)).pack(side="right")

        # 4. Action & Progress Panel
        action_frame = tk.Frame(self.bottom_container, bg="#1a1a1a")
        action_frame.pack(fill="x", pady=(0, 6))

        self.btn_download = tk.Button(action_frame, text="▶  INICIAR DESCARGA", font=("Segoe UI", 10, "bold"), 
                                      bg="#0078d4", fg="#ffffff", activebackground="#005a9e", activeforeground="#ffffff", 
                                      relief="flat", bd=0, padx=20, pady=8, cursor="hand2", command=self.on_download)
        self.btn_download.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_cancel = tk.Button(action_frame, text="✕  CANCELAR", font=("Segoe UI", 10, "bold"), 
                                    bg="#a80000", fg="#ffffff", activebackground="#820000", activeforeground="#ffffff", 
                                    relief="flat", bd=0, padx=20, pady=8, state="disabled", cursor="hand2", command=self.on_cancel)
        self.btn_cancel.pack(side="right")

        # Progress elements
        progress_container = tk.Frame(self.bottom_container, bg="#1a1a1a")
        progress_container.pack(fill="x")

        self.lbl_status = tk.Label(progress_container, text="Estado: Listo", font=("Segoe UI", 9), fg="#8c8c8c", bg="#1a1a1a")
        self.lbl_status.pack(fill="x", anchor="w", pady=(0, 4))

        self.progress_bar = ttk.Progressbar(progress_container, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x")

        # 5. Log Console Card
        card_log = tk.Frame(self.main_container, bg="#262626", bd=1, relief="flat", highlightbackground="#333333", highlightthickness=1)
        card_log.pack(fill="both", expand=True)

        lbl_log = tk.Label(card_log, text="Consola de registro:", font=("Segoe UI", 9, "bold"), fg="#ffffff", bg="#262626")
        lbl_log.pack(anchor="w", padx=12, pady=(8, 4))

        # Log content terminal
        log_container = tk.Frame(card_log, bg="#262626")
        log_container.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        self.terminal = scrolledtext.ScrolledText(log_container, font=("Consolas", 9), bg="#0c0c0c", fg="#dfdfdf",
                                                 insertbackground="#ffffff", relief="flat", bd=0, wrap="word", state="disabled",
                                                 height=9)
        self.terminal.pack(fill="both", expand=True)

        # Config tags for colors in console logs
        self.terminal.tag_config('error', foreground='#F85149')
        self.terminal.tag_config('warning', foreground='#F9C846')
        self.terminal.tag_config('success', foreground='#2EA043')
        self.terminal.tag_config('normal', foreground='#dfdfdf')

        # Log controls button row
        log_ctrls = tk.Frame(card_log, bg="#262626")
        log_ctrls.pack(fill="x", padx=12, pady=(0, 8))

        btn_copy_log = tk.Button(log_ctrls, text="Copiar registro", font=("Segoe UI", 8), bg="#383838", fg="#ffffff",
                                 activebackground="#4a4a4a", activeforeground="#ffffff", relief="flat", bd=0, 
                                 padx=8, pady=3, command=self.copy_log)
        btn_copy_log.pack(side="left")

        btn_clear_log = tk.Button(log_ctrls, text="Limpiar registro", font=("Segoe UI", 8), bg="#383838", fg="#ffffff",
                                  activebackground="#4a4a4a", activeforeground="#ffffff", relief="flat", bd=0, 
                                  padx=8, pady=3, command=self.clear_log)
        btn_clear_log.pack(side="right")

        # Initial Welcome Message in console
        self.update_video_option_state()
        self.log_to_terminal("✨ AveDescargas iniciado y listo.", "success")
        self.log_to_terminal("Introduce enlaces de YouTube, Instagram o Facebook arriba y selecciona el formato deseado.\n", "normal")

        self.editor_panel = EditorPanel(self.editor_tab)
        self.editor_panel.pack(fill="both", expand=True)

    # --- Actions ---
    def update_video_option_state(self):
        state = "normal" if self.fmt_var.get() == "mp4" else "disabled"
        self.rb_video_with_audio.configure(state=state)
        self.rb_video_without_audio.configure(state=state)
        self.quality_combo.configure(state="readonly" if state == "normal" else "disabled")

    def select_folder(self):
        f = filedialog.askdirectory(initialdir=self.folder_var.get())
        if f:
            self.folder_var.set(f)
            self.settings["download_folder"] = f
            try:
                save_settings(SETTINGS_PATH, self.settings)
            except OSError:
                pass

    def load_title_async(self, force=False):
        urls = [u.strip() for u in self.url_input.get("1.0", tk.END).splitlines() if u.strip()]
        current_name = self.custom_name_var.get().strip()
        auto_val = getattr(self, "_auto_title_value", None)
        is_auto = bool(auto_val and current_name == auto_val)
        if len(urls) != 1 or has_playlist_reference(urls[0]) or not is_valid_http_url(urls[0]):
            self.title_lookup_url = None
            if force or current_name == "Obteniendo título…" or is_auto:
                self.custom_name_var.set("")
                self._auto_title_value = None
            return
        url = urls[0]
        if not force and url == self.title_lookup_url and current_name:
            return
        if not force and current_name and current_name != "Obteniendo título…" and not is_auto:
            self.title_lookup_url = url
            return
        self.title_lookup_url = url
        self._auto_title_value = None
        self.custom_name_var.set("Obteniendo título…")

        def worker():
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 15}) as ydl:
                    info = ydl.extract_info(url, download=False)
                title = (info or {}).get("title") or ""
            except Exception:
                title = ""
            ui_queue.put({"type": "detected_title", "url": url, "title": title})

        threading.Thread(target=worker, daemon=True).start()

    def clear_urls(self):
        self.url_input.delete("1.0", tk.END)
        self.title_lookup_url = None
        self._auto_title_value = None
        self.custom_name_var.set("")

    def clear_log(self):
        self.terminal.configure(state='normal')
        self.terminal.delete("1.0", tk.END)
        self.terminal.configure(state='disabled')

    def copy_log(self):
        content = self.terminal.get("1.0", tk.END).strip()
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            messagebox.showinfo("Copiado", "El registro se ha copiado al portapapeles.")

    def log_to_terminal(self, text, level='normal'):
        self.terminal.configure(state='normal')
        self.terminal.insert(tk.END, text + "\n", level)
        self.terminal.see(tk.END)
        self.terminal.configure(state='disabled')

    def resolve_download_targets(self, urls):
        """Convierte URLs en objetivos explícitos, preguntando por playlists."""
        targets = []
        for url in urls:
            if not has_playlist_reference(url):
                targets.extend(prepare_download_targets(url, "single"))
                continue

            choice = ask_playlist_action(self.root)
            if choice is None:
                return None
            if choice == "selected":
                selected_urls = select_playlist_entries(self.root, url)
                if selected_urls is None:
                    return None
                targets.extend(
                    prepare_download_targets(url, "selected", selected_urls)
                )
            else:
                targets.extend(prepare_download_targets(url, choice))
        return targets

    def on_download(self):
        global IS_DOWNLOADING, CANCEL_REQUESTED
        if IS_DOWNLOADING:
            return

        urls_text = self.url_input.get("1.0", tk.END).strip()
        if not urls_text:
            messagebox.showwarning("Sin enlaces", "Por favor, introduce al menos una URL en el cuadro superior.")
            return

        urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
        
        # Verify URLs
        invalid_urls = [u for u in urls if not is_valid_http_url(u)]
        
        if invalid_urls:
            messagebox.showerror("Enlaces Inválidos", 
                                 "Los siguientes enlaces no son válidos (deben empezar con http/https):\n\n" + 
                                 "\n".join(invalid_urls[:5]))
            return

        unsupported_urls = [
            (u, unsupported_download_reason(u))
            for u in urls
            if unsupported_download_reason(u)
        ]
        if unsupported_urls:
            url, reason = unsupported_urls[0]
            messagebox.showerror(
                "Spotify no es compatible",
                f"{reason}\n\nEnlace:\n{url}",
            )
            return

        clip_range = None
        if self.clip_enabled_var.get():
            try:
                clip_range = validate_clip_range(
                    self.clip_start_var.get(),
                    self.clip_end_var.get(),
                )
            except ValueError as exc:
                messagebox.showerror("Intervalo no válido", str(exc))
                return

        download_targets = self.resolve_download_targets(urls)
        if download_targets is None:
            self.lbl_status.configure(text="Descarga cancelada.", fg="#F9C846")
            return
        if IS_DOWNLOADING:
            return
        if clip_range:
            download_targets = apply_clip_to_targets(
                download_targets, clip_range[0], clip_range[1]
            )

        output_dir = self.folder_var.get().strip()
        if not output_dir:
            output_dir = DEFAULT_FOLDER
            self.folder_var.set(output_dir)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Carpeta no válida", f"No se pudo acceder o crear la carpeta de destino:\n\n{output_dir}\n\n{exc}")
            return
        self.settings["download_folder"] = output_dir
        try:
            save_settings(SETTINGS_PATH, self.settings)
        except OSError:
            pass

        if len(download_targets) == 1:
            raw_custom = self.custom_name_var.get().strip()
            auto_val = getattr(self, "_auto_title_value", None)
            if auto_val and raw_custom == auto_val and download_targets[0]["url"] != self.title_lookup_url:
                self.custom_name_var.set("")
                self._auto_title_value = None
                self.title_lookup_url = None
            else:
                custom_name = sanitize_output_name(raw_custom)
                if custom_name and raw_custom != "Obteniendo título…":
                    download_targets[0]["custom_name"] = custom_name

        fmt = self.fmt_var.get()
        video_quality = self.quality_var.get()
        include_audio = self.video_audio_var.get() == "with_audio"
        
        # Reset progress components
        self.progress_bar['value'] = 0
        self.lbl_status.configure(text="Iniciando...", fg="#8c8c8c")
        self.clear_log()
        
        self.log_to_terminal(f"📁 Directorio de descarga: {output_dir}", "normal")
        self.log_to_terminal(f"🎬 Formato seleccionado: {'MP3 Audio' if fmt == 'mp3' else 'MP4 Video'}", "normal")
        if fmt == "mp4":
            self.log_to_terminal(f"📺 Calidad máxima: {video_quality}", "normal")
            self.log_to_terminal(
                f"{'🔊' if include_audio else '🔇'} Modo de vídeo: "
                f"{video_audio_description(include_audio)}.",
                "normal",
            )
        if clip_range:
            self.log_to_terminal(
                f"✂️ Fragmento: {self.clip_start_var.get()} → {self.clip_end_var.get()} "
                "(solo se solicitará este intervalo)",
                "normal",
            )
        self.log_to_terminal(f"🔗 Total elementos preparados: {len(download_targets)}\n", "normal")

        # Spawn download background thread
        IS_DOWNLOADING = True
        CANCEL_REQUESTED = False
        self.btn_download.configure(state="disabled")
        try:
            t = threading.Thread(
                target=run_downloads_thread,
                args=(download_targets, output_dir, fmt, video_quality, include_audio),
                daemon=True,
            )
            t.start()
        except Exception:
            IS_DOWNLOADING = False
            self.btn_download.configure(state="normal")
            raise

    def on_cancel(self):
        global CANCEL_REQUESTED
        if not IS_DOWNLOADING:
            return
        if messagebox.askyesno("Confirmar Cancelación", "¿Estás seguro de que quieres cancelar la descarga actual?"):
            if not IS_DOWNLOADING:
                return
            CANCEL_REQUESTED = True
            self.btn_cancel.configure(state="disabled")
            self.lbl_status.configure(text="Cancelando descarga...", fg="#F9C846")
            self.log_to_terminal("\n⚠️ Solicitud de cancelación enviada, esperando que terminen las tareas activas...", "warning")

    # --- Thread Safe Queue Checker Loop ---
    def check_queue(self):
        try:
            while not ui_queue.empty():
                try:
                    msg = ui_queue.get_nowait()
                except queue.Empty:
                    break

                m_type = msg.get('type')

                if m_type == 'start':
                    self.btn_download.configure(state='disabled')
                    self.btn_cancel.configure(state='normal')
                    self.progress_bar['value'] = 0
                    self.lbl_status.configure(text="Iniciando descargas...", fg="#ffffff")

                elif m_type == 'detected_title':
                    if msg.get('url') == self.title_lookup_url:
                        auto_val = getattr(self, "_auto_title_value", None)
                        current = self.custom_name_var.get().strip()
                        if current in ("", "Obteniendo título…") or (auto_val and current == auto_val):
                            detected = msg.get('title') or ""
                            self._auto_title_value = detected or None
                            self.custom_name_var.set(detected)

                elif m_type == 'youtube_login_opened':
                    messagebox.showinfo(
                        "Inicia sesión en YouTube",
                        "AveDescargas ha abierto una ventana especial de Chrome.\n\n"
                        "Inicia sesión en YouTube y después cierra completamente esa ventana. "
                        "La descarga continuará automáticamente.",
                    )

                elif m_type == 'active_url':
                    idx = (msg.get('index') or 0) + 1
                    url = msg.get('url') or ''
                    platform = (msg.get('platform') or 'general').upper()
                    self.log_to_terminal(f"📥 [{idx}] Descargando ({platform}): {url}", "normal")
                    self.lbl_status.configure(text=f"Procesando enlace {idx}...", fg="#ffffff")
                    self.progress_bar['value'] = 0

                elif m_type == 'progress':
                    try:
                        percent = float(msg.get('percent') or 0.0)
                    except (TypeError, ValueError):
                        percent = 0.0
                    if not math.isfinite(percent):
                        percent = 0.0
                    percent = max(0.0, min(100.0, percent))
                    percent_str = msg.get('percent_str') or ''
                    speed = msg.get('speed') or ''
                    eta = msg.get('eta') or ''

                    self.progress_bar['value'] = percent
                    self.lbl_status.configure(text=f"Descargando: {percent_str} | Velocidad: {speed} | Tiempo restante: {eta}", fg="#0078d4")

                elif m_type == 'finished_download':
                    fname = msg.get('filename') or ''
                    self.log_to_terminal(f"  ✅ Descarga completa: {fname}", "success")
                    self.lbl_status.configure(text="Procesando/Convirtiendo archivo...", fg="#2EA043")

                elif m_type == 'log':
                    text = msg.get('text') or ''
                    level = msg.get('level') or 'normal'
                    self.log_to_terminal(text, level)

                elif m_type == 'complete':
                    ok = msg.get('ok_count') or 0
                    err = msg.get('err_count') or 0
                    cancelled = bool(msg.get('cancelled'))

                    self.progress_bar['value'] = 100 if (ok > 0 and not cancelled) else 0

                    if cancelled:
                        self.log_to_terminal("\n🛑 Descargas canceladas por el usuario.", "warning")
                        self.lbl_status.configure(text="Operación cancelada.", fg="#F9C846")
                        messagebox.showinfo("Cancelado", "Las descargas han sido canceladas por el usuario.")
                    else:
                        self.log_to_terminal(f"\n✨ ¡Finalizado! Descargas completadas: {ok} | Errores: {err}", "success" if err == 0 else "warning")
                        self.lbl_status.configure(text=f"¡Terminado! {ok} exitosas, {err} errores.", fg="#2EA043" if err == 0 else "#F9C846")
                        messagebox.showinfo("Proceso Terminado", f"¡Descargas terminadas!\n\nExitosas: {ok}\nErrores: {err}")

                    self.btn_download.configure(state='normal')
                    self.btn_cancel.configure(state='disabled')

                    # Run temporary files cleanup
                    folder = self.folder_var.get().strip()
                    if os.path.exists(folder):
                        deleted = clean_folder(folder)
                        if deleted > 0:
                            self.log_to_terminal(f"🧹 Limpieza: Se eliminaron {deleted} archivos temporales.", "normal")
        finally:
            winfo_exists = getattr(self.root, "winfo_exists", None)
            if not callable(winfo_exists) or winfo_exists():
                try:
                    self._queue_after_id = self.root.after(100, self.check_queue)
                except tk.TclError:
                    pass


if __name__ == "__main__":
    # Fix High DPI scaling issues on Windows
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    app = MediaGrabberApp(root)
    root.mainloop()

