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
  kinds: string[];
  reasons: string[];
  tracks: number[];
}

export interface Track {
  id: number;
  kind: string;
  source: "auto" | "tracker" | "manual";
  disabled: boolean;
  start: number;
  end: number;
  extend_before_s: number | null;
  extend_after_s: number | null;
  score: number;
  count: number;
  keyframes?: number[][];
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
  tracks: Record<string, Track[]>;
  problem: string | null;
  /** Les clips du montage n'ont pas tous le même format. */
  mismatch: boolean;
  /** Choix d'alignement : "" (pas encore choisi), "first" ou "largest". */
  conform: string;
}

export interface Job {
  kind: string;
  label: string;
  done: number;
  total: number;
  fps: number;
  state: "running" | "done" | "error" | "cancelled";
  message: string;
}

export interface State {
  project: Project | null;
  job: Job | null;
  version: number;
  created?: number;
}

/** Zone sur une image : x1, y1, x2, y2, forme, rayon, catégorie, piste, score (null si interpolée). */
export type ZoneTuple = [number, number, number, number, string, number, string, number, number | null];

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
