# AveDescargas

Código fuente de AveDescargas, descargador de medios y editor multipista para Windows.

## Descargar

Ve a **[Releases](https://github.com/Avesthyle/AveDescargas/releases)** y baja `AveDescargasEditor-portable.zip`
(incluye FFmpeg: descomprimir y abrir `AveDescargasEditor.exe`).

## Editor

Línea de tiempo multipista con imán (los clips se pegan solos a otros clips y al cabezal), recorte de bordes,
altura de pistas ajustable, cierre de huecos, pista principal magnética y deshacer/rehacer.
Guía completa y atajos: **[docs/EDITOR.md](docs/EDITOR.md)**.

![Editor](docs/img/02_iman_guia.png)

## Desarrollo

Requiere Python 3.12+ y FFmpeg/FFprobe/FFplay (en el PATH, en `C:\ffmpeg\bin` o en una carpeta `ffmpeg\bin` junto al ejecutable).

```powershell
py -3 -m pip install -r requirements.txt
py -3 media_grabber_editor.py
```

Tests:

```powershell
py -3 -m unittest test_magnetic_timeline test_media_editor test_media_grabber test_editor_panel_gui
```

El ejecutable de Windows se genera con `pyinstaller AveDescargasEditor.spec`, y GitHub Actions lo compila
automáticamente en cada push (ver `.github/workflows/build-windows.yml`).
