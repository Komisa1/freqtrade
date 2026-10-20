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

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const { values: opts } = parseArgs({
  options: {
    scene: { type: 'string', default: 'scene.html' },
    out: { type: 'string', default: 'out.mp4' },
    fps: { type: 'string', default: '30' },
    duration: { type: 'string', default: '5' },
    width: { type: 'string', default: '1280' },
    height: { type: 'string', default: '720' },
    'frames-dir': { type: 'string' },
  },
});

const fps = Number(opts.fps);
const width = Number(opts.width);
const height = Number(opts.height);
const totalFrames = Math.round(Number(opts.duration) * fps);
const scenePath = path.resolve(opts.scene);
const outPath = path.resolve(opts.out);
const framesDir = opts['frames-dir'] && path.resolve(opts['frames-dir']);

if (![fps, width, height, totalFrames].every((n) => Number.isFinite(n) && n > 0)) {
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

function startFfmpeg() {
  const args = [
    '-y', '-loglevel', 'error',
    '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'png', '-i', 'pipe:0',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
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

  const browser = await chromium.launch(launchOpts);
  const ffmpeg = startFfmpeg();
  const started = Date.now();

  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    page.on('pageerror', (err) => console.error('[escena]', err.message));
    await page.goto(pathToFileURL(scenePath).href, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
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
    ffmpeg.proc.stdin.end();
    await browser.close();
  }

  await ffmpeg.done;
  const secs = ((Date.now() - started) / 1000).toFixed(1);
  console.log(`MP4 listo: ${outPath} (${totalFrames} fotogramas, ${fps} fps, ${secs}s de render)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
