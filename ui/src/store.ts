// État de l'interface et calculs de montage. Le fichier projet, côté moteur, reste la seule
// source de vérité : chaque modification est envoyée au moteur, qui renvoie l'état complet.

import { computed, reactive } from "vue";
import { call, type Clip, type Range, type ReviewItem, type State, type Track, type ZoneTuple } from "./api";

export interface Resolved {
  index: number;
  clip: Clip;
  fps: number;
  inFrame: number; // première image gardée
  outFrame: number; // première image non gardée
  offset: number; // début dans le montage, en secondes
  duration: number;
}

export const state = reactive({
  data: { project: null, job: null, version: 0 } as State,
  loaded: false,
  // Tête de lecture : passage du montage et image SOURCE affichée (exact, sans arrondi de temps).
  seg: 0,
  frame: 0,
  playing: false,
  selected: -1, // passage sélectionné dans la timeline
  selectedTrack: null as { clip: string; id: number } | null,
  tool: "none" as "none" | "manual" | "dash" | "redraw",
  preview: "boxes" as "boxes" | "blur" | "off",
  muteIn: null as number | null, // début d'un passage muet en cours de pose (temps du montage)
  toast: "",
  toastKind: "info" as "info" | "error",
  zoom: 0, // pixels par seconde ; 0 = ajusté à la largeur
  pendingDash: null as { clip: string; time: number; box: number[] } | null,
  tab: "review" as "review" | "zones" | "settings", // onglet du panneau latéral
  step: 0, // étape du guide choisie à la main (1 à 4) ; 0 = étape déduite de l'état du projet
});

/** Commandes du lecteur, renseignées par le composant Player à son montage. */
export const player = {
  play: async (): Promise<void> => {},
  pause: (): void => {},
};

export const project = computed(() => state.data.project);
export const job = computed(() => state.data.job);
export const busy = computed(() => state.data.job?.state === "running");
export const needsAnalysis = computed(() => project.value?.clips.some((c) => c.needs_analysis) ?? false);

export function fpsOf(clip: Clip): number {
  return clip.fps_num / clip.fps_den;
}

export function clipById(id: string): Clip | undefined {
  return project.value?.clips.find((c) => c.id === id);
}

export function resolve(timeline: Range[]): Resolved[] {
  const out: Resolved[] = [];
  let offset = 0;
  timeline.forEach((seg, index) => {
    const clip = clipById(seg.clip);
    if (!clip) return;
    const fps = fpsOf(clip);
    const inFrame = Math.max(0, Math.round(seg.start * fps));
    const outFrame = Math.min(clip.frames, Math.round(seg.end * fps));
    if (outFrame <= inFrame) return;
    const duration = (outFrame - inFrame) / fps;
    out.push({ index, clip, fps, inFrame, outFrame, offset, duration });
    offset += duration;
  });
  return out;
}

export const segments = computed(() => resolve(project.value?.timeline ?? []));
export const duration = computed(() => {
  const last = segments.value[segments.value.length - 1];
  return last ? last.offset + last.duration : 0;
});
export const current = computed<Resolved | undefined>(
  () => segments.value.find((s) => s.index === state.seg) ?? segments.value[0],
);
/** Position de la tête de lecture dans le montage, en secondes. */
export const time = computed(() => {
  const s = current.value;
  return s ? s.offset + (clampFrame(s, state.frame) - s.inFrame) / s.fps : 0;
});

function clampFrame(s: Resolved, frame: number): number {
  return Math.min(Math.max(frame, s.inFrame), s.outFrame - 1);
}

export function locate(t: number): { seg: Resolved; frame: number } | undefined {
  const list = segments.value;
  if (!list.length) return undefined;
  const clamped = Math.min(Math.max(t, 0), duration.value - 1e-6);
  const seg = list.find((s) => clamped < s.offset + s.duration) ?? list[list.length - 1];
  const frame = clampFrame(seg, seg.inFrame + Math.floor((clamped - seg.offset) * seg.fps + 1e-6));
  return { seg, frame };
}

export function seekTime(t: number): void {
  const where = locate(t);
  if (!where) return;
  state.seg = where.seg.index;
  state.frame = where.frame;
}

/** Positions dans le montage d'un intervalle de temps source (peut être coupé en morceaux). */
export function mapRange(range: Range): { start: number; end: number }[] {
  const out = [];
  for (const s of segments.value) {
    if (s.clip.id !== range.clip) continue;
    const a = Math.max(range.start, s.inFrame / s.fps);
    const b = Math.min(range.end, s.outFrame / s.fps);
    if (b >= a) out.push({ start: s.offset + a - s.inFrame / s.fps, end: s.offset + b - s.inFrame / s.fps });
  }
  return out;
}

export const visibleReview = computed(() => {
  const items: { item: ReviewItem; at: number; end: number; key: string }[] = [];
  for (const item of project.value?.review ?? []) {
    for (const part of mapRange(item)) {
      items.push({ item, at: part.start, end: part.end, key: item.key });
    }
  }
  return items.sort((a, b) => a.at - b.at);
});

export function formatTime(t: number, decimals = 2): string {
  const sign = t < 0 ? "-" : "";
  t = Math.abs(t);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const h = Math.floor(m / 60);
  const body = `${String(m % 60).padStart(2, "0")}:${s.toFixed(decimals).padStart(decimals ? decimals + 3 : 2, "0")}`;
  return sign + (h ? `${h}:${body}` : body);
}

export const kindLabel: Record<string, string> = {
  face: "visage",
  plate: "plaque",
  dash: "compteur",
  manual: "zone manuelle",
};

// --- Appels au moteur -------------------------------------------------------

export function notify(message: string, kind: "info" | "error" = "info", sticky = false): void {
  state.toast = message;
  state.toastKind = kind;
  if (kind === "info" && !sticky) window.setTimeout(() => state.toast === message && (state.toast = ""), 5000);
}

function apply(next: State): void {
  const t = time.value;
  state.data = next;
  if (next.project) seekTime(t); // la tête reste au même instant du montage
}

export async function send(method: string, url: string, body?: unknown): Promise<State | undefined> {
  try {
    const next = await call<State>(method, url, body);
    apply(next);
    return next;
  } catch (error) {
    notify((error as Error).message, "error");
    return undefined;
  }
}

let polling = false;
export async function refresh(): Promise<void> {
  const before = state.data.job?.state;
  try {
    const next = await call<State>("GET", "/api/state");
    apply(next);
    state.loaded = true;
    const job = next.job;
    if (before === "running" && job && job.state !== "running" && job.message) {
      notify(job.message, job.state === "error" ? "error" : "info", true); // reste affiché jusqu'au clic
    }
    if (next.project && !job?.state.startsWith("run") && next.project.clips.some((c) => !c.proxy && !c.missing)) {
      if (job?.state !== "error" && job?.state !== "cancelled") await send("POST", "/api/proxies");
    }
  } catch (error) {
    notify(`moteur injoignable : ${(error as Error).message}`, "error");
  }
  if (state.data.job?.state === "running" && !polling) {
    polling = true;
    window.setTimeout(() => {
      polling = false;
      void refresh();
    }, 600);
  }
}

export async function start(url: string, body?: unknown): Promise<void> {
  if (await send("POST", url, body ?? {})) void refresh();
}

// --- Montage ----------------------------------------------------------------

function round6(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

export async function saveMontage(timeline: Range[], mutes?: Range[]): Promise<void> {
  await send("PUT", "/api/montage", { timeline, mutes: mutes ?? project.value?.mutes ?? [] });
}

export async function splitAtPlayhead(): Promise<void> {
  const s = current.value;
  const p = project.value;
  if (!s || !p) return;
  if (state.frame <= s.inFrame || state.frame >= s.outFrame) {
    notify("Placez la tête de lecture à l'intérieur d'un passage pour le couper.");
    return;
  }
  const cut = round6(state.frame / s.fps);
  const timeline = p.timeline.flatMap((seg, i) =>
    i === s.index ? [{ ...seg, end: cut }, { ...seg, start: cut }] : [seg],
  );
  const t = time.value;
  await saveMontage(timeline);
  seekTime(t);
  state.selected = state.seg;
}

export async function deleteSegment(index: number): Promise<void> {
  const p = project.value;
  if (!p || index < 0) return;
  const t = segments.value.find((s) => s.index === index)?.offset ?? 0;
  await saveMontage(p.timeline.filter((_, i) => i !== index));
  state.selected = -1;
  seekTime(t);
}

export async function moveSegment(index: number, delta: number): Promise<void> {
  const p = project.value;
  const target = index + delta;
  if (!p || index < 0 || target < 0 || target >= p.timeline.length) return;
  const timeline = [...p.timeline];
  [timeline[index], timeline[target]] = [timeline[target], timeline[index]];
  await saveMontage(timeline);
  state.selected = target;
  const moved = segments.value.find((s) => s.index === target);
  if (moved) seekTime(moved.offset);
}

export async function trimSegment(index: number, edge: "start" | "end", frame: number): Promise<void> {
  const p = project.value;
  const s = segments.value.find((x) => x.index === index);
  if (!p || !s) return;
  const value = round6(frame / s.fps);
  await saveMontage(p.timeline.map((seg, i) => (i === index ? { ...seg, [edge]: value } : seg)));
}

/** Pose un passage muet entre deux instants du montage (réparti sur les passages concernés). */
export async function addMute(from: number, to: number): Promise<void> {
  const p = project.value;
  if (!p) return;
  const [a, b] = from <= to ? [from, to] : [to, from];
  const added: Range[] = [];
  for (const s of segments.value) {
    const lo = Math.max(a, s.offset);
    const hi = Math.min(b, s.offset + s.duration);
    if (hi - lo < 0.5 / s.fps) continue;
    const base = s.inFrame / s.fps - s.offset;
    added.push({ clip: s.clip.id, start: round6(lo + base), end: round6(hi + base) });
  }
  if (!added.length) {
    notify("Passage muet trop court.");
    return;
  }
  await saveMontage(p.timeline, [...p.mutes, ...added]);
}

export async function removeMute(index: number): Promise<void> {
  const p = project.value;
  if (p) await saveMontage(p.timeline, p.mutes.filter((_, i) => i !== index));
}

// --- Zones ------------------------------------------------------------------

const CHUNK = 240;
const zoneCache = new Map<string, Promise<Record<string, ZoneTuple[]>>>();
let cacheVersion = -1;

function chunkOf(clip: string, index: number): Promise<Record<string, ZoneTuple[]>> {
  if (cacheVersion !== state.data.version) {
    zoneCache.clear();
    cacheVersion = state.data.version;
  }
  const key = `${clip}:${index}`;
  let chunk = zoneCache.get(key);
  if (!chunk) {
    const from = index * CHUNK;
    chunk = call<{ frames: Record<string, ZoneTuple[]> }>(
      "GET",
      `/api/zones?clip=${clip}&from=${from}&to=${from + CHUNK - 1}`,
    )
      .then((r) => r.frames)
      .catch(() => ({}));
    zoneCache.set(key, chunk);
  }
  return chunk;
}

const ready = new Map<string, Record<string, ZoneTuple[]>>();

/** Zones de l'image `frame`, si elles sont déjà chargées ; sinon lance le chargement. */
export function zonesNow(clip: string, frame: number, onLoad?: () => void): ZoneTuple[] | undefined {
  const index = Math.floor(frame / CHUNK);
  const key = `${state.data.version}:${clip}:${index}`;
  const loaded = ready.get(key);
  if (loaded) {
    if (frame % CHUNK > CHUNK - 60) void chunkOf(clip, index + 1); // préchargement de la suite
    return loaded[String(frame)] ?? [];
  }
  void chunkOf(clip, index).then((frames) => {
    if (ready.size > 40) ready.clear();
    ready.set(key, frames);
    onLoad?.();
  });
  return undefined;
}

export function trackById(clip: string, id: number): Track | undefined {
  return project.value?.tracks[clip]?.find((t) => t.id === id);
}

export async function patchTrack(clip: string, id: number, changes: Partial<Track>): Promise<void> {
  await send("PATCH", "/api/tracks", { clip, id, ...changes });
}

export async function deleteTrack(clip: string, id: number): Promise<void> {
  await send("DELETE", `/api/tracks?clip=${clip}&id=${id}`);
  if (state.selectedTrack?.id === id) state.selectedTrack = null;
}

export async function saveManual(clip: string, keyframes: number[][], id?: number, kind = "manual"): Promise<void> {
  const next = await send("POST", "/api/manual", { clip, keyframes, id, kind });
  if (next?.created !== undefined) state.selectedTrack = { clip, id: next.created };
}
