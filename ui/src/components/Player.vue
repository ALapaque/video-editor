<script setup lang="ts">
// Lecteur : vidéo proxy + calque des flous. La tête de lecture est une image SOURCE exacte ; la
// lecture enchaîne les passages gardés en sautant ce qui est coupé.
// Sert aux deux modes : `simple` (assistant) n'affiche ni barre de commandes ni légende.
// Zoom : molette (ou pincement), centré sur la souris ; glisser pour se déplacer une fois zoomé.
// Le calque est dessiné avec le même cadrage que la vidéo : tracés, clics, contours et aperçu du
// flou restent justes à tous les niveaux de zoom.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import type { ZoneTuple } from "../api";
import Icon from "./Icon.vue";
import { confidence, confidenceInfo, snapshot } from "../extras";
import { displayName, panels } from "../blurs";
import { loop, objectHere, outline, rate, removeZone, resume, review, speeds } from "../review";
import {
  start,
  current, droppedNow, duration, formatTime, kindLabel, notify, objectNames, player, project, saveManual, seekTime, segments,
  state, time, trackById, zonesNow,
} from "../store";

const props = defineProps<{ simple?: boolean }>();

const video = ref<HTMLVideoElement>();
const canvas = ref<HTMLCanvasElement>();
const stage = ref<HTMLDivElement>();
const frameEl = ref<HTMLDivElement>();
const exact = ref<"" | "blur" | "source">("");
const exactLoading = ref(false);
let loadedClip = "";
let frameCallback = 0;
let drag: { x0: number; y0: number; x1: number; y1: number } | null = null;
const scratch = document.createElement("canvas");
const droppedCount = ref(0); // nombre de contours gris (non floutés) tracés au dernier dessin
const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

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

/** « Voir le flou » / « Voir les contours » : aperçu du flou ou contours des flous. */
function toggleView(): void {
  state.preview = state.preview === "blur" ? "boxes" : "blur";
}

// --- Zoom sur la vidéo ---------------------------------------------------------
// Cadrage en pixels CSS de l'image : point affiché = point de l'image × s + (x, y). La vidéo et
// l'image exacte suivent par une transformation CSS (aucune remise en page) ; le calque applique
// la même transformation à son dessin.
const view = reactive({ s: 1, x: 0, y: 0 });
const zoomed = computed(() => view.s > 1.001);
const MAX_ZOOM = 8;
const viewStyle = computed(() => (zoomed.value ? { transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` } : undefined));

function frameSize(): { w: number; h: number } {
  const r = canvas.value?.getBoundingClientRect();
  return { w: r?.width ?? 0, h: r?.height ?? 0 };
}
function clampView(): void {
  const { w, h } = frameSize();
  if (view.s <= 1.001) {
    view.s = 1;
    view.x = 0;
    view.y = 0;
    return;
  }
  view.x = Math.min(0, Math.max(w - w * view.s, view.x));
  view.y = Math.min(0, Math.max(h - h * view.s, view.y));
}
/** Zoom d'un facteur, le point (cx, cy) de l'image affichée (pixels CSS) restant immobile. */
function zoomAt(factor: number, cx: number, cy: number): void {
  const next = Math.min(MAX_ZOOM, Math.max(1, view.s * factor));
  if (Math.abs(next - view.s) < 1e-4) return;
  const px = (cx - view.x) / view.s;
  const py = (cy - view.y) / view.s;
  view.x = cx - px * next;
  view.y = cy - py * next;
  view.s = next;
  clampView();
  draw();
}
function zoomCenter(factor: number): void {
  const { w, h } = frameSize();
  zoomAt(factor, w / 2, h / 2);
}
function resetZoom(): void {
  view.s = 1;
  clampView();
  draw();
}
function onWheel(event: WheelEvent): void {
  if (!clip.value || !canvas.value) return;
  event.preventDefault();
  const rect = canvas.value.getBoundingClientRect();
  const dy = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
  // pincement du pavé tactile : arrive comme une molette avec Ctrl, par petits pas
  const factor = Math.exp(-dy * (event.ctrlKey ? 0.01 : 0.0018));
  zoomAt(factor, event.clientX - rect.left, event.clientY - rect.top);
}
watch(() => clip.value?.id, () => resetZoom());

// Déplacement de l'image zoomée : glisser sans outil de tracé, ou bouton du milieu (même pendant un tracé).
let pan: { x0: number; y0: number; vx: number; vy: number; moved: boolean; click: boolean } | null = null;

// --- Calque -----------------------------------------------------------------

const colors: Record<string, string> = { face: "#4ade80", plate: "#38bdf8", dash: "#f472b6", manual: "#ffffff" };

function shapePath(ctx: CanvasRenderingContext2D, z: ZoneTuple, k: number, begin = true): void {
  const [x1, y1, x2, y2, shape, radius] = z;
  const contour = shape === "contour" ? z[10] : undefined;
  if (begin) ctx.beginPath();
  if (contour && contour.length >= 3) {
    // contour précis d'un objet suivi : polygone fermé, en pixels de l'image source
    ctx.moveTo(contour[0][0] * k, contour[0][1] * k);
    for (let i = 1; i < contour.length; i++) ctx.lineTo(contour[i][0] * k, contour[i][1] * k);
    ctx.closePath();
  } else if (shape === "ellipse") {
    const cx = ((x1 + x2) / 2) * k;
    const cy = ((y1 + y2) / 2) * k;
    ctx.moveTo(cx + ((x2 - x1) / 2) * k, cy);
    ctx.ellipse(cx, cy, ((x2 - x1) / 2) * k, ((y2 - y1) / 2) * k, 0, 0, Math.PI * 2);
  } else if (shape === "rounded" && radius > 0) ctx.roundRect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k, radius * k);
  else ctx.rect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
}

/** Le flou de la zone fait-il partie de `ids` (zone d'un seul flou, ou flous réunis en une zone) ? */
function zoneIn(z: ZoneTuple, ids: Set<number>): boolean {
  return ids.has(z[7]) || (z[9]?.some((id) => ids.has(id)) ?? false);
}

/** Étiquette d'un flou sur l'image : son nom et son niveau de confiance, sans score chiffré. */
function labelOf(clipId: string, id: number, kind: string): string {
  const track = trackById(clipId, id);
  if (!track) return kind === "dash" ? (objectNames.value.get(`${clipId}:${id}`) ?? kindLabel[kind]) : (kindLabel[kind] ?? kind);
  return `${displayName(track)} · ${confidenceInfo[confidence(track)].label}`;
}

let hoverId: number | null = null; // flou survolé à la souris (mode avancé)

// Légende : ce qui est réellement dessiné sur l'image. Mise à jour seulement quand elle change.
type LegendKey = "blur" | "face" | "plate" | "dash" | "manual" | "dashed" | "gray" | "selected" | "drawing";
const legend = ref<LegendKey[]>([]);
const seen = new Set<LegendKey>();
let legendSignature = "";
let legendSince = 0;
const legendInfo: Record<LegendKey, { label: string; help: string }> = {
  blur: { label: "Flou", help: "Aperçu rapide du flou. La vidéo exportée a le même flou, en plus fin." },
  face: { label: "Visage", help: "Contour vert : visage flouté." },
  plate: { label: "Plaque", help: "Contour bleu : plaque flouée." },
  dash: { label: "Objet suivi", help: "Contour rose : objet que vous avez désigné (compteur, téléphone…)." },
  manual: { label: "Flou ajouté", help: "Contour blanc : flou que vous avez ajouté." },
  dashed: { label: "Pointillés : position estimée", help: "Sur cette image, la place du flou est calculée entre deux images où l'objet a été vu." },
  gray: { label: "Gris : non flouté", help: "Trop peu sûr pour être flouté. Vous pouvez le flouter quand même depuis la liste." },
  selected: { label: "Trait épais : sélectionné", help: "Le flou sélectionné dans l'image ou dans la liste." },
  drawing: { label: "Jaune : rectangle tracé", help: "Le rectangle que vous venez de tracer." },
};
const legendOrder: LegendKey[] = ["blur", "face", "plate", "dash", "manual", "dashed", "gray", "selected", "drawing"];
function publishLegend(): void {
  if (props.simple) return;
  // pendant la lecture, la légende ne fait que s'enrichir pendant 2 s (elle ne clignote pas d'une
  // image à l'autre), puis repart de ce qui est à l'écran ; à l'arrêt, elle montre exactement ce qui est dessiné
  const now = performance.now();
  const keep = state.playing && now - legendSince < 2000;
  if (!keep) legendSince = now;
  const list = legendOrder.filter((key) => seen.has(key) || (keep && legend.value.includes(key)));
  const signature = list.join();
  if (signature !== legendSignature) {
    legendSignature = signature;
    legend.value = list;
  }
}

/** Flou mis en évidence (state.focus) sur le clip affiché. */
function focusIds(clipId: string): Set<number> | null {
  const f = state.focus;
  return f && f.clip === clipId && f.ids.length ? new Set(f.ids) : null;
}

function draw(): void {
  const el = video.value;
  const cv = canvas.value;
  const s = current.value;
  if (!el || !cv || !s) return;
  const rect = cv.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * ratio));
  const h = Math.max(1, Math.round(rect.height * ratio));
  if (cv.width !== w || cv.height !== h) {
    cv.width = w;
    cv.height = h;
  }
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w, h);
  seen.clear();
  // Dessin en pixels CSS de l'image, avec le cadrage du zoom ; `u` = un pixel CSS à l'écran.
  const view0 = [ratio * view.s, 0, 0, ratio * view.s, ratio * view.x, ratio * view.y] as const;
  ctx.setTransform(...view0);
  const u = 1 / view.s;
  const k = rect.width / s.clip.width;
  const top = -view.y / view.s; // haut de la partie visible, dans le repère du dessin
  const zones = state.preview === "off" ? [] : (zonesNow(s.clip.id, state.frame, draw) ?? []);
  // vérification guidée : les flous du passage affiché sont entourés par-dessus l'aperçu du flou ;
  // tous le sont quand il faut en cliquer un pour le retirer
  const marked = outline.value && outline.value.clip === s.clip.id ? outline.value.ids : null;
  const outlineAll = review.sub === "extra";
  const focus = focusIds(s.clip.id);
  const focused = focus ? zones.filter((z) => zoneIn(z, focus)) : [];
  const pulse = focused.length && !reducedMotion ? 0.5 + 0.5 * Math.sin((performance.now() / 1600) * Math.PI * 2) : 0.6;

  if (state.preview === "blur" && el.videoWidth) {
    // Aperçu du flou : la zone de la vidéo est réduite à quelques pixels puis ré-agrandie.
    const px = el.videoWidth / s.clip.width;
    const sc = scratch.getContext("2d");
    for (const z of zones) {
      const [x1, y1, x2, y2] = z;
      const cells = 5;
      const cw = x2 - x1 >= y2 - y1 ? cells : Math.max(1, Math.round((cells * (x2 - x1)) / (y2 - y1)));
      const ch = x2 - x1 >= y2 - y1 ? Math.max(1, Math.round((cells * (y2 - y1)) / (x2 - x1))) : cells;
      scratch.width = cw;
      scratch.height = ch;
      if (!sc) break;
      sc.imageSmoothingEnabled = true;
      sc.drawImage(el, x1 * px, y1 * px, (x2 - x1) * px, (y2 - y1) * px, 0, 0, cw, ch);
      ctx.save();
      shapePath(ctx, z, k);
      ctx.clip();
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(scratch, x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
      ctx.restore();
      seen.add("blur");
    }
  }

  if (focused.length) {
    // flou mis en évidence : le reste de l'image est légèrement assombri
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.beginPath();
    ctx.rect(0, 0, w, h);
    ctx.setTransform(...view0);
    for (const z of focused) shapePath(ctx, z, k, false);
    ctx.fillStyle = "rgba(0, 0, 0, .55)";
    ctx.fill("evenodd");
    ctx.restore();
  }

  const labels: { text: string; x: number; y: number; color: string }[] = [];
  for (const z of zones) {
    const [x1, y1, , , , , kind, id, score] = z;
    const selected = state.selectedTrack?.id === id && state.selectedTrack.clip === s.clip.id;
    const isFocused = !!focus && zoneIn(z, focus);
    const hovered = hoverId === id;
    if (state.preview === "boxes" || selected || outlineAll || isFocused || (marked && marked.has(id))) {
      const color = colors[kind] ?? "#fff";
      if (isFocused) {
        // contour qui pulse doucement autour du flou mis en évidence
        ctx.lineWidth = (5 + 5 * pulse) * u;
        ctx.strokeStyle = `rgba(255, 255, 255, ${0.3 + 0.4 * pulse})`;
        shapePath(ctx, z, k);
        ctx.stroke();
      }
      ctx.lineWidth = (selected ? 3 : isFocused ? 2.5 : 1.5) * u;
      ctx.strokeStyle = color;
      const dashed = score === null && kind !== "manual";
      ctx.setLineDash(dashed ? [6 * u, 4 * u] : []);
      shapePath(ctx, z, k);
      ctx.stroke();
      ctx.setLineDash([]);
      seen.add((kind in colors ? kind : "manual") as LegendKey);
      if (dashed) seen.add("dashed");
      if (selected) seen.add("selected");
      // contours : seul le flou survolé, sélectionné ou mis en évidence porte son étiquette
      if (state.preview === "boxes" && (hovered || selected || isFocused)) {
        // flous réunis en une seule zone : l'étiquette est celle du flou mis en évidence
        const named = focus && !focus.has(id) ? (z[9]?.find((other) => focus.has(other)) ?? id) : id;
        labels.push({ text: labelOf(s.clip.id, named, kind), x: x1 * k, y: Math.max(top + 14 * u, y1 * k - 3 * u), color });
      }
    }
  }
  if (labels.length) {
    ctx.font = `${11.5 * u}px system-ui, sans-serif`;
    for (const l of labels) {
      const tw = ctx.measureText(l.text).width + 8 * u;
      ctx.fillStyle = "rgba(0,0,0,.72)";
      ctx.fillRect(l.x, l.y - 12 * u, tw, 15 * u);
      ctx.fillStyle = l.color;
      ctx.fillText(l.text, l.x + 4 * u, l.y - 1 * u);
    }
  }
  if (state.showDropped) {
    // flous non appliqués (trop peu sûrs) : contours gris fins, pour décider de les flouter ou non
    const dropped = droppedNow(s.clip.id, state.frame, draw) ?? [];
    ctx.lineWidth = 1 * u;
    ctx.strokeStyle = "#c3c9d1";
    ctx.setLineDash([4 * u, 4 * u]);
    for (const [x1, y1, x2, y2] of dropped) ctx.strokeRect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
    ctx.setLineDash([]);
    if (dropped.length) seen.add("gray");
    if (droppedCount.value !== dropped.length) droppedCount.value = dropped.length;
  }
  const waiting = state.pendingDash ?? state.pendingBlur;
  const yellow = drag ? [drag.x0, drag.y0, drag.x1, drag.y1] : waiting && waiting.clip === s.clip.id ? waiting.box : null;
  if (yellow) {
    // rectangle en cours de tracé, ou tracé en attente du nom de l'objet ou du choix « suivre » / « fixe »
    const [x1, y1, x2, y2] = yellow;
    ctx.lineWidth = 2 * u;
    ctx.strokeStyle = "#fde047";
    ctx.setLineDash([5 * u, 4 * u]);
    ctx.strokeRect(x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
    ctx.setLineDash([]);
    seen.add("drawing");
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  publishLegend();
}

// Mise en évidence à l'arrêt : le contour pulse ; pendant la lecture, chaque image le redessine déjà.
let pulseFrame = 0;
function pulseLoop(): void {
  pulseFrame = 0;
  const s = current.value;
  if (!s || !focusIds(s.clip.id)) return;
  if (!state.playing) draw();
  pulseFrame = window.requestAnimationFrame(pulseLoop);
}
watch(() => state.focus, () => {
  if (!state.playing) draw();
  if (!reducedMotion && state.focus && !pulseFrame) pulseFrame = window.requestAnimationFrame(pulseLoop);
}, { deep: true });

// --- Tracé à la souris, sélection, déplacement de l'image zoomée --------------

/** Point de l'image source sous le pointeur, cadrage du zoom compris. */
function toSource(event: PointerEvent): { x: number; y: number } {
  const s = current.value!;
  const rect = canvas.value!.getBoundingClientRect();
  const fx = (event.clientX - rect.left - view.x) / view.s;
  const fy = (event.clientY - rect.top - view.y) / view.s;
  return {
    x: Math.min(Math.max((fx / rect.width) * s.clip.width, 0), s.clip.width),
    y: Math.min(Math.max((fy / rect.height) * s.clip.height, 0), s.clip.height),
  };
}

/** Flous sous le point, le plus petit d'abord. */
function hitsAt(p: { x: number; y: number }): ZoneTuple[] {
  const s = current.value;
  if (!s) return [];
  return (zonesNow(s.clip.id, state.frame) ?? [])
    .filter((z) => p.x >= z[0] && p.x <= z[2] && p.y >= z[1] && p.y <= z[3])
    .sort((a, b) => (a[2] - a[0]) * (a[3] - a[1]) - (b[2] - b[0]) * (b[3] - b[1]));
}

/** Clic simple sans outil : sélectionne le flou sous le pointeur, ou le retire (« Un flou est en trop »). */
function clickAt(p: { x: number; y: number }): void {
  const s = current.value;
  if (!s) return;
  const hits = hitsAt(p);
  if (review.sub === "extra") {
    if (hits.length) void removeZone(s.clip.id, hits[0][7]);
    else notify("Cliquez directement sur un flou entouré dans l'image.");
    return;
  }
  if (state.mode === "simple") return; // pas de sélection de flou dans l'assistant
  state.selectedTrack = hits.length ? { clip: s.clip.id, id: hits[0][7] } : null;
  draw();
}

function setCursor(): void {
  const cv = canvas.value;
  if (!cv) return;
  let cursor = "";
  if (pan?.moved) cursor = "grabbing";
  else if (state.tool === "none" && review.sub !== "extra") cursor = hoverId !== null && !props.simple ? "pointer" : zoomed.value ? "grab" : "";
  if (cv.style.cursor !== cursor) cv.style.cursor = cursor;
}

function pointerDown(event: PointerEvent): void {
  const s = current.value;
  if (!s) return;
  if (event.button === 1 || (event.button === 0 && state.tool === "none" && zoomed.value)) {
    // image zoomée : on la déplace en glissant ; un clic sans déplacement reste un clic
    event.preventDefault();
    if (!zoomed.value) return;
    pan = { x0: event.clientX, y0: event.clientY, vx: view.x, vy: view.y, moved: false, click: event.button === 0 };
    canvas.value?.setPointerCapture(event.pointerId);
    return;
  }
  if (event.button !== 0) return;
  const p = toSource(event);
  if (state.tool === "none") {
    clickAt(p);
    return;
  }
  pause();
  drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
  canvas.value?.setPointerCapture(event.pointerId);
}

function pointerMove(event: PointerEvent): void {
  if (pan) {
    const dx = event.clientX - pan.x0;
    const dy = event.clientY - pan.y0;
    if (!pan.moved && Math.hypot(dx, dy) < 4) return;
    pan.moved = true;
    view.x = pan.vx + dx;
    view.y = pan.vy + dy;
    clampView();
    setCursor();
    draw();
    return;
  }
  if (drag) {
    const p = toSource(event);
    drag.x1 = p.x;
    drag.y1 = p.y;
    draw();
    return;
  }
  // survol : l'étiquette du flou sous la souris s'affiche (mode avancé)
  if (props.simple || state.tool !== "none" || !current.value) return;
  const hit = hitsAt(toSource(event))[0];
  const id = hit ? hit[7] : null;
  if (id !== hoverId) {
    hoverId = id;
    if (!state.playing) draw();
  }
  setCursor();
}

function pointerLeave(): void {
  if (hoverId !== null) {
    hoverId = null;
    if (!state.playing) draw();
  }
}

async function pointerUp(event: PointerEvent): Promise<void> {
  if (pan) {
    const done = pan;
    pan = null;
    setCursor();
    if (!done.moved && done.click && current.value) clickAt(toSource(event));
    return;
  }
  const s = current.value;
  if (!drag || !s) return;
  const box = [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)].map(Math.round);
  drag = null;
  const tool = state.tool;
  state.tool = "none";
  draw();
  const guidedLoop = review.sub === "missing" ? loop.value : null;
  if (box[2] - box[0] < 6 || box[3] - box[1] < 6) {
    notify("Rectangle trop petit : maintenez le bouton et tracez un rectangle.");
    if (guidedLoop) state.tool = "manual"; // on reste sur « Il manque un flou » : nouvel essai
    return;
  }
  const t = state.frame / s.fps;
  if (tool === "manual" && state.mode !== "simple") {
    // mode avancé : le rectangle attend le choix « Suivre ce que j'ai entouré » ou « Flou fixe ».
    // Le flou fixe reste celui d'avant : tout le passage en boucle pendant la vérification, 2 s sinon.
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
    // vérification guidée : le flou couvre tout le passage en boucle (dans le passage du montage affiché)
    const base = s.inFrame / s.fps - s.offset;
    const from = Math.max(guidedLoop.from + base, s.inFrame / s.fps);
    const to = Math.min(guidedLoop.to + base, (s.outFrame - 1) / s.fps);
    await saveManual(s.clip.id, [[from, ...box], [Math.max(to, from + 1 / s.fps), ...box]]);
    notify(state.mode === "simple"
      ? "Flou ajouté sur tout le passage. Regardez la vidéo : s'il ne couvre pas tout, ajoutez-en un autre."
      : "Flou ajouté sur tout le passage. Regardez la boucle : si l'objet bouge, retracez-le à un autre instant avec « Déplacer ici… » dans l'onglet « Flous ».");
    resume();
  } else if (tool === "manual") {
    // nouveau flou : de l'image courante jusqu'à 2 s plus tard (ou la fin du passage)
    const end = Math.min(t + 2, (s.outFrame - 1) / s.fps);
    await saveManual(s.clip.id, [[t, ...box], [Math.max(end, t + 1 / s.fps), ...box]]);
    notify("Flou ajouté pour 2 s. Réglez son début et sa fin dans l'onglet « Flous ».");
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
  if (state.jobOpen || state.pendingBlur || review.sheet || panels.shortcuts) return; // un dialogue est ouvert : le clavier lui appartient
  const s = current.value;
  if (!s) return;
  const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
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
  } else if (plain && (event.key === "0" || event.code === "Numpad0")) resetZoom();
  else if (plain && (event.key === "+" || event.key === "=" || event.code === "NumpadAdd")) zoomCenter(1.5);
  else if (plain && (event.key === "-" || event.code === "NumpadSubtract")) zoomCenter(1 / 1.5);
  else if (plain && !props.simple && (event.key === "v" || event.key === "V")) toggleView();
  else return;
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
watch(() => [review.sub, review.current, state.showDropped, state.pendingDash, state.pendingBlur, state.tool], () => {
  hoverId = null;
  setCursor();
  draw();
});

// Capture : l'image affichée est enregistrée en PNG pleine résolution à côté du projet, floutée
// comme à l'export (nette seulement si la qualité de l'aperçu est « image exacte, sans flou »).
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
let fitWidth = 0;
function measure(): void {
  const el = stage.value;
  const c = clip.value;
  if (!el || !c || !el.clientWidth || !el.clientHeight) return;
  const width = Math.min(el.clientWidth, (el.clientHeight * c.width) / c.height);
  if (fitWidth && zoomed.value && Math.abs(width - fitWidth) > 0.5) {
    // la scène change de taille pendant un zoom : même cadrage, à la nouvelle échelle
    view.x *= width / fitWidth;
    view.y *= width / fitWidth;
  }
  fitWidth = width;
  fit.value = { width: `${width}px`, height: `${(width * c.height) / c.width}px` };
}
const observer = new ResizeObserver(() => {
  measure();
  void nextTick(() => {
    clampView();
    draw();
  });
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
  window.cancelAnimationFrame(pulseFrame);
  const el = video.value as (HTMLVideoElement & { cancelVideoFrameCallback?: (id: number) => void }) | undefined;
  el?.cancelVideoFrameCallback?.(frameCallback);
});

const zoomText = computed(() => `${String(Math.round(view.s * 10) / 10).replace(".", ",")}×`);

defineExpose({ toggle, step, pause, snap, resetZoom });
</script>

<template>
  <div class="player">
    <div ref="stage" class="stage" :class="{ drawing: state.tool !== 'none', picking: review.sub === 'extra', zoomed }">
      <template v-if="clip">
        <div ref="frameEl" class="frame" :style="fit ?? { aspectRatio: `${clip.width} / ${clip.height}` }" @wheel="onWheel">
          <div class="view" :style="viewStyle">
            <video ref="video" playsinline preload="auto" @seeked="draw" @loadeddata="sync" @ended="onEnded"></video>
            <img v-if="exactUrl && !state.playing" class="exact" :src="exactUrl" alt="" @load="exactLoading = false" />
          </div>
          <canvas
            ref="canvas" :data-dropped="state.showDropped ? droppedCount : undefined"
            @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp" @pointerleave="pointerLeave" @auxclick.prevent
          ></canvas>
          <div v-if="!clip.proxy" class="veil"><span class="spinner"></span>Préparation de l'aperçu de ce clip…</div>
          <!-- assistant : pas de barre de commandes, la capture apparaît au survol dans le coin de l'image -->
          <button
            v-if="simple && clip.proxy && state.tool === 'none' && review.sub !== 'extra'" class="icon-btn snap-float" :disabled="snapping"
            title="Enregistrer cette image, floutée, à côté de la vidéo" aria-label="Enregistrer cette image, floutée" @click="snap"
          ><span v-if="snapping" class="spinner small"></span><Icon v-else name="camera" /></button>
          <Transition name="fade">
            <button v-if="zoomed" class="zoom-reset" :title="`Revenir à l'image entière (0). Zoom actuel : ${zoomText}`" @click="resetZoom">
              <Icon name="zoom" :size="14" />Image entière<span class="mono">{{ zoomText }}</span>
            </button>
          </Transition>
          <Transition name="hint">
            <div v-if="state.tool !== 'none' && simple" class="hint tool">{{ state.tool === "dash" ? "Entourez l'objet avec la souris" : "Entourez avec la souris ce qui doit être flouté" }}</div>
            <div v-else-if="state.tool !== 'none'" class="hint tool">
              {{ state.tool === "dash" ? "Tracez un rectangle autour de l'objet à flouter" : "Tracez un rectangle sur ce qui doit être flouté" }} — Échap pour annuler
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
    <div v-if="!simple" class="transport" role="group" aria-labelledby="group-lecture">
      <span id="group-lecture" class="group-name">Lecture</span>
      <span class="cluster">
        <button class="icon-btn" title="Image précédente (←, Maj+← : une seconde)" aria-label="Image précédente" @click="step(-1)"><Icon name="stepBack" /></button>
        <button class="primary play" title="Lecture / pause (Espace)" @click="toggle"><Icon :name="state.playing ? 'pause' : 'play'" />{{ state.playing ? "Pause" : "Lecture" }}</button>
        <button class="icon-btn" title="Image suivante (→, Maj+→ : une seconde)" aria-label="Image suivante" @click="step(1)"><Icon name="stepForward" /></button>
      </span>
      <select v-model.number="review.speed" class="speed-select" aria-label="Vitesse de l'aperçu" title="Vitesse de l'aperçu (sans effet sur l'export)">
        <option v-for="s in speeds" :key="s.value" :value="s.value">{{ s.label }}</option>
      </select>
      <span class="timecode">
        <span class="clock">{{ formatTime(time) }} <span class="clock-total">/ {{ formatTime(duration) }}</span></span>
        <span v-if="current" class="dim meta" :title="`${current.clip.name}, image ${state.frame}, à ${formatTime(state.frame / current.fps)} dans ce clip`">{{ current.clip.name }} · image {{ state.frame }}</span>
      </span>
      <span v-if="muted" class="badge"><Icon name="volumeOff" :size="12" />muet</span>
      <span class="volume" title="Volume de l'aperçu (sans effet sur l'export)">
        <button class="icon-btn" :class="{ on: userMuted }" :aria-pressed="userMuted" :aria-label="userMuted ? 'Rétablir le son de l\'aperçu' : 'Couper le son de l\'aperçu'" @click="toggleSound"><Icon :name="silent ? 'volumeOff' : 'volume'" /></button>
        <input v-model.number="volume" type="range" min="0" max="100" step="1" aria-label="Volume de l'aperçu" :aria-valuetext="`${volume} %`" @pointerup="($event.target as HTMLInputElement).blur()" />
        <span class="mono volume-value">{{ volume }} %</span>
      </span>
      <button
        class="view-toggle" :title="state.preview === 'blur' ? 'Voir les contours des flous, avec leur nom au survol (V)' : 'Voir le flou tel qu\'il sera à l\'export (V)'" @click="toggleView"
      ><Icon :name="state.preview === 'blur' ? 'target' : 'eye'" />{{ state.preview === "blur" ? "Voir les contours" : "Voir le flou" }}</button>
      <label class="dim field quality" title="À l'arrêt, « image exacte » affiche l'image en pleine qualité, telle qu'elle sortira à l'export (floutée) ou telle qu'elle a été filmée (sans flou). Un peu plus lent : l'image est préparée à chaque arrêt."><span class="field-label">Qualité</span>
        <select v-model="exact" aria-label="Qualité de l'aperçu">
          <option value="">aperçu rapide</option>
          <option value="blur">image exacte, floutée</option>
          <option value="source">image exacte, sans flou</option>
        </select>
      </label>
      <button
        class="icon-btn snap" :disabled="snapping || !clip"
        :title="exact === 'source' ? 'Enregistrer cette image en PNG, sans flou (qualité « image exacte, sans flou »)' : 'Enregistrer cette image en PNG, floutée comme à l\'export, à côté du projet'"
        :aria-label="exact === 'source' ? 'Enregistrer cette image, sans flou' : 'Enregistrer cette image, floutée'" @click="snap"
      ><span v-if="snapping" class="spinner small"></span><Icon v-else name="camera" /></button>
    </div>
    <div v-if="!simple" class="legend" aria-label="Légende de l'image">
      <span v-if="!legend.length" class="legend-item none">{{ state.preview === "off" ? "Flous masqués" : "Aucun flou sur cette image" }}</span>
      <span v-for="key in legend" :key="key" class="legend-item" :title="legendInfo[key].help"><i class="sw" :class="`sw-${key}`" aria-hidden="true"></i>{{ legendInfo[key].label }}</span>
      <span v-if="!zoomed" class="legend-tip">Molette : zoomer</span>
    </div>
  </div>
</template>

<style>
/* === 12 points : lecteur (zoom, légende, mise en évidence) === */
.frame { overflow: hidden; }
.frame .view { position: absolute; inset: 0; transform-origin: 0 0; will-change: transform; }
.frame .view video, .frame .view .exact { position: absolute; inset: 0; width: 100%; height: 100%; }
.zoom-reset {
  position: absolute; z-index: 2; left: 10px; bottom: 10px; height: 28px; padding: 0 9px; gap: 6px;
  background: rgba(13, 14, 16, .86); border-color: var(--line-strong); font-size: 12px;
}
.zoom-reset .mono { color: var(--dim); }
/* consigne sur l'image : centrée et repliée sur plusieurs lignes si l'image est étroite (vidéo verticale) */
.frame .hint { left: 10px; right: 10px; width: max-content; max-width: calc(100% - 20px); margin: 0 auto; transform: none; white-space: normal; text-align: center; justify-content: center; }
.frame .hint-enter-from, .frame .hint-leave-to { transform: translateY(-4px); }
.transport { flex-wrap: wrap; row-gap: 6px; }
.group-name { flex: none; color: var(--faint); font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
.view-toggle { flex: none; min-width: 152px; }
.legend { flex: none; display: flex; align-items: center; gap: 4px 14px; height: 16px; overflow: hidden; color: var(--faint); font-size: 11.5px; white-space: nowrap; }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend-item.none { color: var(--faint); }
.legend-tip { margin-left: auto; }
.sw { flex: none; display: inline-block; width: 16px; height: 0; border-top: 2px solid #fff; }
.sw-face { border-color: var(--face); }
.sw-plate { border-color: var(--plate); }
.sw-dash { border-color: var(--dash); }
.sw-manual { border-color: #fff; }
.sw-dashed { border-top-style: dashed; border-color: var(--dim); }
.sw-gray { border-top: 1px dashed #c3c9d1; }
.sw-selected { border-top-width: 3px; border-color: var(--text); }
.sw-drawing { border-top-style: dashed; border-color: #fde047; }
.sw-blur {
  width: 12px; height: 10px; border: 0; border-radius: 2px;
  background: linear-gradient(90deg, #6b7380 0 33%, #8a929e 33% 66%, #5b626d 66%), #6b7380;
  background-size: 100% 50%; background-repeat: repeat-y;
}
@media (max-width: 1280px) {
  .legend-tip { display: none; }
}
</style>
