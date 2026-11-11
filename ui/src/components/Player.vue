<script setup lang="ts">
// Lecteur : vidéo proxy + calque des zones de flou. La tête de lecture est une image SOURCE
// exacte ; la lecture enchaîne les passages gardés en sautant ce qui est coupé.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { ZoneTuple } from "../api";
import {
  current, duration, formatTime, kindLabel, notify, project, saveManual, seekTime, segments,
  state, time, trackById, zonesNow,
} from "../store";

const video = ref<HTMLVideoElement>();
const canvas = ref<HTMLCanvasElement>();
const stage = ref<HTMLDivElement>();
const exact = ref<"" | "blur" | "source">("");
const exactLoading = ref(false);
let loadedClip = "";
let frameCallback = 0;
let drag: { x0: number; y0: number; x1: number; y1: number } | null = null;
const scratch = document.createElement("canvas");

const clip = computed(() => current.value?.clip);
const muted = computed(() => {
  const s = current.value;
  if (!s || !project.value) return false;
  const t = state.frame / s.fps;
  return project.value.mutes.some((m) => m.clip === s.clip.id && t >= m.start && t < m.end);
});
const exactUrl = computed(() => {
  const s = current.value;
  if (!s || !exact.value) return "";
  return `/api/frame?clip=${s.clip.id}&frame=${state.frame}&blur=${exact.value === "blur" ? 1 : 0}&w=2560&v=${state.data.version}`;
});

function mediaTime(frame: number, fps: number): number {
  return (frame + 0.5) / fps; // milieu de l'image : jamais d'arrondi vers l'image précédente
}

function sync(): void {
  const el = video.value;
  const s = current.value;
  if (!el || !s) return;
  if (!s.clip.proxy) return;
  if (loadedClip !== s.clip.id) {
    loadedClip = s.clip.id;
    el.src = `/media/${s.clip.id}`;
  }
  const target = mediaTime(state.frame, s.fps);
  if (Math.abs(el.currentTime - target) > 0.25 / s.fps) el.currentTime = target;
  el.muted = muted.value;
  draw();
}

function onVideoFrame(_now: number, meta: { mediaTime: number }): void {
  const el = video.value;
  const s = current.value;
  if (!el || !s) return;
  if (state.playing) {
    const frame = Math.round(meta.mediaTime * s.fps);
    if (frame >= s.outFrame) {
      const next = segments.value[segments.value.indexOf(s) + 1];
      if (next) {
        state.seg = next.index;
        state.frame = next.inFrame;
        sync();
      } else {
        pause();
        state.frame = s.outFrame - 1;
      }
    } else if (frame >= s.inFrame) {
      state.frame = frame;
    }
    el.muted = muted.value;
  }
  draw();
  schedule();
}

function schedule(): void {
  const el = video.value as (HTMLVideoElement & { requestVideoFrameCallback?: (cb: typeof onVideoFrame) => number }) | undefined;
  if (!el) return;
  if (el.requestVideoFrameCallback) frameCallback = el.requestVideoFrameCallback(onVideoFrame);
  else frameCallback = window.requestAnimationFrame((now) => onVideoFrame(now, { mediaTime: el.currentTime }));
}

async function play(): Promise<void> {
  const el = video.value;
  if (!el || !current.value) return;
  if (time.value >= duration.value - 1.5 / current.value.fps) seekTime(0);
  exact.value = "";
  sync();
  state.playing = true;
  try {
    await el.play();
  } catch {
    state.playing = false;
  }
}

function pause(): void {
  state.playing = false;
  video.value?.pause();
}

function toggle(): void {
  if (state.playing) pause();
  else void play();
}

function step(frames: number): void {
  pause();
  const s = current.value;
  if (!s) return;
  const target = state.frame + frames;
  if (target < s.inFrame || target >= s.outFrame) seekTime(time.value + frames / s.fps);
  else state.frame = target;
}

// --- Calque -----------------------------------------------------------------

const colors: Record<string, string> = { face: "#4ade80", plate: "#38bdf8", dash: "#f472b6", manual: "#ffffff" };

function shapePath(ctx: CanvasRenderingContext2D, z: ZoneTuple, k: number): void {
  const [x1, y1, x2, y2, shape, radius] = z;
  ctx.beginPath();
  if (shape === "ellipse") ctx.ellipse(((x1 + x2) / 2) * k, ((y1 + y2) / 2) * k, ((x2 - x1) / 2) * k, ((y2 - y1) / 2) * k, 0, 0, Math.PI * 2);
  else if (shape === "rounded" && radius > 0) ctx.roundRect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k, radius * k);
  else ctx.rect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
}

function draw(): void {
  const el = video.value;
  const cv = canvas.value;
  const s = current.value;
  if (!el || !cv || !s) return;
  const rect = el.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * ratio));
  const h = Math.max(1, Math.round(rect.height * ratio));
  if (cv.width !== w || cv.height !== h) {
    cv.width = w;
    cv.height = h;
  }
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);
  const k = w / s.clip.width;
  const zones = state.preview === "off" ? [] : zonesNow(s.clip.id, state.frame, draw);
  for (const z of zones ?? []) {
    const [x1, y1, x2, y2, , , kind, id, score] = z;
    const selected = state.selectedTrack?.id === id && state.selectedTrack.clip === s.clip.id;
    if (state.preview === "blur" && el.videoWidth) {
      // Aperçu du flou : la zone de la vidéo est réduite à quelques pixels puis ré-agrandie.
      const px = el.videoWidth / s.clip.width;
      const cells = 5;
      const cw = x2 - x1 >= y2 - y1 ? cells : Math.max(1, Math.round((cells * (x2 - x1)) / (y2 - y1)));
      const ch = x2 - x1 >= y2 - y1 ? Math.max(1, Math.round((cells * (y2 - y1)) / (x2 - x1))) : cells;
      scratch.width = cw;
      scratch.height = ch;
      const sc = scratch.getContext("2d");
      if (sc) {
        sc.imageSmoothingEnabled = true;
        sc.drawImage(el, x1 * px, y1 * px, (x2 - x1) * px, (y2 - y1) * px, 0, 0, cw, ch);
        ctx.save();
        shapePath(ctx, z, k);
        ctx.clip();
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(scratch, x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
        ctx.restore();
      }
    }
    if (state.preview === "boxes" || selected) {
      ctx.lineWidth = (selected ? 3 : 1.5) * ratio;
      ctx.strokeStyle = colors[kind] ?? "#fff";
      ctx.setLineDash(score === null && kind !== "manual" ? [6 * ratio, 4 * ratio] : []);
      shapePath(ctx, z, k);
      ctx.stroke();
      ctx.setLineDash([]);
      if (state.preview === "boxes") {
        const label = `${kindLabel[kind] ?? kind}${score === null ? "" : " " + score.toFixed(2)}`;
        ctx.font = `${11 * ratio}px system-ui, sans-serif`;
        const tw = ctx.measureText(label).width + 6 * ratio;
        const ty = Math.max(13 * ratio, y1 * k - 3 * ratio);
        ctx.fillStyle = "rgba(0,0,0,.65)";
        ctx.fillRect(x1 * k, ty - 11 * ratio, tw, 13 * ratio);
        ctx.fillStyle = colors[kind] ?? "#fff";
        ctx.fillText(label, x1 * k + 3 * ratio, ty - 1 * ratio);
      }
    }
  }
  if (drag) {
    ctx.lineWidth = 2 * ratio;
    ctx.strokeStyle = "#fde047";
    ctx.setLineDash([5 * ratio, 4 * ratio]);
    ctx.strokeRect(drag.x0 * k, drag.y0 * k, (drag.x1 - drag.x0) * k, (drag.y1 - drag.y0) * k);
    ctx.setLineDash([]);
  }
}

// --- Tracé à la souris et sélection -----------------------------------------

function toSource(event: PointerEvent): { x: number; y: number } {
  const s = current.value!;
  const rect = canvas.value!.getBoundingClientRect();
  return {
    x: Math.min(Math.max(((event.clientX - rect.left) / rect.width) * s.clip.width, 0), s.clip.width),
    y: Math.min(Math.max(((event.clientY - rect.top) / rect.height) * s.clip.height, 0), s.clip.height),
  };
}

function pointerDown(event: PointerEvent): void {
  const s = current.value;
  if (!s) return;
  const p = toSource(event);
  if (state.tool === "none") {
    // clic simple : sélectionne la zone sous le pointeur (la plus petite si plusieurs)
    const hits = (zonesNow(s.clip.id, state.frame) ?? [])
      .filter((z) => p.x >= z[0] && p.x <= z[2] && p.y >= z[1] && p.y <= z[3])
      .sort((a, b) => (a[2] - a[0]) * (a[3] - a[1]) - (b[2] - b[0]) * (b[3] - b[1]));
    state.selectedTrack = hits.length ? { clip: s.clip.id, id: hits[0][7] } : null;
    draw();
    return;
  }
  pause();
  drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
  canvas.value?.setPointerCapture(event.pointerId);
}

function pointerMove(event: PointerEvent): void {
  if (!drag) return;
  const p = toSource(event);
  drag.x1 = p.x;
  drag.y1 = p.y;
  draw();
}

async function pointerUp(): Promise<void> {
  const s = current.value;
  if (!drag || !s) return;
  const box = [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)].map(Math.round);
  drag = null;
  const tool = state.tool;
  state.tool = "none";
  draw();
  if (box[2] - box[0] < 6 || box[3] - box[1] < 6) {
    notify("Zone trop petite : maintenez le bouton et tracez un rectangle.");
    return;
  }
  const t = state.frame / s.fps;
  if (tool === "manual") {
    // nouvelle zone : de l'image courante jusqu'à 2 s plus tard (ou la fin du passage)
    const end = Math.min(t + 2, (s.outFrame - 1) / s.fps);
    await saveManual(s.clip.id, [[t, ...box], [Math.max(end, t + 1 / s.fps), ...box]]);
    notify("Zone ajoutée pour 2 s. Réglez son début et sa fin dans le panneau « Zones ».");
  } else if (tool === "dash") {
    state.pendingDash = { clip: s.clip.id, time: t, box };
  } else if (tool === "redraw" && state.selectedTrack) {
    const track = trackById(state.selectedTrack.clip, state.selectedTrack.id);
    if (track?.keyframes) {
      const frames = track.keyframes.filter((kf) => Math.abs(kf[0] - t) > 0.5 / s.fps);
      await saveManual(s.clip.id, [...frames, [t, ...box]], track.id);
    }
  }
}

function onKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement;
  if (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA") return;
  const s = current.value;
  if (!s) return;
  if (event.code === "Space") toggle();
  else if (event.code === "ArrowLeft") step(event.shiftKey ? -Math.round(s.fps) : -1);
  else if (event.code === "ArrowRight") step(event.shiftKey ? Math.round(s.fps) : 1);
  else if (event.code === "Home") seekTime(0);
  else if (event.code === "End") seekTime(duration.value);
  else if (event.code === "Escape") {
    state.tool = "none";
    drag = null;
    draw();
  } else return;
  event.preventDefault();
}

watch(() => [state.seg, state.frame, state.data.version, state.preview, state.selectedTrack?.id], () => {
  if (!state.playing) {
    if (exact.value) exactLoading.value = true;
    sync();
  }
});
watch(() => current.value?.clip.proxy, () => nextTick(sync));
watch(exact, (value) => (exactLoading.value = !!value));

const observer = new ResizeObserver(() => draw());
onMounted(() => {
  window.addEventListener("keydown", onKey);
  if (stage.value) observer.observe(stage.value);
  schedule();
  sync();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  observer.disconnect();
  const el = video.value as (HTMLVideoElement & { cancelVideoFrameCallback?: (id: number) => void }) | undefined;
  el?.cancelVideoFrameCallback?.(frameCallback);
});

defineExpose({ toggle, step, pause });
</script>

<template>
  <div class="player">
    <div ref="stage" class="stage" :class="{ drawing: state.tool !== 'none' }">
      <template v-if="clip">
        <div class="frame" :style="{ aspectRatio: `${clip.width} / ${clip.height}` }">
          <video ref="video" playsinline preload="auto" @seeked="draw" @loadeddata="sync" @ended="pause"></video>
          <img v-if="exactUrl && !state.playing" class="exact" :src="exactUrl" alt="" @load="exactLoading = false" />
          <canvas ref="canvas" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp"></canvas>
          <div v-if="!clip.proxy" class="veil">Préparation de l'aperçu de ce clip…</div>
          <div v-if="state.tool !== 'none'" class="hint">
            {{ state.tool === "dash" ? "Tracez un rectangle autour du compteur" : "Tracez un rectangle sur la zone à flouter" }} — Échap pour annuler
          </div>
          <div v-if="exactLoading && exact && !state.playing" class="hint">Chargement de l'image exacte…</div>
        </div>
      </template>
      <div v-else class="empty">Aucun passage dans le montage.</div>
    </div>
    <div class="transport">
      <button title="Image précédente (←)" @click="step(-1)">◀︎|</button>
      <button class="primary" title="Lecture / pause (Espace)" @click="toggle">{{ state.playing ? "Pause" : "Lecture" }}</button>
      <button title="Image suivante (→)" @click="step(1)">|▶︎</button>
      <span class="clock">{{ formatTime(time) }} / {{ formatTime(duration) }}</span>
      <span v-if="current" class="dim">{{ current.clip.name }} · image {{ state.frame }} · source {{ formatTime(state.frame / current.fps) }}</span>
      <span v-if="muted" class="badge">muet</span>
      <span class="spacer"></span>
      <label class="dim">Zones
        <select v-model="state.preview">
          <option value="boxes">cadres et scores</option>
          <option value="blur">aperçu du flou</option>
          <option value="off">masquées</option>
        </select>
      </label>
      <label class="dim" title="Affiche l'image source exacte en pleine résolution, à l'arrêt">Image exacte
        <select v-model="exact">
          <option value="">non (aperçu rapide)</option>
          <option value="blur">rendu final flouté</option>
          <option value="source">source nette</option>
        </select>
      </label>
    </div>
  </div>
</template>
