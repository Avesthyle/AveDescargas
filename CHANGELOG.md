# Changelog

Notable project changes are recorded here.

## [1.0.1] - 2026-11-21

### Mejoras respecto a `1.0.0`
- **Selector deslizante de formato (`🎬 Vídeo` / `🎵 MP3`)**: sustituye los botones de radio por un interruptor deslizante más claro.
- **Opciones de vídeo ocultables**: los controles de sonido (`Con sonido` / `Sin sonido`) y `Calidad de vídeo` se ocultan automáticamente cuando se selecciona `MP3` y solo aparecen al elegir `Vídeo`.

## [1.0.0] - 2026-11-21

Primera versión ejecutable compilada y publicada en GitHub Releases (`AveDescargasEditor-v1.0.0.exe` y `AveDescargasEditor-v1.0.0-portable.zip`).

### Mejoras respecto a `0.1.0`
- **Editor multipista visual (`SimpleEditorPanel`)**: visor con previsualización FFmpeg/FFplay, biblioteca de medios con arrastrar y soltar, inspector de volumen/fundidos/tono (`±12` semitonos) y guardado/carga de proyectos `.avep`.
- **Línea de tiempo magnética**: imán con guía visual, pista principal magnética, cerrar hueco, eliminar y cerrar hueco (`Shift+Supr`), altura redimensionable por pista, zoom adaptativo, formas de onda en segundo plano y deshacer/rehacer (`Ctrl+Z` / `Ctrl+Y`).
- **Descargador mejorado**: selector de playlists (vídeo suelto, playlist completa o selección), descarga por fragmentos, detección de títulos, validación estricta de URLs y nombres en Windows, reintentos con sesión en YouTube/Instagram y cancelación limpia.
- **Estabilidad y pruebas**: 32 rondas de corrección de errores verificadas con 190 tests automáticos.

## [0.1.0]

- Publicación inicial del código fuente de AveDescargas y el editor multipista básico.
