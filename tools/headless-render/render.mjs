#!/usr/bin/env node
// Bucle de renderizado headless:
//   1. abre la escena en Chromium sin interfaz,
//   2. para cada fotograma llama a window.renderFrame(frame, time),
//   3. captura el canvas con Chrome (screenshot PNG vía CDP),
//   4. envía cada PNG por stdin a ffmpeg, que lo codifica a MP4 (H.264).
//
// Uso:
//   node render.mjs [--scene scene.html] [--out out.mp4] [--fps 30]
//                   [--duration 5] [--width 1280] [--height 720]
//                   [--frames-dir dir]   (opcional: guarda también los PNG)
//                   [--audio pista.wav]  (opcional: mezcla una pista de audio)
//
// Si se omite --duration y la escena define window.SCENE_DURATION, se usa esa.
// La escena se sirve por HTTP local para que pueda cargar fuentes e imágenes
// relativas a su carpeta.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values: opts } = parseArgs({
  options: {
    scene: { type: 'string', default: 'scene.html' },
    out: { type: 'string', default: 'out.mp4' },
    fps: { type: 'string', default: '30' },
    duration: { type: 'string' },
    width: { type: 'string', default: '1280' },
    height: { type: 'string', default: '720' },
    'frames-dir': { type: 'string' },
    audio: { type: 'string' },
  },
});

const fps = Number(opts.fps);
const width = Number(opts.width);
const height = Number(opts.height);
const scenePath = path.resolve(opts.scene);
const outPath = path.resolve(opts.out);
const framesDir = opts['frames-dir'] && path.resolve(opts['frames-dir']);
const audioPath = opts.audio && path.resolve(opts.audio);

if (![fps, width, height].every((n) => Number.isFinite(n) && n > 0)) {
  console.error('Parámetros numéricos inválidos');
  process.exit(1);
}
if (width % 2 || height % 2) {
  // yuv420p exige dimensiones pares.
  console.error('width y height deben ser pares');
  process.exit(1);
}

// Usa el Chromium preinstalado si existe; si no, el que trae Playwright.
const PREINSTALLED = '/opt/pw-browsers/chromium';
const launchOpts = { headless: true };
if (existsSync(PREINSTALLED) && !process.env.PLAYWRIGHT_BROWSERS_PATH) {
  launchOpts.executablePath = PREINSTALLED;
}

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml', '.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2',
};

// Servidor estático mínimo sobre la carpeta de la escena (solo 127.0.0.1).
function serveDir(root) {
  const server = createServer(async (req, res) => {
    const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const file = path.join(root, path.normalize(rel));
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const body = await readFile(file);
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' }).end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function startFfmpeg() {
  const args = [
    '-y', '-loglevel', 'error',
    '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'png', '-i', 'pipe:0',
    ...(audioPath ? ['-i', audioPath] : []),
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
    ...(audioPath ? ['-c:a', 'aac', '-b:a', '192k', '-shortest'] : []),
    outPath,
  ];
  const proc = spawn('ffmpeg', args, { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((resolve, reject) => {
    proc.on('error', reject);
    proc.on('close', (code) =>
      code === 0 ? resolve() : reject(new Error(`ffmpeg salió con código ${code}`)));
  });
  return { proc, done };
}

// Escribe en stdin de ffmpeg respetando la contrapresión del pipe.
function writeFrame(stream, buf) {
  return new Promise((resolve, reject) => {
    const ok = stream.write(buf, (err) => err && reject(err));
    if (ok) resolve();
    else stream.once('drain', resolve);
  });
}

async function main() {
  if (framesDir) await mkdir(framesDir, { recursive: true });

  const server = await serveDir(path.dirname(scenePath));
  const browser = await chromium.launch(launchOpts);
  let ffmpeg, totalFrames;
  const started = Date.now();

  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    page.on('pageerror', (err) => console.error('[escena]', err.message));
    const { port } = server.address();
    await page.goto(`http://127.0.0.1:${port}/${encodeURIComponent(path.basename(scenePath))}`, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);

    const duration = Number(opts.duration ?? (await page.evaluate(() => window.SCENE_DURATION)) ?? 5);
    totalFrames = Math.round(duration * fps);
    if (!(totalFrames > 0)) throw new Error(`Duración inválida: ${duration}`);
    ffmpeg = startFfmpeg();
    await page.evaluate((cfg) => window.setup(cfg), { width, height, fps, totalFrames });

    const canvas = page.locator('canvas').first();

    for (let frame = 0; frame < totalFrames; frame++) {
      const time = frame / fps;

      // Pinta el fotograma (renderFrame puede ser async) y espera a que
      // Chrome lo haya presentado antes de capturar.
      await page.evaluate(async ({ frame, time }) => {
        await window.renderFrame(frame, time);
        await new Promise((r) => requestAnimationFrame(() => r()));
      }, { frame, time });

      const png = await canvas.screenshot({ type: 'png', animations: 'disabled' });

      if (framesDir) {
        await writeFile(path.join(framesDir, `frame_${String(frame).padStart(5, '0')}.png`), png);
      }
      await writeFrame(ffmpeg.proc.stdin, png);

      if (frame % fps === 0 || frame === totalFrames - 1) {
        const pct = (((frame + 1) / totalFrames) * 100).toFixed(1);
        process.stdout.write(`\rfotograma ${frame + 1}/${totalFrames} (${pct}%)`);
      }
    }
    process.stdout.write('\n');
  } finally {
    ffmpeg?.proc.stdin.end();
    await browser.close();
    server.close();
  }

  await ffmpeg.done;
  const secs = ((Date.now() - started) / 1000).toFixed(1);
  console.log(`MP4 listo: ${outPath} (${totalFrames} fotogramas, ${fps} fps, ${secs}s de render)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
