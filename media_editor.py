"""Modelo multipista y motor FFmpeg del editor de AveDescargas."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import math
from typing import Callable, Optional


MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
}


def clamp_media_time(value: float, duration: float) -> float:
    return max(0.0, min(float(value), max(0.0, float(duration))))


def normalize_selection(first: float, second: float, duration: float) -> tuple[float, float]:
    start, end = sorted((clamp_media_time(first, duration), clamp_media_time(second, duration)))
    if end <= start:
        end = float(duration)
        if end <= start:
            start = max(0.0, end - 1.0)
    return start, end


def playback_range(cursor: float, mark_in: float, mark_out: float, duration: float, from_mark: bool = False) -> tuple[float, float]:
    """Return transport range, from cursor for Play or from mark-in for Space."""
    total = max(0.0, float(duration))
    start = clamp_media_time(mark_in if from_mark else cursor, total)
    end = clamp_media_time(mark_out, total)
    if end <= start:
        end = total
    return start, end


def parse_editor_time(value) -> float:
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value).strip().replace(",", ".")
        if not text:
            return 0.0
        parts = text.split(":")
        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError("Usa segundos, MM:SS o HH:MM:SS.") from exc
        if len(numbers) == 1:
            result = numbers[0]
        elif len(numbers) == 2:
            result = numbers[0] * 60 + numbers[1]
        elif len(numbers) == 3:
            result = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        else:
            raise ValueError("Usa segundos, MM:SS o HH:MM:SS.")
    if result < 0:
        raise ValueError("El tiempo no puede ser negativo.")
    return result


def format_editor_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def pitch_factor(semitones: int) -> float:
    """Return the playback-rate factor for a semitone shift."""
    value = int(semitones)
    if not -12 <= value <= 12:
        raise ValueError("El tono debe estar entre -12 y +12 semitonos.")
    return 2.0 ** (value / 12.0)


def build_pitch_filter(semitones: int, sample_rate: int = 44100) -> Optional[str]:
    """Shift pitch and compensate tempo so the clip keeps its duration."""
    value = int(semitones)
    factor = pitch_factor(value)
    if value == 0:
        return None
    return (
        f"asetrate={int(sample_rate)}*{factor:.9f},"
        f"aresample={int(sample_rate)},atempo={1.0 / factor:.9f}"
    )


def snap_timeline_position(
    raw_start: float,
    duration: float,
    candidates: list[float],
    threshold: float,
) -> tuple[float, Optional[float]]:
    """Snap either edge of a moving clip to the nearest candidate."""
    raw_start = max(0.0, float(raw_start))
    duration = max(0.0, float(duration))
    threshold = max(0.0, float(threshold))
    options = []
    for candidate in candidates:
        target = float(candidate)
        options.append((abs(raw_start - target), raw_start - target, target))
        options.append((abs(raw_start + duration - target), raw_start + duration - target, target))
    if not options:
        return raw_start, None
    distance, delta, target = min(options, key=lambda item: item[0])
    if distance > threshold:
        return raw_start, None
    return max(0.0, raw_start - delta), target


def avoid_clip_overlap(raw_start: float, duration: float, ranges: list[tuple[float, float]]) -> float:
    """Choose the nearest position that does not intersect any neighbor range."""
    start = max(0.0, float(raw_start))
    length = max(MIN_CLIP_DURATION, float(duration))

    def valid(candidate):
        return candidate >= 0 and all(candidate + length <= left + 1e-6 or candidate >= right - 1e-6 for left, right in ranges)

    if valid(start):
        return start
    candidates = [0.0]
    for left, right in ranges:
        candidates.extend((max(0.0, left - length), max(0.0, right)))
    valid_candidates = [candidate for candidate in candidates if valid(candidate)]
    return min(valid_candidates, key=lambda candidate: abs(candidate - start), default=start)


# ---------------------------------------------------------------------------
# Línea de tiempo magnética
# ---------------------------------------------------------------------------

SNAP_PIXELS = 12.0
DEFAULT_TRACK_HEIGHTS = {"video": 56.0, "audio": 64.0}
MIN_TRACK_HEIGHT = 34.0
MAX_TRACK_HEIGHT = 220.0
TRACK_SIZE_PRESETS = {"S": 38.0, "M": 60.0, "L": 110.0}


def snap_threshold(zoom: float, pixels: float = SNAP_PIXELS) -> float:
    """Convierte un radio de enganche en píxeles a segundos según el zoom."""
    return max(0.02, float(pixels) / max(0.001, float(zoom)))


def snap_point(raw: float, candidates: list[float], threshold: float) -> tuple[float, Optional[float]]:
    """Engancha un único punto (p. ej. el borde que se está recortando) al candidato más cercano."""
    raw = max(0.0, float(raw))
    best = None
    for candidate in candidates:
        distance = abs(raw - float(candidate))
        if distance <= threshold and (best is None or distance < best[0]):
            best = (distance, float(candidate))
    if best is None:
        return raw, None
    return max(0.0, best[1]), best[1]


def collect_snap_points(tracks, exclude=(), playhead: Optional[float] = None) -> list[float]:
    """Puntos magnéticos: inicio del proyecto, cabezal y bordes de todos los clips no excluidos."""
    excluded = {id(clip) for clip in exclude}
    points = {0.0}
    if playhead is not None:
        points.add(round(max(0.0, float(playhead)), 6))
    for track in tracks:
        for clip in track.clips:
            if id(clip) in excluded:
                continue
            points.add(round(clip.timeline_start, 6))
            points.add(round(clip.timeline_end, 6))
    return sorted(points)


def find_gap(clips: list[Clip], time: float) -> Optional[tuple[float, float]]:
    """Devuelve el hueco (inicio, fin) que contiene ``time`` o None si cae dentro de un clip o tras el último."""
    time = float(time)
    for clip in clips:
        if clip.timeline_start - 1e-6 <= time < clip.timeline_end - 1e-6:
            return None
    gap_start = max((clip.timeline_end for clip in clips if clip.timeline_end <= time + 1e-6), default=0.0)
    gap_end = min((clip.timeline_start for clip in clips if clip.timeline_start > time - 1e-6), default=None)
    if gap_end is None or gap_end - gap_start <= 1e-6:
        return None
    return gap_start, gap_end


def close_gap(clips: list[Clip], time: float) -> float:
    """Cierra el hueco que contiene ``time`` desplazando a la izquierda los clips posteriores.

    Devuelve los segundos desplazados (0 si no había hueco).
    """
    gap = find_gap(clips, time)
    if gap is None:
        return 0.0
    gap_start, gap_end = gap
    shift = gap_end - gap_start
    for clip in clips:
        if clip.timeline_start >= gap_end - 1e-6:
            clip.timeline_start = round(max(0.0, clip.timeline_start - shift), 6)
    return shift


def ripple_delete(clips: list[Clip], clip: Clip) -> float:
    """Elimina ``clip`` y junta los clips posteriores para no dejar hueco."""
    if clip not in clips:
        return 0.0
    clips.remove(clip)
    shift = clip.duration
    for other in clips:
        if other.timeline_start >= clip.timeline_end - 1e-6:
            other.timeline_start = round(max(0.0, other.timeline_start - shift), 6)
    return shift


def compact_clips(clips: list[Clip]) -> dict[int, float]:
    """Pista magnética: coloca los clips uno detrás de otro desde 0 sin huecos.

    Devuelve ``{id(clip): desplazamiento}`` para poder mover también los clips vinculados.
    """
    deltas = {}
    cursor = 0.0
    for clip in sorted(clips, key=lambda item: item.timeline_start):
        delta = cursor - clip.timeline_start
        if abs(delta) > 1e-9:
            clip.timeline_start = round(cursor, 6)
            deltas[id(clip)] = delta
        cursor = clip.timeline_end
    return deltas


def track_layout(heights: list[float], top: float = 24.0) -> list[tuple[float, float]]:
    """Calcula (y1, y2) de cada pista acumulando alturas variables."""
    rows = []
    current = float(top)
    for height in heights:
        rows.append((current, current + float(height)))
        current += float(height)
    return rows


def track_index_at(layout: list[tuple[float, float]], y: float) -> Optional[int]:
    for index, (y1, y2) in enumerate(layout):
        if y1 <= y < y2:
            return index
    return None


def track_boundary_at(layout: list[tuple[float, float]], y: float, tolerance: float = 6.0) -> Optional[int]:
    """Índice de la pista cuyo borde inferior está a ``tolerance`` píxeles de ``y``."""
    best = None
    for index, (_y1, y2) in enumerate(layout):
        distance = abs(y - y2)
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, index)
    return None if best is None else best[1]


def clamp_track_height(value: float) -> float:
    return max(MIN_TRACK_HEIGHT, min(MAX_TRACK_HEIGHT, float(value)))


def unique_track_name(existing: list[str], kind: str) -> str:
    base = "Vídeo" if kind == "video" else "Audio"
    number = 1
    while f"{base} {number}" in existing:
        number += 1
    return f"{base} {number}"


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    format_name: str = ""
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None


@dataclass
class Clip:
    media: MediaInfo
    source_in: float = 0.0
    source_out: Optional[float] = None
    timeline_start: float = 0.0
    use_source_audio: bool = True
    name: str = ""
    pitch_semitones: int = 0
    link_group: Optional[str] = None
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0

    def __post_init__(self):
        self.source_in = parse_editor_time(self.source_in)
        self.source_out = self.media.duration if self.source_out is None else parse_editor_time(self.source_out)
        self.timeline_start = parse_editor_time(self.timeline_start)
        self.pitch_semitones = int(self.pitch_semitones)
        self.volume = max(0.0, min(2.0, float(self.volume)))
        self.fade_in = max(0.0, float(self.fade_in))
        self.fade_out = max(0.0, float(self.fade_out))
        pitch_factor(self.pitch_semitones)
        if self.source_out <= self.source_in:
            raise ValueError("La salida debe ser posterior a la entrada.")
        if self.source_out > self.media.duration + 0.05:
            raise ValueError("La salida supera la duración del archivo.")
        if not self.name:
            self.name = pathlib.Path(self.media.path).stem

    @property
    def duration(self) -> float:
        return self.source_out - self.source_in

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.duration


MIN_CLIP_DURATION = 0.01


def timeline_to_source_time(clip: Clip, timeline_time: float) -> Optional[float]:
    """Map project time to source time when the timeline cursor intersects a clip."""
    position = float(timeline_time)
    if position < clip.timeline_start or position >= clip.timeline_end:
        return None
    return min(clip.source_out, clip.source_in + position - clip.timeline_start)


def split_clip(clip: Clip, timeline_time: float) -> Optional[tuple[Clip, Clip]]:
    """Divide un clip en dos conservando la correspondencia con el archivo fuente."""
    cut = float(timeline_time)
    if cut <= clip.timeline_start + MIN_CLIP_DURATION:
        return None
    if cut >= clip.timeline_end - MIN_CLIP_DURATION:
        return None
    source_cut = clip.source_in + (cut - clip.timeline_start)
    base_name = re.sub(r"\s*·\s*\d+$", "", clip.name).strip() or clip.name
    left_fade_in, _ = clamp_fades(clip.fade_in, 0.0, source_cut - clip.source_in)
    _, right_fade_out = clamp_fades(0.0, clip.fade_out, clip.source_out - source_cut)
    left = replace(
        clip,
        source_out=source_cut,
        name=f"{base_name} · 1",
        fade_in=left_fade_in,
        fade_out=0.0,
    )
    right = replace(
        clip,
        source_in=source_cut,
        timeline_start=cut,
        name=f"{base_name} · 2",
        fade_in=0.0,
        fade_out=right_fade_out,
    )
    return left, right


def clamp_fades(fade_in: float, fade_out: float, duration: float) -> tuple[float, float]:
    """Limita fade_in y fade_out a la duración del clip evitando que se solapen."""
    dur = max(0.0, float(duration))
    f_in = max(0.0, min(dur, float(fade_in)))
    f_out = max(0.0, min(dur, float(fade_out)))
    total = f_in + f_out
    if total > dur > 0:
        scale = dur / total
        f_in = round(f_in * scale, 3)
        f_out = round(f_out * scale, 3)
    return f_in, f_out


def resize_clip_left(clip: Clip, timeline_start: float) -> Clip:
    """Mueve el borde izquierdo y permite recuperar material anterior del original."""
    requested_start = max(0.0, float(timeline_start))
    source_in = clip.source_in + (requested_start - clip.timeline_start)
    source_in = max(0.0, min(source_in, clip.source_out - MIN_CLIP_DURATION))
    actual_start = clip.timeline_start + (source_in - clip.source_in)
    f_in, f_out = clamp_fades(clip.fade_in, clip.fade_out, clip.source_out - source_in)
    return replace(clip, source_in=source_in, timeline_start=actual_start, fade_in=f_in, fade_out=f_out)


def resize_clip_right(clip: Clip, timeline_end: float) -> Clip:
    """Mueve el borde derecho hasta el archivo fuente, sin crear duración inválida."""
    requested_end = max(clip.timeline_start + MIN_CLIP_DURATION, float(timeline_end))
    source_out = clip.source_in + (requested_end - clip.timeline_start)
    source_out = max(clip.source_in + MIN_CLIP_DURATION, min(source_out, clip.media.duration))
    f_in, f_out = clamp_fades(clip.fade_in, clip.fade_out, source_out - clip.source_in)
    return replace(clip, source_out=source_out, fade_in=f_in, fade_out=f_out)


def unlink_video_audio(video_clip: Clip, audio_clip: Clip) -> tuple[Clip, Clip]:
    """Detach a linked pair without changing timing or source ranges."""
    return replace(video_clip, link_group=None), replace(audio_clip, link_group=None)


def audio_media_from_video(media: MediaInfo) -> MediaInfo:
    return replace(media, has_video=False, width=0, height=0, fps=0.0, video_codec=None)


def waveform_from_values(values: list[float], sample_count: int = 240) -> list[float]:
    sample_count = max(1, int(sample_count))
    if not values:
        return [0.0] * sample_count
    chunk = max(1, len(values) // sample_count)
    rms = []
    for index in range(0, len(values), chunk):
        section = values[index:index + chunk]
        rms.append(min(1.0, math.sqrt(sum(value * value for value in section) / max(1, len(section)))))
    peak = max(rms, default=0.0)
    if peak > 0:
        rms = [min(1.0, value / peak) for value in rms]
    rms.extend([0.0] * (sample_count - len(rms)))
    return rms[:sample_count]


def waveform_segment(samples: list[float], source_in: float, source_out: float, media_duration: float, sample_count: int = 160) -> list[float]:
    """Map a source-time range to waveform samples without stretching its time scale."""
    if not samples or media_duration <= 0 or source_out <= source_in:
        return [0.0] * max(1, int(sample_count))
    start = max(0, min(len(samples), int(round(float(source_in) / media_duration * len(samples)))))
    end = max(start + 1, min(len(samples), int(round(float(source_out) / media_duration * len(samples)))))
    selected = samples[start:end]
    count = max(1, int(sample_count))
    if len(selected) <= count:
        return selected + [selected[-1]] * (count - len(selected))
    step = len(selected) / count
    return [max(selected[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]) for i in range(count)]


def waveform_samples(path: str, sample_count: int = 240) -> list[float]:
    """Read normalized mono waveform peaks, returning a center-line fallback."""
    sample_count = max(1, int(sample_count))
    fallback = [0.0] * sample_count
    try:
        ffmpeg = discover_ffmpeg_tools()[0]
        command = [ffmpeg, "-v", "error", "-i", os.fspath(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"]
        result = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not result.stdout:
            return fallback
        values = [value[0] for value in struct.iter_unpack("<f", result.stdout) if value]
        if not values:
            return fallback
        return waveform_from_values(values, sample_count)
    except (OSError, ValueError, struct.error):
        return fallback


@dataclass
class Track:
    name: str
    kind: str
    enabled: bool = True
    muted: bool = False
    clips: list[Clip] = field(default_factory=list)

    def __post_init__(self):
        if self.kind not in {"video", "audio"}:
            raise ValueError("El canal debe ser de vídeo o audio.")


@dataclass
class EditorProject:
    tracks: list[Track] = field(default_factory=list)
    selected_track_index: int = 0

    def selected_track(self) -> Optional[Track]:
        if 0 <= self.selected_track_index < len(self.tracks):
            return self.tracks[self.selected_track_index]
        return None

    def export_tracks(self, mode: str) -> list[Track]:
        if mode == "selected":
            selected = self.selected_track()
            return [selected] if selected and selected.enabled else []
        if mode != "active":
            raise ValueError("Modo de exportación desconocido.")
        return [track for track in self.tracks if track.enabled]

    def duration(self, tracks: Optional[list[Track]] = None) -> float:
        chosen = self.tracks if tracks is None else tracks
        return max((clip.timeline_end for track in chosen for clip in track.clips), default=0.0)


def _parse_fps(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator or 1)
    return float(value)


def discover_ffmpeg_tools() -> tuple[str, str, str]:
    import sys

    app_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates = [
        os.path.join(app_dir, "ffmpeg", "bin"),
        os.path.join(app_dir, "ffmpeg"),
        app_dir,
        r"C:\ffmpeg_shared4\ffmpeg-master-latest-win64-gpl-shared\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\ffmpeg\bin",
    ]
    for folder in candidates:
        paths = tuple(os.path.join(folder, name) for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"))
        if all(os.path.isfile(path) for path in paths):
            return paths
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    ffplay = shutil.which("ffplay")
    if not all((ffmpeg, ffprobe, ffplay)):
        raise FileNotFoundError("No se encontraron FFmpeg, FFprobe y FFplay.")
    return ffmpeg, ffprobe, ffplay


def probe_media(path: str) -> MediaInfo:
    _, ffprobe, _ = discover_ffmpeg_tools()
    command = [
        ffprobe, "-v", "error", "-show_streams", "-show_format",
        "-of", "json", os.fspath(path),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "No se pudo analizar el archivo.")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration_values = [
        data.get("format", {}).get("duration"),
        video.get("duration") if video else None,
        audio.get("duration") if audio else None,
    ]
    duration = next((float(value) for value in duration_values if value not in (None, "N/A")), 0.0)
    if duration <= 0:
        raise ValueError("No se pudo determinar la duración del archivo.")
    return MediaInfo(
        path=os.path.abspath(path),
        duration=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0") if video else 0.0,
        format_name=data.get("format", {}).get("format_name", ""),
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
    )


def _number(value: float) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".") or "0"


def build_export_plan(
    project: EditorProject,
    mode: str,
    selected_track: Optional[int],
    output_path: str,
    preview: bool = False,
) -> dict:
    if selected_track is not None:
        project.selected_track_index = selected_track
    tracks = project.export_tracks(mode)
    clips_with_tracks = [(track, clip) for track in tracks for clip in track.clips]
    if not clips_with_tracks:
        raise ValueError("No hay clips activos para exportar.")
    duration = project.duration(tracks)
    if duration <= 0:
        raise ValueError("La línea de tiempo está vacía.")

    video_tracks = [track for track in tracks if track.kind == "video" and track.clips]
    first_video = next((clip for track in video_tracks for clip in track.clips if clip.media.has_video), None)
    width = first_video.media.width if first_video else 0
    height = first_video.media.height if first_video else 0
    raw_fps = first_video.media.fps if first_video and first_video.media.fps else 30.0
    fps = raw_fps if 10.0 <= raw_fps <= 60.0 else 30.0
    if first_video:
        if preview:
            ratio = min(1.0, 854 / max(1, width), 480 / max(1, height))
            width = int(width * ratio)
            height = int(height * ratio)
        width = max(2, int(width) // 2 * 2)
        height = max(2, int(height) // 2 * 2)

    inputs = []
    filter_parts = []
    input_map = {}
    for track, clip in clips_with_tracks:
        input_map[id(clip)] = len(inputs)
        inputs.append(clip.media.path)

    video_track_order = [track.name for track in reversed(video_tracks)]
    current_video = None
    if first_video:
        filter_parts.append(
            f"color=c=black:s={width}x{height}:r={_number(fps)}:d={_number(duration)}[vbase]"
        )
        current_video = "vbase"
        layer_number = 0
        for track in reversed(video_tracks):
            for clip in sorted(track.clips, key=lambda item: item.timeline_start):
                if not clip.media.has_video:
                    continue
                index = input_map[id(clip)]
                prepared = f"vclip{layer_number}"
                output = f"vlayer{layer_number}"
                v_filters = [
                    f"[{index}:v]trim=start={_number(clip.source_in)}:end={_number(clip.source_out)}",
                    "setpts=PTS-STARTPTS",
                ]
                d_in, d_out = clamp_fades(clip.fade_in, clip.fade_out, clip.duration)
                if d_in > 0:
                    v_filters.append(f"fade=t=in:st=0:d={_number(d_in)}")
                if d_out > 0:
                    st_out = max(0.0, clip.duration - d_out)
                    v_filters.append(f"fade=t=out:st={_number(st_out)}:d={_number(d_out)}")
                v_filters.extend([
                    f"setpts=PTS+{_number(clip.timeline_start)}/TB",
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                    f"setsar=1[{prepared}]",
                ])
                filter_parts.append(",".join(v_filters))
                filter_parts.append(
                    f"[{current_video}][{prepared}]overlay=0:0:eof_action=pass:"
                    f"enable='between(t,{_number(clip.timeline_start)},{_number(clip.timeline_end)})'[{output}]"
                )
                current_video = output
                layer_number += 1

    audio_labels = []
    audio_number = 0
    for track, clip in clips_with_tracks:
        include = (
            clip.media.has_audio
            and not track.muted
            and clip.use_source_audio
        )
        if not include:
            continue
        index = input_map[id(clip)]
        label = f"aclip{audio_number}"
        delay = int(round(clip.timeline_start * 1000))
        pitch = build_pitch_filter(clip.pitch_semitones)
        a_parts = [
            f"[{index}:a]atrim=start={_number(clip.source_in)}:end={_number(clip.source_out)}",
            "asetpts=PTS-STARTPTS",
            "aformat=channel_layouts=stereo",
        ]
        if pitch:
            a_parts.append(pitch)
        if abs(clip.volume - 1.0) > 1e-4:
            a_parts.append(f"volume={_number(clip.volume)}")
        d_in, d_out = clamp_fades(clip.fade_in, clip.fade_out, clip.duration)
        if d_in > 0:
            a_parts.append(f"afade=t=in:st=0:d={_number(d_in)}")
        if d_out > 0:
            st_out = max(0.0, clip.duration - d_out)
            a_parts.append(f"afade=t=out:st={_number(st_out)}:d={_number(d_out)}")
        a_parts.extend([
            f"adelay={delay}|{delay}",
            "aresample=async=1:first_pts=0",
            "apad",
            f"atrim=duration={_number(duration)}[{label}]",
        ])
        filter_parts.append(",".join(a_parts))
        audio_labels.append(label)
        audio_number += 1

    final_audio = None
    if len(audio_labels) == 1:
        final_audio = audio_labels[0]
    elif audio_labels:
        final_audio = "amixout"
        filter_parts.append(
            "".join(f"[{label}]" for label in audio_labels)
            + f"amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0,"
            + f"alimiter=limit=0.95[{final_audio}]"
        )

    extension = pathlib.Path(output_path).suffix.lower()
    if first_video and extension in {".mp3", ".wav"}:
        raise ValueError("Elige MP4, MOV, MKV o WEBM para exportar vídeo.")
    if not first_video and extension not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
        raise ValueError("Elige un formato de audio para un proyecto sin vídeo.")

    command = [discover_ffmpeg_tools()[0], "-y"]
    for path in inputs:
        command += ["-i", path]
    if filter_parts:
        command += ["-filter_complex", ";".join(filter_parts)]
    if current_video:
        command += ["-map", f"[{current_video}]"]
    if final_audio:
        command += ["-map", f"[{final_audio}]"]
    if first_video:
        if extension == ".webm":
            command += ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0"]
            if final_audio:
                command += ["-c:a", "libopus", "-b:a", "160k"]
        else:
            command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
            if final_audio:
                command += ["-c:a", "aac", "-b:a", "192k"]
        command += ["-r", _number(fps), "-t", _number(duration), "-movflags", "+faststart"]
    elif final_audio:
        if extension == ".wav":
            command += ["-c:a", "pcm_s16le"]
        elif extension == ".mp3":
            command += ["-c:a", "libmp3lame", "-b:a", "320k"]
        else:
            command += ["-c:a", "aac", "-b:a", "256k"]
        command += ["-t", _number(duration)]
    command.append(os.path.abspath(output_path))
    return {
        "command": command,
        "inputs": inputs,
        "filter_complex": ";".join(filter_parts),
        "has_video": first_video is not None,
        "audio_labels": audio_labels,
        "video_track_order": video_track_order,
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "output_path": os.path.abspath(output_path),
    }


def parse_ffmpeg_time(line: str) -> Optional[float]:
    """Extrae el tiempo en segundos de una línea de progreso de FFmpeg (time=HH:MM:SS.xx)."""
    match = re.search(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", line or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def run_export(plan: dict, progress_callback: Optional[Callable[[str], None]] = None) -> str:
    os.makedirs(os.path.dirname(plan["output_path"]) or ".", exist_ok=True)
    process = subprocess.Popen(
        plan["command"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail = []
    try:
        for line in process.stdout or []:
            line = line.rstrip()
            tail.append(line)
            tail = tail[-20:]
            if progress_callback and ("time=" in line or "error" in line.lower()):
                progress_callback(line)
        code = process.wait()
    finally:
        if process.stdout:
            process.stdout.close()
    if code:
        raise RuntimeError("FFmpeg no pudo exportar:\n" + "\n".join(tail))
    if not os.path.isfile(plan["output_path"]) or os.path.getsize(plan["output_path"]) == 0:
        raise RuntimeError("FFmpeg terminó sin crear un archivo válido.")
    return plan["output_path"]


def project_to_dict(
    project: EditorProject,
    track_heights: Optional[dict[str, float]] = None,
    media_library: Optional[list[MediaInfo]] = None,
) -> dict:
    """Serializa un EditorProject a un diccionario JSON."""
    media_list: list[MediaInfo] = []
    media_index: dict[int, int] = {}
    for media in media_library or []:
        if id(media) not in media_index:
            media_index[id(media)] = len(media_list)
            media_list.append(media)
    for track in project.tracks:
        for clip in track.clips:
            if id(clip.media) not in media_index:
                media_index[id(clip.media)] = len(media_list)
                media_list.append(clip.media)
    return {
        "version": 1,
        "selected_track_index": project.selected_track_index,
        "track_heights": dict(track_heights or {}),
        "media": [
            {
                "path": m.path,
                "duration": m.duration,
                "has_video": m.has_video,
                "has_audio": m.has_audio,
                "width": m.width,
                "height": m.height,
                "fps": m.fps,
                "format_name": m.format_name,
                "video_codec": m.video_codec,
                "audio_codec": m.audio_codec,
            }
            for m in media_list
        ],
        "tracks": [
            {
                "name": t.name,
                "kind": t.kind,
                "enabled": t.enabled,
                "muted": t.muted,
                "clips": [
                    {
                        "media_idx": media_index[id(c.media)],
                        "source_in": c.source_in,
                        "source_out": c.source_out,
                        "timeline_start": c.timeline_start,
                        "use_source_audio": c.use_source_audio,
                        "name": c.name,
                        "pitch_semitones": c.pitch_semitones,
                        "link_group": c.link_group,
                        "volume": c.volume,
                        "fade_in": c.fade_in,
                        "fade_out": c.fade_out,
                    }
                    for c in t.clips
                ],
            }
            for t in project.tracks
        ],
    }


def project_from_dict(data: dict) -> tuple[EditorProject, dict[str, float], list[MediaInfo]]:
    """Reconstruye (EditorProject, track_heights, media_list) desde un diccionario JSON."""
    media_list = [
        MediaInfo(
            path=m["path"],
            duration=float(m["duration"]),
            has_video=bool(m["has_video"]),
            has_audio=bool(m["has_audio"]),
            width=int(m.get("width", 0)),
            height=int(m.get("height", 0)),
            fps=float(m.get("fps", 0.0)),
            format_name=str(m.get("format_name", "")),
            video_codec=m.get("video_codec"),
            audio_codec=m.get("audio_codec"),
        )
        for m in data.get("media", [])
    ]
    tracks = []
    for t in data.get("tracks", []):
        clips = [
            Clip(
                media=media_list[int(c["media_idx"])],
                source_in=c["source_in"],
                source_out=c["source_out"],
                timeline_start=c["timeline_start"],
                use_source_audio=bool(c.get("use_source_audio", True)),
                name=str(c.get("name", "")),
                pitch_semitones=int(c.get("pitch_semitones", 0)),
                link_group=c.get("link_group"),
                volume=float(c.get("volume", 1.0)),
                fade_in=float(c.get("fade_in", 0.0)),
                fade_out=float(c.get("fade_out", 0.0)),
            )
            for c in t.get("clips", [])
        ]
        tracks.append(Track(
            name=t["name"],
            kind=t["kind"],
            enabled=bool(t.get("enabled", True)),
            muted=bool(t.get("muted", False)),
            clips=clips,
        ))
    project = EditorProject(tracks=tracks, selected_track_index=int(data.get("selected_track_index", 0)))
    heights = {str(k): clamp_track_height(v) for k, v in data.get("track_heights", {}).items()}
    return project, heights, media_list


def save_project(
    path: str,
    project: EditorProject,
    track_heights: Optional[dict[str, float]] = None,
    media_library: Optional[list[MediaInfo]] = None,
) -> str:
    payload = project_to_dict(project, track_heights, media_library=media_library)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def load_project(path: str) -> tuple[EditorProject, dict[str, float], list[MediaInfo]]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return project_from_dict(data)


