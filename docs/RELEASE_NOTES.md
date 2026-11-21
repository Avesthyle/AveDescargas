## AveDescargas v1.0.2 — Separar y unir audio/vídeo con clic derecho

**Descarga recomendada:** `AveDescargasEditor-v1.0.2-portable.zip` (incluye FFmpeg, descomprimir y abrir).
`AveDescargasEditor-v1.0.2.exe` solo sirve si ya tienes FFmpeg instalado.

### Mejoras respecto a `v1.0.1`
- Al añadir un vídeo entra unido en la pista de vídeo y, con **clic derecho** sobre el clip, puedes elegir **Separar audio y vídeo**.
- Cuando el audio y el vídeo están separados, el **clic derecho** sobre cualquiera de los dos ofrece **Unir audio y vídeo** para volver a juntarlos en un único clip.

Historial completo de versiones: [CHANGELOG.md](https://github.com/Avesthyle/AveDescargas/blob/main/CHANGELOG.md)

### Novedades
- 🧲 **Imán**: al mover, recortar o cortar, los clips se pegan a otros clips (de cualquier pista), al cabezal y a 00:00, con una guía amarilla que indica a qué se enganchan. **Alt** = movimiento libre.
- ↕ **Altura de pistas**: separador visible y arrastrable, Ctrl + rueda, botones S/M/L y menú contextual.
- **Cerrar hueco** (clic derecho), **Eliminar y cerrar hueco** (Shift+Supr) y **pista principal magnética** opcional.
- ↶↷ **Deshacer / rehacer** (Ctrl+Z / Ctrl+Y).
- **Zoom** (− / ＋ / Ajustar, Alt + rueda) con regla de tiempo adaptativa.
- Atajos nuevos: S / Ctrl+K dividir, Supr eliminar, Inicio / Fin.

### Correcciones
- Los atajos V, C y Espacio ya no se activan al escribir en la pestaña Descargar.
- Arrastrar el cabezal por la línea de tiempo vuelve a funcionar.
- Las pistas con alturas distintas ya no se solapan ni se desalinean de los clics.
- El imán ya no se pegaba al propio clip que se estaba moviendo.
- Las formas de onda se calculan en segundo plano (la ventana ya no se congela).
- Los nombres de pista ya no se repiten tras borrar una pista.

Guía completa: [docs/EDITOR.md](https://github.com/Avesthyle/AveDescargas/blob/main/docs/EDITOR.md)
