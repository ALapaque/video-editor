<script setup lang="ts">
// Lecteur : vidéo proxy + calque des zones de flou. La tête de lecture est une image SOURCE
// exacte ; la lecture enchaîne les passages gardés en sautant ce qui est coupé.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { ZoneTuple } from "../api";
import Icon from "./Icon.vue";
import { snapshot } from "../extras";
import { loop, objectHere, outline, rate, removeZone, resume, review, speeds } from "../review";
import {
  start,
  current, droppedNow, duration, formatTime, kindLabel, notify, objectNames, player, project, saveManual, seekTime, segments,
  state, time, trackById, zonesNow,
} from "../store";

// `simple` : lecteur de l'assistant du mode simple, sans barre de commandes (l'assistant a les siennes).
defineProps<{ simple?: boolean }>();

const video = ref<HTMLVideoElement>();
const canvas = ref<HTMLCanvasElement>();
const stage = ref<HTMLDivElement>();
const exact = ref<"" | "blur" | "source">("");
const exactLoading = ref(false);
let loadedClip = "";
let frameCallback = 0;
let drag: { x0: number; y0: number; x1: number; y1: number } | null = null;
const scratch = document.createElement("canvas");
const droppedCount = ref(0); // nombre de contours de détections écartées tracés au dernier dessin

// Volume de l'aperçu : ne concerne que l'écoute dans le lecteur, jamais le projet ni l'export.
// Mémorisé dans le navigateur ; 50 % à la première utilisation.
const VOLUME_KEY = "moto-blur:volume-apercu";
function storedVolume(): { level: number; muted: boolean } {
  try {
    const saved = JSON.parse(localStorage.getItem(VOLUME_KEY) ?? "null");
    if (saved && typeof saved.level === "number" && saved.level >= 0 && saved.level <= 100) {
      return { level: Math.round(saved.level), muted: saved.muted === true };
    }
  } catch {
    /* stockage indisponible ou valeur illisible : réglage par défaut */
  }
  return { level: 50, muted: false };
}
const initialVolume = storedVolume();
const volume = ref(initialVolume.level); // 0 à 100
const userMuted = ref(initialVolume.muted);
const silent = computed(() => userMuted.value || volume.value === 0);

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
    el.volume = volume.value / 100;
  }
  if (el.playbackRate !== rate.value) el.playbackRate = rate.value; // une nouvelle source remet la vitesse à 1
  const target = mediaTime(state.frame, s.fps);
  if (Math.abs(el.currentTime - target) > 0.25 / s.fps) el.currentTime = target;
  el.muted = muted.value || userMuted.value;
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
      } else if (loop.value) {
        restartLoop();
      } else {
        pause();
        state.frame = s.outFrame - 1;
      }
    } else if (frame >= s.inFrame) {
      state.frame = frame;
      // vérification guidée : le passage affiché tourne en boucle (simple comparaison, aucune mise en page)
      if (loop.value && time.value >= loop.value.to) restartLoop();
    }
    el.muted = muted.value || userMuted.value;
  }
  draw();
  schedule();
}

function restartLoop(): void {
  if (!loop.value) return;
  seekTime(loop.value.from);
  sync();
}

function onEnded(): void {
  if (state.playing && loop.value) {
    restartLoop();
    void video.value?.play();
  } else pause();
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

function applyVolume(): void {
  const el = video.value;
  if (!el) return;
  el.volume = volume.value / 100;
  // passage muet du montage OU aperçu coupé par l'utilisateur
  el.muted = muted.value || userMuted.value;
}

function toggleSound(): void {
  if (!userMuted.value && volume.value === 0) volume.value = 50; // curseur à zéro : le bouton rend le son
  else userMuted.value = !userMuted.value;
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
  const contour = shape === "contour" ? z[10] : undefined;
  ctx.beginPath();
  if (contour && contour.length >= 3) {
    // contour précis d'un objet suivi : polygone fermé, en pixels de l'image source
    ctx.moveTo(contour[0][0] * k, contour[0][1] * k);
    for (let i = 1; i < contour.length; i++) ctx.lineTo(contour[i][0] * k, contour[i][1] * k);
    ctx.closePath();
  } else if (shape === "ellipse") ctx.ellipse(((x1 + x2) / 2) * k, ((y1 + y2) / 2) * k, ((x2 - x1) / 2) * k, ((y2 - y1) / 2) * k, 0, 0, Math.PI * 2);
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
  // vérification guidée : les zones du passage affiché sont entourées par-dessus l'aperçu du flou ;
  // toutes le sont quand il faut en cliquer une pour la retirer
  const marked = outline.value && outline.value.clip === s.clip.id ? outline.value.ids : null;
  const outlineAll = review.sub === "extra";
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
    if (state.preview === "boxes" || selected || outlineAll || (marked && marked.has(id))) {
      ctx.lineWidth = (selected ? 3 : 1.5) * ratio;
      ctx.strokeStyle = colors[kind] ?? "#fff";
      ctx.setLineDash(score === null && kind !== "manual" ? [6 * ratio, 4 * ratio] : []);
      shapePath(ctx, z, k);
      ctx.stroke();
      ctx.setLineDash([]);
      if (state.preview === "boxes") {
        // un objet désigné à la main porte son nom (compteur, téléphone…)
        const name = kind === "dash" ? (objectNames.value.get(`${s.clip.id}:${id}`) ?? kindLabel[kind]) : (kindLabel[kind] ?? kind);
        const label = `${name}${score === null ? "" : " " + score.toFixed(2)}`;
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
  if (state.showDropped) {
    // détections écartées par le score minimal : contours gris fins, pour décider de les flouter ou non
    const dropped = droppedNow(s.clip.id, state.frame, draw) ?? [];
    ctx.lineWidth = 1 * ratio;
    ctx.strokeStyle = "#c3c9d1";
    ctx.setLineDash([4 * ratio, 4 * ratio]);
    ctx.font = `${10 * ratio}px system-ui, sans-serif`;
    for (const [x1, y1, x2, y2, , id] of dropped) {
      ctx.strokeRect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
      const label = `n° ${id}`;
      const tw = ctx.measureText(label).width + 6 * ratio;
      const ty = Math.max(12 * ratio, y1 * k - 3 * ratio);
      ctx.fillStyle = "rgba(0,0,0,.6)";
      ctx.fillRect(x1 * k, ty - 10 * ratio, tw, 12 * ratio);
      ctx.fillStyle = "#c3c9d1";
      ctx.fillText(label, x1 * k + 3 * ratio, ty - 1 * ratio);
    }
    ctx.setLineDash([]);
    if (droppedCount.value !== dropped.length) droppedCount.value = dropped.length;
  }
  const waiting = state.pendingDash ?? state.pendingBlur;
  if (waiting && waiting.clip === s.clip.id && !drag) {
    // rectangle tracé, en attente du nom de l'objet ou du choix « suivre » / « zone fixe »
    const [x1, y1, x2, y2] = waiting.box;
    ctx.lineWidth = 2 * ratio;
    ctx.strokeStyle = "#fde047";
    ctx.setLineDash([5 * ratio, 4 * ratio]);
    ctx.strokeRect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
    ctx.setLineDash([]);
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
    if (review.sub === "extra") {
      // « Un flou est en trop » : le clic retire la zone visée
      if (hits.length) void removeZone(s.clip.id, hits[0][7]);
      else notify("Cliquez directement sur un flou entouré dans l'image.");
      return;
    }
    if (state.mode === "simple") return; // pas de sélection de zone dans l'assistant
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
  const guidedLoop = review.sub === "missing" ? loop.value : null;
  if (box[2] - box[0] < 6 || box[3] - box[1] < 6) {
    notify("Zone trop petite : maintenez le bouton et tracez un rectangle.");
    if (guidedLoop) state.tool = "manual"; // on reste sur « Il manque un flou » : nouvel essai
    return;
  }
  const t = state.frame / s.fps;
  if (tool === "manual" && state.mode !== "simple") {
    // mode avancé : le rectangle attend le choix « Suivre ce que j'ai entouré » ou « Zone fixe ».
    // La zone fixe reste celle d'avant : tout le passage en boucle pendant la vérification, 2 s sinon.
    let from = t;
    let to = Math.min(t + 2, (s.outFrame - 1) / s.fps);
    if (guidedLoop) {
      const base = s.inFrame / s.fps - s.offset;
      from = Math.max(guidedLoop.from + base, s.inFrame / s.fps);
      to = Math.min(guidedLoop.to + base, (s.outFrame - 1) / s.fps);
    }
    state.pendingBlur = {
      clip: s.clip.id, time: t, box, start: s.inFrame / s.fps, end: s.outFrame / s.fps,
      fixed: [[from, ...box], [Math.max(to, from + 1 / s.fps), ...box]], guided: !!guidedLoop,
    };
  } else if (tool === "manual" && guidedLoop) {
    // vérification guidée : la zone couvre tout le passage en boucle (dans le passage du montage affiché)
    const base = s.inFrame / s.fps - s.offset;
    const from = Math.max(guidedLoop.from + base, s.inFrame / s.fps);
    const to = Math.min(guidedLoop.to + base, (s.outFrame - 1) / s.fps);
    await saveManual(s.clip.id, [[from, ...box], [Math.max(to, from + 1 / s.fps), ...box]]);
    notify(state.mode === "simple"
      ? "Flou ajouté sur tout le passage. Regardez la vidéo : s'il ne couvre pas tout, ajoutez-en un autre."
      : "Zone ajoutée sur tout le passage. Regardez la boucle : si l'objet bouge, retracez-la à un autre instant avec « Déplacer ici… » dans l'onglet « Zones ».");
    resume();
  } else if (tool === "manual") {
    // nouvelle zone : de l'image courante jusqu'à 2 s plus tard (ou la fin du passage)
    const end = Math.min(t + 2, (s.outFrame - 1) / s.fps);
    await saveManual(s.clip.id, [[t, ...box], [Math.max(end, t + 1 / s.fps), ...box]]);
    notify("Zone ajoutée pour 2 s. Réglez son début et sa fin dans le panneau « Zones ».");
  } else if (tool === "dash" && review.sub === "object" && objectHere.value) {
    // vérification : l'objet est re-suivi sur ce plan seulement, le reste de son suivi est gardé
    const label = objectHere.value;
    resume();
    await start("/api/dash", { clip: s.clip.id, time: t, box, mode: "track", label, direction: "both", scope: "shot" });
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
  if (state.jobOpen || state.pendingBlur || review.sheet) return; // un dialogue est ouvert : le clavier lui appartient
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
    if (review.sub !== "ask") resume(); // abandon de la correction : retour à la question
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
watch(rate, (value) => video.value && (video.value.playbackRate = value));
watch(() => [review.sub, review.current, state.showDropped, state.pendingDash, state.pendingBlur], draw);

// Capture : l'image affichée est enregistrée en PNG pleine résolution à côté du projet, floutée
// comme à l'export (nette seulement si « Image exacte » montre la source nette).
const snapping = ref(false);
async function snap(): Promise<void> {
  const s = current.value;
  if (!s || snapping.value) return;
  pause();
  snapping.value = true;
  await snapshot(s.clip.id, state.frame, exact.value !== "source");
  snapping.value = false;
}
watch([volume, userMuted], () => {
  applyVolume();
  try {
    localStorage.setItem(VOLUME_KEY, JSON.stringify({ level: volume.value, muted: userMuted.value }));
  } catch {
    /* stockage indisponible : le volume n'est simplement pas mémorisé */
  }
});

// Taille de l'image dans la scène : la plus grande qui garde les proportions de la vidéo. Calculée
// au redimensionnement seulement (jamais à chaque image) ; sans elle, une scène plus haute que la
// vidéo déformait le calque et décalait les flous par rapport à l'image.
const fit = ref<{ width: string; height: string } | null>(null);
function measure(): void {
  const el = stage.value;
  const c = clip.value;
  if (!el || !c || !el.clientWidth || !el.clientHeight) return;
  const width = Math.min(el.clientWidth, (el.clientHeight * c.width) / c.height);
  fit.value = { width: `${width}px`, height: `${(width * c.height) / c.width}px` };
}
const observer = new ResizeObserver(() => {
  measure();
  void nextTick(draw);
});
watch(() => [clip.value?.width, clip.value?.height], measure);
onMounted(() => {
  window.addEventListener("keydown", onKey);
  if (stage.value) observer.observe(stage.value);
  player.play = play;
  player.pause = pause;
  schedule();
  applyVolume();
  sync();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  observer.disconnect();
  const el = video.value as (HTMLVideoElement & { cancelVideoFrameCallback?: (id: number) => void }) | undefined;
  el?.cancelVideoFrameCallback?.(frameCallback);
});

defineExpose({ toggle, step, pause, snap });
</script>

<template>
  <div class="player">
    <div ref="stage" class="stage" :class="{ drawing: state.tool !== 'none', picking: review.sub === 'extra' }">
      <template v-if="clip">
        <div class="frame" :style="fit ?? { aspectRatio: `${clip.width} / ${clip.height}` }">
          <video ref="video" playsinline preload="auto" @seeked="draw" @loadeddata="sync" @ended="onEnded"></video>
          <img v-if="exactUrl && !state.playing" class="exact" :src="exactUrl" alt="" @load="exactLoading = false" />
          <canvas ref="canvas" :data-dropped="state.showDropped ? droppedCount : undefined" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp"></canvas>
          <div v-if="!clip.proxy" class="veil"><span class="spinner"></span>Préparation de l'aperçu de ce clip…</div>
          <!-- assistant : pas de barre de commandes, la capture apparaît au survol dans le coin de l'image -->
          <button
            v-if="simple && clip.proxy && state.tool === 'none' && review.sub !== 'extra'" class="icon-btn snap-float" :disabled="snapping"
            title="Enregistrer cette image, floutée, à côté de la vidéo" aria-label="Enregistrer cette image, floutée" @click="snap"
          ><span v-if="snapping" class="spinner small"></span><Icon v-else name="camera" /></button>
          <Transition name="hint">
            <div v-if="state.tool !== 'none' && simple" class="hint tool">{{ state.tool === "dash" ? "Entourez l'objet avec la souris" : "Entourez avec la souris ce qui doit être flouté" }}</div>
            <div v-else-if="state.tool !== 'none'" class="hint tool">
              {{ state.tool === "dash" ? "Tracez un rectangle autour de l'objet à flouter" : "Tracez un rectangle sur la zone à flouter" }} — Échap pour annuler
            </div>
            <div v-else-if="review.sub === 'extra'" class="hint tool">{{ simple ? "Cliquez sur le flou à retirer" : "Cliquez le flou à retirer — Échap pour annuler" }}</div>
            <div v-else-if="exactLoading && exact && !state.playing" class="hint"><span class="spinner small"></span>Chargement de l'image exacte…</div>
          </Transition>
        </div>
      </template>
      <div v-else class="empty">
        <Icon name="film" :size="28" />
        <strong>Aucun passage dans le montage.</strong>
        <span>Ajoutez un clip pour continuer.</span>
      </div>
    </div>
    <div v-if="!simple" class="transport">
      <span class="cluster">
        <button class="icon-btn" title="Image précédente (←)" aria-label="Image précédente" @click="step(-1)"><Icon name="stepBack" /></button>
        <button class="primary play" title="Lecture / pause (Espace)" @click="toggle"><Icon :name="state.playing ? 'pause' : 'play'" />{{ state.playing ? "Pause" : "Lecture" }}</button>
        <button class="icon-btn" title="Image suivante (→)" aria-label="Image suivante" @click="step(1)"><Icon name="stepForward" /></button>
      </span>
      <select v-model.number="review.speed" class="speed-select" aria-label="Vitesse de l'aperçu" title="Vitesse de l'aperçu (sans effet sur l'export)">
        <option v-for="s in speeds" :key="s.value" :value="s.value">{{ s.label }}</option>
      </select>
      <span class="timecode">
        <span class="clock">{{ formatTime(time) }} <span class="clock-total">/ {{ formatTime(duration) }}</span></span>
        <span v-if="current" class="dim meta" :title="`${current.clip.name} · image ${state.frame} · source ${formatTime(state.frame / current.fps)}`">{{ current.clip.name }} · image {{ state.frame }} · source {{ formatTime(state.frame / current.fps) }}</span>
      </span>
      <span v-if="muted" class="badge"><Icon name="volumeOff" :size="12" />muet</span>
      <span class="volume" title="Volume de l'aperçu (sans effet sur l'export)">
        <button class="icon-btn" :class="{ on: userMuted }" :aria-pressed="userMuted" :aria-label="userMuted ? 'Rétablir le son de l\'aperçu' : 'Couper le son de l\'aperçu'" @click="toggleSound"><Icon :name="silent ? 'volumeOff' : 'volume'" /></button>
        <input v-model.number="volume" type="range" min="0" max="100" step="1" aria-label="Volume de l'aperçu" :aria-valuetext="`${volume} %`" @pointerup="($event.target as HTMLInputElement).blur()" />
        <span class="mono volume-value">{{ volume }} %</span>
      </span>
      <label class="dim field" title="Affichage des zones de flou sur l'aperçu"><span class="field-label">Zones</span>
        <select v-model="state.preview" aria-label="Zones">
          <option value="boxes">cadres et scores</option>
          <option value="blur">aperçu du flou</option>
          <option value="off">masquées</option>
        </select>
      </label>
      <label class="dim field" title="Affiche l'image source exacte en pleine résolution, à l'arrêt"><span class="field-label">Image exacte</span>
        <select v-model="exact" aria-label="Image exacte">
          <option value="">non (aperçu rapide)</option>
          <option value="blur">rendu final flouté</option>
          <option value="source">source nette</option>
        </select>
      </label>
      <button
        class="icon-btn snap" :disabled="snapping || !clip"
        :title="exact === 'source' ? 'Enregistrer cette image en PNG, nette (« Image exacte » est sur « source nette »)' : 'Enregistrer cette image en PNG, floutée comme à l\'export, à côté du projet'"
        :aria-label="exact === 'source' ? 'Enregistrer cette image, nette' : 'Enregistrer cette image, floutée'" @click="snap"
      ><span v-if="snapping" class="spinner small"></span><Icon v-else name="camera" /></button>
    </div>
  </div>
</template>
