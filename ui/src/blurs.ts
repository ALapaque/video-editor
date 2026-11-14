// Présentation des flous dans le mode avancé : nom en clair, niveau de confiance, tri du plus
// douteux au plus sûr, filtre par type, et raisons d'un passage douteux rattachées à chaque flou.
// S'appuie sur le vocabulaire commun d'`extras` (confidence, confidenceInfo, blurName, blurSpan).
import { reactive } from "vue";
import type { ReviewItem, Track } from "./api";
import { blurName, blurSpan, confidence, confidenceInfo, type Confidence } from "./extras";
import { notify, state } from "./store";

/** Dialogues propres au mode avancé dont le clavier ne doit pas atteindre le lecteur. */
export const panels = reactive({
  shortcuts: false,
  // outil « Ne jamais flouter ici » : le prochain rectangle tracé devient une zone à ne jamais flouter
  ignoring: false,
  // zone à ne jamais flouter à faire ressortir sur la vidéo (rang dans `project.ignore[clip]`), sinon null
  ignoreHover: null as number | null,
});

const IGNORE_WARNED = "moto-blur:zone-avertie";
/** Avertissement donné une fois, à la première zone à ne jamais flouter créée depuis la relecture :
 *  il complète le message qui vient d'annoncer la zone, et reste affiché jusqu'au clic. */
export function warnIgnoreOnce(): void {
  try {
    if (localStorage.getItem(IGNORE_WARNED)) return;
    localStorage.setItem(IGNORE_WARNED, "1");
  } catch {
    /* stockage indisponible : l'avertissement est simplement redonné */
  }
  const said = state.toastKind === "info" && state.toast ? `${state.toast} ` : "";
  notify(`${said}Attention : un visage ou une plaque qui se tiendrait à cet endroit ne serait pas flouté non plus. La zone se retire dans l'onglet « Flous ».`, "info", true);
}

/** Où se trouve un rectangle dans l'image, en clair : « en bas à droite de l'image ». */
export function placeOf(box: number[], width: number, height: number): string {
  const cx = (box[0] + box[2]) / 2 / Math.max(1, width);
  const cy = (box[1] + box[3]) / 2 / Math.max(1, height);
  const v = cy < 1 / 3 ? "en haut" : cy > 2 / 3 ? "en bas" : "";
  const h = cx < 1 / 3 ? "à gauche" : cx > 2 / 3 ? "à droite" : "";
  if (!v && !h) return "au centre de l'image";
  return `${[v, h].filter(Boolean).join(" ")} de l'image`;
}

/** Nom en clair : « Visage », « Plaque », « Compteur », « Flou ajouté »… (jamais « zone manuelle »). */
export function displayName(track: Track): string {
  if (track.kind === "manual") return "Flou ajouté";
  return blurName(track);
}

/** « Visage · de 0:12 à 0:15 · douteux » */
export function describe(track: Track): string {
  return `${displayName(track)} · ${blurSpan(track)} · ${levelLabel(track)}`;
}

export function levelLabel(track: Track): string {
  return confidenceInfo[confidence(track)].label;
}

/** Ordre « du plus douteux au plus sûr » ; les flous ajoutés par vous viennent en dernier. */
const doubtRank: Record<Confidence, number> = { douteux: 0, retrouve: 0, verifier: 1, ecarte: 2, sur: 3, main: 4 };

export function byDoubt(a: Track, b: Track): number {
  return doubtRank[confidence(a)] - doubtRank[confidence(b)] || a.score - b.score || a.start - b.start || a.id - b.id;
}

export type KindFilter = "all" | "face" | "plate" | "object";
export const kindFilters: { id: KindFilter; label: string }[] = [
  { id: "all", label: "Tous" },
  { id: "face", label: "Visages" },
  { id: "plate", label: "Plaques" },
  { id: "object", label: "Objets" },
];

export function matchesKind(track: Track, filter: KindFilter): boolean {
  if (filter === "all") return true;
  if (filter === "object") return track.kind !== "face" && track.kind !== "plate";
  return track.kind === filter;
}

// --- Raisons d'un passage douteux, rattachées au flou concerné ---------------------------------
// Le moteur donne les raisons de chaque flou (`reasons_by_track`). Pour un projet enregistré par
// une version plus ancienne, on les rattache quand l'état du flou suffit à le savoir ; les autres
// restent affichées une fois, sous la liste.

/** Raison s'appliquant à ce flou, d'après ce que l'on sait de lui (null : impossible à dire). */
function appliesTo(raw: string, track: Track): boolean | null {
  const named = raw.match(/«\s*(.+?)\s*»/)?.[1];
  if (named) return track.kind === "dash" && track.name.toLowerCase() === named.toLowerCase();
  if (raw.startsWith("confiance faible")) return track.source === "auto" && track.score < 0.4;
  if (raw.startsWith("piste très courte")) return track.source === "auto" && track.count < 4;
  return null;
}

/** Raisons brutes réparties entre les flous du passage, et celles qui ne se rattachent à aucun. */
export function splitReasons(item: ReviewItem, tracks: Track[]): { byTrack: Map<number, string[]>; rest: string[] } {
  const byTrack = new Map<number, string[]>();
  const rest: string[] = [];
  // Le moteur donne les raisons de chaque flou : on s'en sert quand elles sont là.
  const given = item.reasons_by_track;
  if (given && Object.keys(given).length) {
    const ids = new Set(tracks.map((t) => t.id));
    for (const [id, reasons] of Object.entries(given)) {
      if (ids.has(Number(id)) && reasons.length) byTrack.set(Number(id), [...reasons]);
    }
    // Raisons qui ne viennent d'aucun flou détaillé (objet suivi, par exemple) : sous la liste.
    const attached = new Set([...byTrack.values()].flat().map((r) => r.split(" (")[0]));
    for (const raw of item.reasons) if (!attached.has(raw.split(" (")[0])) rest.push(raw);
    return { byTrack, rest };
  }
  for (const raw of item.reasons) {
    if (tracks.length === 1) {
      byTrack.set(tracks[0].id, [...(byTrack.get(tracks[0].id) ?? []), raw]);
      continue;
    }
    const verdicts = tracks.map((t) => appliesTo(raw, t));
    if (verdicts.some((v) => v === null)) {
      rest.push(raw);
      continue;
    }
    const hits = tracks.filter((_, i) => verdicts[i]);
    if (!hits.length) rest.push(raw);
    for (const t of hits) byTrack.set(t.id, [...(byTrack.get(t.id) ?? []), raw]);
  }
  return { byTrack, rest };
}
