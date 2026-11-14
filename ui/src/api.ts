// Types et appels vers le moteur local (serveur Python sur 127.0.0.1).

export interface Clip {
  id: string;
  name: string;
  path: string;
  width: number;
  height: number;
  fps_num: number;
  fps_den: number;
  frames: number;
  duration: number;
  codec: string;
  pix_fmt: string;
  has_audio: boolean;
  proxy: boolean;
  /** Aperçu net (1080 pixels sur le petit côté) déjà fabriqué pour ce clip. */
  proxy_fine: boolean;
  /** L'aperçu net apporte quelque chose : la source est plus grande que l'aperçu rapide. */
  fine_useful: boolean;
  missing: boolean;
  needs_analysis: boolean;
}

/** Passage gardé ou muet : secondes depuis le début du clip source. */
export interface Range {
  clip: string;
  start: number;
  end: number;
}

export interface ReviewItem extends Range {
  /** Identifiant stable du passage ; il change si le passage change (il est alors redemandé). */
  key: string;
  /** Validé par l'utilisateur pendant la vérification guidée. */
  done: boolean;
  kinds: string[];
  reasons: string[];
  tracks: number[];
  /** Vrai : quelque chose peut rester visible ici (objet suivi perdu, flou posé sur un autre
   *  objet) : c'est une alerte à regarder. Faux : flou douteux mais appliqué, donc sans risque ;
   *  le vérifier est facultatif. */
  risk: boolean;
  /** Raisons propres à chaque flou du passage (clé : identifiant du flou). */
  reasons_by_track?: Record<string, string[]>;
}

export interface Track {
  id: number;
  kind: string;
  /** Nom à afficher : celui de l'objet désigné à la main (« compteur », « téléphone »…), sinon la catégorie. */
  name: string;
  source: "auto" | "tracker" | "manual";
  disabled: boolean;
  /** Floutée même si son score est sous le minimum. */
  forced: boolean;
  /** Détection écartée : son meilleur score n'atteint pas le minimum, elle n'est pas floutée. */
  below_min_score: boolean;
  best_score: number;
  start: number;
  end: number;
  extend_before_s: number | null;
  extend_after_s: number | null;
  /** Objet suivi dont le contour précis a été calculé (flou qui épouse l'objet). */
  contour?: boolean;
  score: number;
  count: number;
  /** Place du flou à l'écran, en % de l'image. */
  area: number;
  /** Gêne du flou s'il est inutile : taille (% de l'image) × durée (jusqu'à 5 s). */
  nuisance: number;
  keyframes?: number[][];
}

/** Signalement posé pendant la relecture complète : instant d'un clip, en secondes de temps source. */
export interface Mark {
  id: number;
  clip: string;
  time: number;
}

export interface Settings {
  analysis: Record<string, string | number | boolean | string[]>;
  blur: Record<string, string | number | boolean>;
}

export interface Project {
  path: string;
  name: string;
  clips: Clip[];
  timeline: Range[];
  mutes: Range[];
  settings: Settings;
  review: ReviewItem[];
  marks: Mark[];
  tracks: Record<string, Track[]>;
  problem: string | null;
  /** Les clips du montage n'ont pas tous le même format. */
  mismatch: boolean;
  /** Choix d'alignement : "" (pas encore choisi), "first" ou "largest". */
  conform: string;
  /** Préréglage d'analyse : "normal", "difficile" (conditions difficiles), "rapide", ou "perso"
   *  quand les réglages de détection ont été modifiés à la main. */
  preset: Preset;
  /** Relecture complète : position atteinte (secondes du montage) et relecture terminée ou non. */
  sweep: { position: number; done: boolean };
  /** « Analyser » ne fera que revoir de plus près les flous douteux (court) : rien n'est à analyser. */
  recheck_only: boolean;
  /** Zones à ne jamais flouter, par clip : rectangles [x1, y1, x2, y2] en pixels de l'image source. */
  ignore: Record<string, number[][]>;
  /** Nom de la modification que « Annuler » / « Rétablir » défera ou refera (null : rien). */
  history: { undo: string | null; redo: string | null };
  /** Dernière estimation de durée d'analyse encore valable (null : pas mesurée ou périmée). */
  estimate: Estimate | null;
}

/** "perso" : réglages de détection modifiés à la main, qui ne correspondent à aucun préréglage. */
export type Preset = "normal" | "difficile" | "rapide" | "perso";

export interface Estimate {
  frames: number; // images restant à analyser
  seconds: number; // durée estimée de l'analyse
  fps: number; // vitesse mesurée, en images par seconde
}

/** Profil « ma moto » enregistré sur cet ordinateur. */
export interface Profile {
  saved_at: number; // secondes Unix
  preset: Preset;
  kinds: string[];
  objects: string[]; // noms des objets suivis mémorisés (« compteur », « téléphone »…)
}

export interface Recent {
  path: string;
  name: string;
  folder: string;
  modified: number; // secondes Unix
}

export interface Storage {
  previews: { path: string; name: string; size: number; current: boolean; orphan: boolean }[];
  models: number; // octets
  freed?: number;
}

/** Préréglages d'export : même format que la source, qualité maximale, réseaux sociaux, fichier léger. */
export type ExportPreset = "source" | "youtube" | "social" | "leger";

export interface RenderOptions {
  output: string;
  preset?: ExportPreset;
  quality?: number;
  codec?: string;
  encoder?: string;
  max_side?: number;
  debug?: boolean;
  /** Extrait de contrôle : début et durée, en secondes du montage. Le fichier prend le suffixe « _extrait ». */
  window?: { start: number; duration: number };
}

export interface Job {
  kind: string;
  label: string;
  done: number;
  total: number;
  fps: number;
  state: "running" | "done" | "error" | "cancelled";
  message: string;
  /** Identifiant de la zone créée par le traitement (flou ajouté qui suit), sinon null. */
  created: number | null;
}

export interface State {
  project: Project | null;
  job: Job | null;
  version: number;
  created?: number;
  profile: Profile | null;
  /** Fonctions disponibles sur cette installation (contour précis des objets suivis…). */
  features?: { contour: boolean };
  /** Modèles utilisables, y compris ceux ajoutés par l'utilisateur. */
  models?: { face: string[]; plate: string[] };
  undone?: string;
  redone?: string;
}

/** Zone sur une image : x1, y1, x2, y2, forme, rayon, catégorie, piste, score (null si interpolée),
 *  pistes réunies dans la zone, puis — seulement pour la forme "contour" — le contour précis :
 *  liste de points [x, y] en pixels de l'image source, à tracer comme un polygone fermé. */
export type ZoneTuple = [number, number, number, number, string, number, string, number, number | null, number[]?, number[][]?];

/** Détection écartée sur une image : x1, y1, x2, y2 (boîte brute), catégorie, piste, score. */
export type DroppedTuple = [number, number, number, number, string, number, number | null];

export interface Browse {
  dir: string;
  parent: string | null;
  home: string;
  folders: string[];
  files: { name: string; size: number }[];
  roots: string[];
  sep: string;
}

export async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: `réponse illisible (${response.status})` }));
  if (!response.ok) throw new Error(data.error ?? `erreur ${response.status}`);
  return data as T;
}
