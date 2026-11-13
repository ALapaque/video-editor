// Présentation des flous dans le mode avancé : nom en clair, niveau de confiance, tri du plus
// douteux au plus sûr, filtre par type, et raisons d'un passage douteux rattachées à chaque flou.
// S'appuie sur le vocabulaire commun d'`extras` (confidence, confidenceInfo, blurName, blurSpan).
import { reactive } from "vue";
import type { ReviewItem, Track } from "./api";
import { blurName, blurSpan, confidence, confidenceInfo, type Confidence } from "./extras";

/** Dialogues propres au mode avancé dont le clavier ne doit pas atteindre le lecteur. */
export const panels = reactive({ shortcuts: false });

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
const doubtRank: Record<Confidence, number> = { douteux: 0, verifier: 1, ecarte: 2, sur: 3, main: 4 };

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
// Le moteur regroupe les raisons de tous les flous d'un passage sans dire à quel flou chacune
// s'applique. On les rattache quand l'état du flou suffit à le savoir ; les autres restent
// affichées une fois, sous la liste.

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
