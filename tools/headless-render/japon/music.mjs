#!/usr/bin/env node
// Genera la música del video "Japón en Navidad" como WAV estéreo 44.1 kHz.
// Todo es síntesis determinista (sin samples): koto con Karplus-Strong en
// escala Yo (pentatónica japonesa), pad, taiko, cascabeles navideños,
// campana de templo (bonshō) y truenos de fuegos artificiales.
// Los tiempos replican la línea de tiempo de japon.html para ir sincronizados.
//
// Uso: node music.mjs [salida.wav] [duración=62]

import { writeFileSync } from 'node:fs';

const OUT = process.argv[2] || 'musica.wav';
const DUR = Number(process.argv[3] || 62);
const SR = 44100;
const N = Math.ceil(SR * DUR);
const L = new Float32Array(N), R = new Float32Array(N), SEND = new Float32Array(N);

const BPM = 80, BEAT = 60 / BPM, BAR = BEAT * 4, EIGHTH = BEAT / 2;
// Línea de tiempo (s) — igual que en japon.html
const T_DATES = 6, T_ROUTE = 12, T_DAYS = 18, DAY_LEN = 3, T_PRICE = 45, T_OUTRO = 54;
const T_BELL_DAY = T_DAYS + 6 * DAY_LEN; // día 7, Nochevieja

function rng(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12);

function place(sig, t0, gain, pan = 0, rev = 0.3) {
  const i0 = Math.round(t0 * SR);
  const gl = gain * Math.cos((pan + 1) * Math.PI / 4), gr = gain * Math.sin((pan + 1) * Math.PI / 4);
  for (let k = 0; k < sig.length; k++) {
    const i = i0 + k;
    if (i < 0) continue;
    if (i >= N) break;
    L[i] += sig[k] * gl; R[i] += sig[k] * gr; SEND[i] += sig[k] * gain * rev;
  }
}

// ---------------------------------------------------------------- instrumentos
function koto(freq, dur, seed, bright = 0.5) {
  const n = Math.floor(dur * SR), out = new Float32Array(n);
  const p = Math.max(2, Math.round(SR / freq)), buf = new Float32Array(p), r = rng(seed);
  let prev = 0;
  for (let i = 0; i < p; i++) { prev += ((r() * 2 - 1) - prev) * bright; buf[i] = prev; }
  let idx = 0;
  for (let i = 0; i < n; i++) {
    const a = buf[idx], b = buf[(idx + 1) % p];
    buf[idx] = (a + b) * 0.5 * 0.997;
    out[i] = a * (i < 40 ? i / 40 : 1);
    idx = (idx + 1) % p;
  }
  return out;
}

function taiko(dur = 1.4, seed = 1) {
  const n = Math.floor(dur * SR), out = new Float32Array(n), r = rng(seed);
  let ph = 0, lp = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    ph += 2 * Math.PI * (48 + 80 * Math.exp(-t * 20)) / SR;
    lp += ((r() * 2 - 1) - lp) * 0.08;
    out[i] = Math.sin(ph) * Math.exp(-t * 4.5) + lp * 2.2 * Math.exp(-t * 35);
  }
  return out;
}

function bonsho(dur = 7, base = 98) {
  const parts = [[0.5, 0.9, 7], [1, 1, 6], [1.19, 0.55, 4.5], [1.56, 0.45, 3.5], [2.0, 0.35, 3], [2.51, 0.25, 2.4], [3.01, 0.2, 1.8], [4.13, 0.12, 1.2]];
  const n = Math.floor(dur * SR), out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / SR, att = Math.min(1, t / 0.004);
    let v = 0;
    for (const [ratio, amp, dec] of parts) {
      const f = base * ratio;
      v += amp * Math.exp(-t / dec * 1.2) * (Math.sin(2 * Math.PI * f * t) + 0.5 * Math.sin(2 * Math.PI * (f + 0.9) * t));
    }
    out[i] = v * att * 0.35;
  }
  return out;
}

function tink(freq, dur = 1.2) {
  const n = Math.floor(dur * SR), out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    out[i] = Math.sin(2 * Math.PI * freq * t) * Math.exp(-t * 3) + 0.4 * Math.sin(2 * Math.PI * freq * 2.76 * t) * Math.exp(-t * 7) + 0.2 * Math.sin(2 * Math.PI * freq * 5.4 * t) * Math.exp(-t * 12);
  }
  return out;
}

function sleigh(seed) {
  const n = Math.floor(0.16 * SR), out = new Float32Array(n), r = rng(seed);
  let prev = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR, x = r() * 2 - 1, hp = x - prev; prev = x;
    out[i] = hp * Math.exp(-t * 28) * (0.6 + 0.4 * Math.sin(2 * Math.PI * 34 * t)) + 0.3 * Math.sin(2 * Math.PI * 5200 * t) * Math.exp(-t * 30);
  }
  return out;
}

function boom(seed) {
  const n = Math.floor(1.6 * SR), out = new Float32Array(n), r = rng(seed);
  let lp = 0, lp2 = 0;
  for (let i = 0; i < n; i++) {
    const t = i / SR;
    lp += ((r() * 2 - 1) - lp) * 0.05; lp2 += (lp - lp2) * 0.1;
    const crackle = r() < 0.004 * Math.exp(-t * 2) ? (r() * 2 - 1) * 0.5 : 0;
    out[i] = lp2 * 6 * Math.exp(-t * 3.5) + crackle;
  }
  return out;
}

function pad(notes, t0, dur, gain) {
  const i0 = Math.round(t0 * SR), n = Math.floor(dur * SR);
  for (let k = 0; k < n; k++) {
    const i = i0 + k;
    if (i >= N) break;
    const t = k / SR, env = Math.min(1, t / 1.5, (dur - t) / 1.5);
    let v = 0;
    for (const m of notes) {
      const f = mtof(m);
      v += Math.sin(2 * Math.PI * f * t) + 0.6 * Math.sin(2 * Math.PI * f * 1.003 * t + 1) + 0.15 * Math.sin(2 * Math.PI * f * 2 * t);
    }
    v *= gain * env * (0.85 + 0.15 * Math.sin(2 * Math.PI * 0.2 * (t0 + t)));
    L[i] += v; R[i] += v * 0.96; SEND[i] += v * 0.5;
  }
}

// ---------------------------------------------------------------- composición
// Escala Yo en Re: Re Mi Sol La Si
const SCALE = [62, 64, 67, 69, 71, 74, 76, 79, 81, 83];
const CHORDS = [[50, 57, 62, 64], [43, 50, 55, 59], [40, 47, 55, 62], [45, 52, 57, 62]]; // D, G, Em, Asus4
const ROOTS = [38, 43, 40, 45];
const bars = Math.ceil(DUR / BAR);

// Pad: un acorde cada 2 compases
for (let b = 0; b < bars; b += 2) {
  const ch = CHORDS[(b / 2) % CHORDS.length];
  pad(ch, b * BAR, BAR * 2 + 1.5, b * BAR < T_DATES ? 0.012 : 0.016);
}

// Melodía de koto: frases de 2 compases, cada una se repite con final resuelto
const RHYTHMS = [
  [1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0, 0],
  [1, 0, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1, 1, 0, 0, 0],
  [1, 1, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0],
];
const mr = rng(808);
let deg = 4;
for (let b = 0; b < bars; b += 4) {
  const rhythm = RHYTHMS[Math.floor(mr() * RHYTHMS.length)];
  const phrase = [];
  for (let s = 0; s < 16; s++) {
    if (!rhythm[s]) continue;
    deg = Math.max(0, Math.min(SCALE.length - 1, deg + [-2, -1, -1, 1, 1, 2, 0][Math.floor(mr() * 7)]));
    phrase.push([s, deg]);
  }
  for (let rep = 0; rep < 2; rep++) {
    const start = (b + rep * 2) * BAR;
    if (start < 1.5) continue;
    phrase.forEach(([s, d], k) => {
      const t = start + s * EIGHTH;
      if (t >= DUR - 2) return;
      const last = k === phrase.length - 1;
      const m = last && rep === 1 ? 62 + (d > 4 ? 12 : 0) : SCALE[d];
      const sparse = t < T_DATES;
      if (sparse && s % 4) return;
      const gain = t >= T_PRICE && t < T_OUTRO ? 0.42 : 0.34;
      place(koto(mtof(m), 2.5, 1000 + b * 50 + s + rep), t, gain, (m - 70) / 20, 0.35);
      if (t >= T_PRICE && s % 4 === 0) place(koto(mtof(m + 12), 1.5, 3000 + b + s, 0.7), t, 0.12, 0.4, 0.5);
    });
  }
}

// Bajo de koto en los tiempos 1 y 3 desde la escena de fechas
for (let b = 0; b < bars; b++) {
  const t = b * BAR;
  if (t < T_DATES || t >= DUR - 2) continue;
  const root = ROOTS[Math.floor(b / 2) % ROOTS.length];
  place(koto(mtof(root), 2.5, 500 + b, 0.35), t, 0.5, 0, 0.1);
  place(koto(mtof(root + 7), 2, 600 + b, 0.35), t + 2 * BEAT, 0.3, 0, 0.1);
}

// Cascabeles navideños: escena de fechas y final
for (const [a, z] of [[T_DATES, T_ROUTE], [T_OUTRO + 1, DUR - 2.5]]) {
  for (let t = a, k = 0; t < z; t += EIGHTH / 2, k++) {
    const accent = k % 4 === 0 ? 1 : k % 2 === 0 ? 0.6 : 0.35;
    place(sleigh(7000 + k), t, 0.12 * accent, ((k % 3) - 1) * 0.4, 0.2);
  }
}

// Brillos en la intro y al aparecer el título
const tr = rng(4242);
for (let i = 0; i < 14; i++) {
  const t = 0.6 + i * 0.32 + tr() * 0.1;
  place(tink(mtof(SCALE[5 + Math.floor(tr() * 5)] + 12), 1.6), t, 0.07, tr() * 1.6 - 0.8, 0.6);
}

// Taiko: golpes en cada cambio de escena / día y ritmo en la escena del precio
const hits = [T_DATES, T_ROUTE];
for (let d = 0; d < 9; d++) hits.push(T_DAYS + d * DAY_LEN);
hits.push(T_PRICE);
hits.forEach((t, i) => place(taiko(1.4, 50 + i), t, 0.3, 0, 0.25));
for (let t = T_PRICE + BEAT; t < T_OUTRO - BEAT; t += BEAT) place(taiko(0.8, Math.round(t * 10)), t, 0.18, 0, 0.2);
for (let k = 0; k < 8; k++) place(taiko(0.5, 900 + k), T_OUTRO - BEAT * 2 + k * BEAT / 4, 0.18 + k * 0.04, 0, 0.2); // redoble

// Bonshō: las mismas campanadas que se ven en el día 7, y una al empezar el final
for (const s of [0.7, 1.9, 3.1]) place(bonsho(6), T_BELL_DAY + s, 0.3, 0.15, 0.5);
place(bonsho(8, 73.4), T_OUTRO, 0.35, 0, 0.6);

// Fuegos artificiales: mismas semillas que BURSTS en japon.html
{
  const r = rng(2027);
  for (let i = 0; i < 14; i++) {
    const t = 0.4 + i * 0.5 + r() * 0.3;
    const x = 200 + r() * 1520; r(); r(); r();
    place(boom(300 + i), T_OUTRO + t, 0.22, (x - 960) / 960 * 0.7, 0.4);
  }
}

// ---------------------------------------------------------------- reverb + master
function reverb(input, delays, ap) {
  const out = new Float32Array(N);
  for (const d of delays) {
    const buf = new Float32Array(d); let idx = 0, filt = 0;
    for (let i = 0; i < N; i++) {
      const y = buf[idx];
      filt = y * 0.7 + filt * 0.3;
      buf[idx] = input[i] + filt * 0.84;
      out[i] += y / delays.length;
      idx = (idx + 1) % d;
    }
  }
  for (const d of ap) {
    const buf = new Float32Array(d); let idx = 0;
    for (let i = 0; i < N; i++) {
      const b = buf[idx], v = out[i] + b * 0.5;
      out[i] = b - v * 0.5; buf[idx] = v; idx = (idx + 1) % d;
    }
  }
  return out;
}
const revL = reverb(SEND, [1557, 1617, 1491, 1422], [225, 556]);
const revR = reverb(SEND, [1580, 1640, 1514, 1445], [248, 579]);

let peak = 0, dcL = 0, dcR = 0, pl = 0, pr = 0;
for (let i = 0; i < N; i++) {
  let l = L[i] + revL[i] * 0.6, r = R[i] + revR[i] * 0.6;
  dcL = l - pl + 0.995 * dcL; pl = l; l = dcL; // filtro DC
  dcR = r - pr + 0.995 * dcR; pr = r; r = dcR;
  const t = i / SR, fade = Math.min(1, t / 0.4, (DUR - t) / 3.5);
  L[i] = Math.tanh(l * 1.2) * fade; R[i] = Math.tanh(r * 1.2) * fade;
  peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]));
}

const gain = 0.89 / (peak || 1);
const data = Buffer.alloc(44 + N * 4);
data.write('RIFF', 0); data.writeUInt32LE(36 + N * 4, 4); data.write('WAVE', 8);
data.write('fmt ', 12); data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(2, 22);
data.writeUInt32LE(SR, 24); data.writeUInt32LE(SR * 4, 28); data.writeUInt16LE(4, 32); data.writeUInt16LE(16, 34);
data.write('data', 36); data.writeUInt32LE(N * 4, 40);
for (let i = 0; i < N; i++) {
  data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, L[i] * gain)) * 32767), 44 + i * 4);
  data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, R[i] * gain)) * 32767), 46 + i * 4);
}
writeFileSync(OUT, data);
console.log(`Música lista: ${OUT} (${DUR}s, ${SR} Hz estéreo)`);
