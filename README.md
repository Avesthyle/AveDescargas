# AveDescargas

**Descargador de medios y editor multipista para Windows**, construido con Python y Tkinter.

> Proyecto personal en desarrollo. La interfaz y algunas funciones pueden cambiar entre versiones. Consulta [Releases](https://github.com/Avesthyle/AveDescargas/releases) para obtener las versiones publicadas.

## Funciones

- Descarga de vídeo y audio con selección de calidad y carpeta.
- Selección de un fragmento y controles de audio para vídeo.
- Aviso para enlaces que apuntan a listas de reproducción.
- Editor multipista para organizar clips de audio y vídeo.
- Recorte no destructivo, forma de onda, vista previa, ajuste entre clips y exportación con FFmpeg.
- Reproducción desde el cabezal de edición y controles de teclado.

La disponibilidad de formatos de descarga depende del sitio y de sus condiciones. Utiliza la aplicación únicamente con contenido que tengas derecho a descargar.

## Requisitos

- Windows 10/11 de 64 bits.
- Python 3.12 o posterior para ejecutar desde el código.
- FFmpeg, FFprobe y FFplay accesibles en el PATH o en una ubicación detectada por la aplicación.
- Conexión a Internet para las funciones de descarga.

## Puesta en marcha desde código

```powershell
git clone https://github.com/Avesthyle/AveDescargas.git
cd AveDescargas
py -3 -m venv .venv
.venv\\Scripts\\Activate.ps1
py -m pip install --upgrade pip
py -m pip install yt-dlp Pillow numpy psutil tkinterdnd2
py media_grabber_editor.py
```

## Pruebas

```powershell
py -3 -m unittest test_media_editor.py test_media_grabber.py
```

## Compilar el editor

Instala PyInstaller con `py -m pip install pyinstaller` y ejecuta:

```powershell
py -m PyInstaller --noconfirm AveDescargasEditor.spec
```

El resultado se genera en `dist/AveDescargasEditor.exe`.

## ¿Es portable el EXE?

Es un ejecutable de archivo único de PyInstaller y no requiere instalar Python por separado. Sin embargo, **no es completamente autónomo**: FFmpeg, FFprobe y FFplay deben estar instalados o en una ubicación que detecte el programa, y las descargas necesitan Internet. En el primer inicio, PyInstaller puede extraer componentes temporales en la carpeta temporal de Windows.

## Estructura

| Archivo | Propósito |
|---|---|
| `media_grabber.py` | Interfaz y lógica principal de descargas |
| `media_editor.py` | Modelos de clips, línea de tiempo, FFmpeg y exportación |
| `simple_editor_panel.py` | Editor visual multipista |
| `media_grabber_editor.py` | Punto de entrada de la aplicación |
| `editor_panel.py` | Panel de edición alternativo |
| `AveDescargasEditor.spec` | Configuración de PyInstaller |
| `test_*.py` | Pruebas automatizadas |

## Informar de un problema

Abre un [issue](https://github.com/Avesthyle/AveDescargas/issues) e indica Windows, versión de AveDescargas, pasos para reproducir y el mensaje de error. No adjuntes cookies, tokens, contraseñas ni archivos multimedia privados.

## Licencia

No se ha añadido una licencia todavía. Hasta que el autor elija una, no asumas que el código puede redistribuirse o reutilizarse.
