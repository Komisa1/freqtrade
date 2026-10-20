# headless-render

Bucle de renderizado headless: Chromium (sin interfaz) pinta cada fotograma, Chrome lo captura y ffmpeg une las capturas en un MP4.

```
node render.mjs ──setup()──────────► scene.html (canvas)
   │   for frame in 0..N:
   │     renderFrame(frame, frame/fps) ─► pinta en el canvas
   │     esperar rAF (Chrome presenta el fotograma)
   │     screenshot PNG del canvas (CDP)
   └──── PNG ──stdin──► ffmpeg (image2pipe → libx264, yuv420p) ──► out.mp4
```

El tiempo lo marca el bucle externo (`time = frame / fps`), no el reloj de pared, así que el vídeo sale igual de fluido y determinista aunque cada captura tarde más que 1/fps.

## Requisitos

Versiones probadas: **Node.js 24.22.0 LTS**, **FFmpeg 9.0.2**, **Playwright 1.64.1** (Chrome for Testing 156).

Para instalarlo todo en una máquina o contenedor nuevo:

```bash
./setup.sh
export PATH="$HOME/.local/bin:$PATH"
```

## Uso

```bash
npm install
node render.mjs --duration 5 --fps 30 --width 1280 --height 720 --out out.mp4
# opcional: guardar también cada PNG
node render.mjs --frames-dir frames/
```

## Escribir tu propia escena

Cualquier HTML con un `<canvas>` que exponga:

```js
window.setup = ({ width, height, fps, totalFrames }) => { /* una vez */ };
window.renderFrame = async (frame, time) => { /* pinta UN fotograma */ };
```

No uses `requestAnimationFrame`, `Date.now()` ni `Math.random()` sin semilla para animar: deriva todo de `frame`/`time`. `renderFrame` puede ser `async` (p. ej. para esperar imágenes o fuentes).

Luego: `node render.mjs --scene mi-escena.html`.
