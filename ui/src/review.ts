// Vérification guidée : on passe les passages douteux un par un, en boucle, avec une seule
// question (« Tout ce qui doit être flouté l'est-il ? »), puis on relit tout le montage en posant
// des signalements. Les validations et les signalements sont enregistrés dans le fichier projet
// par le moteur ; ce module ne garde que l'état de la séance en cours.

import { computed, reactive } from "vue";
import type { Mark, ReviewItem } from "./api";
import {
  current, duration, formatTime, mapRange, patchTrack, player, project, seekTime, send,
  state, time, trackById,
} from "./store";

/** Un élément de la file : passage douteux ou signalement de relecture, placé dans le montage. */
export interface Entry {
  id: string;
  type: "doubt" | "mark";
  clip: string;
  at: number; // début dans le montage, en secondes
  end: number;
  done: boolean;
  item?: ReviewItem;
  mark?: Mark;
}

export const review = reactive({
  mode: "home" as "home" | "queue" | "sweep" | "end",
  scope: "doubt" as "doubt" | "marks", // file parcourue : passages douteux ou signalements
  current: "", // identifiant de l'élément affiché
  sub: "ask" as "ask" | "missing" | "extra",
  speed: 1, // vitesse de l'aperçu pendant la vérification (sans effet sur l'export)
  removed: [] as { clip: string; id: number; label: string }[], // flous retirés sur l'élément affiché
  flash: "", // confirmation brève du dernier signalement
  savedPreview: "" as "" | "boxes" | "blur" | "off",
  last: null as { clip: string; at: number; end: number } | null, // dernière position connue de l'élément affiché
});

// Seuls les passages encore présents dans le montage sont proposés (un passage coupé ne sort pas).
export const doubtEntries = computed<Entry[]>(() => {
  const out: Entry[] = [];
  for (const item of project.value?.review ?? []) {
    const parts = mapRange(item);
    if (!parts.length) continue;
    out.push({
      id: item.key, type: "doubt", clip: item.clip, done: item.done, item,
      at: Math.min(...parts.map((p) => p.start)), end: Math.max(...parts.map((p) => p.end)),
    });
  }
  return out.sort((a, b) => a.at - b.at);
});

export const markEntries = computed<Entry[]>(() => {
  const out: Entry[] = [];
  for (const mark of project.value?.marks ?? []) {
    const part = mapRange({ clip: mark.clip, start: mark.time, end: mark.time })[0];
    if (part) out.push({ id: `m${mark.id}`, type: "mark", clip: mark.clip, done: false, mark, at: part.start, end: part.start });
  }
  return out.sort((a, b) => a.at - b.at);
});

export const entries = computed(() => (review.scope === "doubt" ? doubtEntries.value : markEntries.value));
export const currentEntry = computed(() => entries.value.find((e) => e.id === review.current));
export const currentIndex = computed(() => entries.value.findIndex((e) => e.id === review.current));
export const doubtDone = computed(() => doubtEntries.value.filter((e) => e.done).length);
export const doubtLeft = computed(() => doubtEntries.value.length - doubtDone.value);
export const pending = computed(() => doubtLeft.value + markEntries.value.length);
export const guided = computed(() => review.mode === "queue" || review.mode === "sweep");
export const rate = computed(() => review.speed);

/** Passage lu en boucle autour de l'élément affiché, en secondes du montage. */
export const loop = computed(() => {
  const e = currentEntry.value;
  if (review.mode !== "queue" || !e) return state.loopRange; // hors vérification : extrait du mode simple, s'il y en a un
  const pad = e.type === "mark" ? 1.5 : 0.5;
  return { from: Math.max(0, e.at - pad), to: Math.min(duration.value, e.end + pad) };
});

/** Zones à entourer dans l'aperçu : celles du passage affiché. */
export const outline = computed(() => {
  const e = currentEntry.value;
  if (review.mode !== "queue" || !e?.item) return null;
  return { clip: e.clip, ids: new Set(e.item.tracks) };
});

const plain: [string, string][] = [
  ["confiance faible", "Le logiciel n'est pas sûr de ce qu'il a vu ici."],
  ["suivi perdu puis retrouvé", "L'objet a disparu un instant puis est revenu : vérifiez qu'il reste flouté entre-temps."],
  ["objet très petit", "Objet très petit, sans doute déjà illisible."],
  ["piste très courte", "Objet vu très brièvement : regardez juste avant et juste après."],
  ["objet partiel au bord de l'image", "Objet coupé par le bord de l'image."],
  ["suivi du compteur incertain", "Le suivi du compteur a hésité : vérifiez qu'il reste couvert."],
];
const lostObject = "suivi de l'objet";

/** Raisons techniques du moteur traduites en phrases simples ; la raison brute reste en infobulle. */
export function explain(reasons: string[]): { text: string; raw: string }[] {
  const out: { text: string; raw: string }[] = [];
  for (const raw of reasons) {
    let text = plain.find(([prefix]) => raw.startsWith(prefix))?.[1] ?? raw;
    if (raw.startsWith(lostObject)) {
      const name = raw.match(/«\s*(.+?)\s*»/)?.[1] ?? "l'objet";
      text = `Le suivi de « ${name} » a hésité : vérifiez qu'il reste couvert.`;
    }
    if (!out.some((x) => x.text === text)) out.push({ text, raw });
  }
  return out;
}

export function summary(): string {
  const plural = (n: number, one: string, many: string) => `${n} ${n > 1 ? many : one}`;
  return [
    plural(doubtDone.value, "passage validé", "passages validés"),
    `${doubtLeft.value} à revoir`,
    plural(markEntries.value.length, "signalement", "signalements"),
  ].join(", ");
}

// --- Lecture pendant la vérification ----------------------------------------

function blurPreview(): void {
  if (!review.savedPreview) review.savedPreview = state.preview;
  state.preview = "blur";
}

/** Relance la boucle du passage affiché depuis son début. */
export function replay(): void {
  const range = loop.value;
  if (!range) return;
  seekTime(range.from);
  void player.play();
}

function show(id: string): void {
  review.current = id;
  review.sub = "ask";
  review.removed = [];
  state.tool = "none";
  state.selectedTrack = null;
  const e = currentEntry.value;
  review.last = e ? { clip: e.clip, at: e.at, end: e.end } : null;
  blurPreview();
  replay();
}

export function finish(): void {
  player.pause();
  review.mode = "end";
  review.current = "";
  review.sub = "ask";
  state.tool = "none";
}

/** Ouvre la file des passages douteux ou des signalements, au premier élément non réglé. */
export function startQueue(scope: "doubt" | "marks"): void {
  review.scope = scope;
  state.tab = "review";
  const first = entries.value.find((e) => !e.done) ?? entries.value[0];
  if (!first) {
    finish();
    return;
  }
  review.mode = "queue";
  show(first.id);
}

function showAt(index: number): void {
  const target = entries.value[index];
  if (target) show(target.id);
  else finish();
}

export function next(): void {
  showAt(currentIndex.value + 1);
}

export function previous(): void {
  if (currentIndex.value > 0) showAt(currentIndex.value - 1);
}

/** « Oui, c'est bon » : valide le passage douteux, ou retire le signalement réglé, puis passe au suivant. */
export async function validate(): Promise<void> {
  const e = currentEntry.value;
  if (!e || review.sub !== "ask") return;
  const index = currentIndex.value;
  if (e.type === "doubt") {
    if (!e.done && !(await send("PUT", "/api/review", { key: e.id, done: true }))) return;
    showAt(index + 1);
  } else if (e.mark) {
    if (!(await send("DELETE", `/api/marks?id=${e.mark.id}`))) return;
    showAt(index); // le suivant a pris sa place
  }
}

export async function unvalidate(): Promise<void> {
  const e = currentEntry.value;
  if (e?.type === "doubt" && e.done) await send("PUT", "/api/review", { key: e.id, done: false });
}

/** « Il manque un flou » : pause et outil de tracé ; le lecteur crée la zone puis appelle `resume`. */
export function missing(): void {
  player.pause();
  review.sub = "missing";
  state.tool = "manual";
}

/** « Un flou est en trop » : pause ; le prochain clic sur une zone de l'image la retire. */
export function extra(): void {
  player.pause();
  state.tool = "none";
  review.sub = "extra";
}

/**
 * Après une correction, le passage affiché peut avoir changé de bornes (donc de clé) ou disparu :
 * on reste sur celui qui recouvre le même endroit, sinon sur le plus proche.
 */
export function relocate(): void {
  if (review.mode !== "queue") return;
  const now = currentEntry.value;
  if (now) {
    review.last = { clip: now.clip, at: now.at, end: now.end };
    return;
  }
  const last = review.last;
  const list = entries.value;
  if (!list.length || !last) {
    finish();
    return;
  }
  const overlap = (e: Entry) => Math.min(e.end, last.end) - Math.max(e.at, last.at);
  const best = [...list].sort((a, b) => overlap(b) - overlap(a) || Math.abs(a.at - last.at) - Math.abs(b.at - last.at))[0];
  review.current = best.id;
  review.last = { clip: best.clip, at: best.at, end: best.end };
}

/** Détections écartées par le score minimal dans le passage affiché (elles n'y sont pas floutées). */
export const droppedInPassage = computed(() => {
  const e = currentEntry.value;
  if (review.mode !== "queue" || !e || !project.value) return 0;
  const from = e.item ? e.item.start - 0.5 : (e.mark?.time ?? 0) - 1.5;
  const to = e.item ? e.item.end + 0.5 : (e.mark?.time ?? 0) + 1.5;
  return (project.value.tracks[e.clip] ?? []).filter((t) => t.below_min_score && !t.disabled && t.end >= from && t.start <= to).length;
});

/** « Flouter quand même » depuis la vérification : la détection écartée est floutée, la boucle reprend. */
export async function forceZone(clip: string, id: number): Promise<void> {
  await patchTrack(clip, id, { forced: true });
  resume();
}

/** Retour à la question après une correction (ou son abandon), et reprise de la boucle. */
export function resume(): void {
  if (review.sub === "missing") state.showDropped = false;
  review.sub = "ask";
  state.tool = "none";
  relocate();
  if (review.mode === "queue") replay();
}

export async function removeZone(clip: string, id: number): Promise<void> {
  const name = trackById(clip, id)?.name ?? "zone";
  await patchTrack(clip, id, { disabled: true });
  review.removed.push({ clip, id, label: `${name} n° ${id}` });
  resume();
}

export async function restoreZone(zone: { clip: string; id: number }): Promise<void> {
  await patchTrack(zone.clip, zone.id, { disabled: false });
  review.removed = review.removed.filter((r) => r.id !== zone.id || r.clip !== zone.clip);
  resume();
}

// --- Relecture complète -----------------------------------------------------

export function startSweep(fromStart = false): void {
  review.mode = "sweep";
  review.current = "";
  review.sub = "ask";
  state.tab = "review";
  state.tool = "none";
  blurPreview();
  if (fromStart || time.value >= duration.value - 0.2) seekTime(0);
  void player.play();
}

let flashTimer = 0;
/** « Signaler ici » : pose un signalement à l'image affichée, sans arrêter la lecture. */
export async function addMark(): Promise<void> {
  const s = current.value;
  if (!s || review.mode !== "sweep") return;
  const at = time.value;
  if (!(await send("POST", "/api/marks", { clip: s.clip.id, time: state.frame / s.fps }))) return;
  review.flash = `Signalé à ${formatTime(at, 1)}`;
  window.clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => (review.flash = ""), 1600);
}

/** Quitte la vérification guidée : retour à l'écran de choix, aperçu remis comme avant. */
export function leave(): void {
  player.pause();
  review.mode = "home";
  review.current = "";
  review.sub = "ask";
  review.removed = [];
  state.tool = "none";
  if (review.savedPreview) state.preview = review.savedPreview;
  review.savedPreview = "";
}

