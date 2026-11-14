// Position de l'assistant du mode simple. Elle vit hors du composant pour survivre à un passage
// par le mode avancé ; à l'ouverture d'un projet, elle est de toute façon déduite de son état.

import { reactive } from "vue";
import type { ExportPreset } from "./api";

export type Screen = "video" | "profile" | "what" | "object" | "trim" | "type" | "analyse" | "verify" | "export" | "done";

export const wiz = reactive({
  screen: "video" as Screen,
  path: "", // projet auquel la position se rapporte
  objectWanted: false, // « Un objet précis » coché, avant même d'en avoir désigné un
  launched: false, // analyse lancée depuis l'assistant pendant cette séance
  launchedExport: false,
  exportFor: "", // projet pour lequel le nom du fichier de sortie a été proposé
  output: "", // chemin de la dernière vidéo créée
  profileAsked: "", // projet pour lequel « Reprendre vos réglages habituels ? » a déjà reçu une réponse
  profileSaved: "", // projet dont les réglages viennent d'être enregistrés comme réglages habituels
  format: "source" as ExportPreset, // format choisi pour la vidéo à créer
  extract: "", // chemin du dernier extrait d'essai créé
});

// « Je choisis moi-même » : le choix est retenu par projet, dans le navigateur (le fichier projet
// ne le connaît pas). Sans lui, un projet où rien n'est automatique et où aucun objet n'est désigné
// est de toute façon traité comme « tout à la main ».
const HAND_KEY = "moto-blur:a-la-main";
function storedHands(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(HAND_KEY) ?? "[]");
    return Array.isArray(saved) ? saved.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return []; // stockage indisponible : le choix vaut pour cette séance seulement
  }
}
/** Projets (chemins) pour lesquels « Je choisis moi-même » est coché. */
export const hands = reactive({ paths: storedHands() });

export function rememberHand(path: string, on: boolean): void {
  const others = hands.paths.filter((p) => p !== path);
  hands.paths = on ? [...others, path].slice(-60) : others;
  try {
    localStorage.setItem(HAND_KEY, JSON.stringify(hands.paths));
  } catch {
    /* stockage indisponible */
  }
}

/** Glisser-déposer : ce que l'assistant fait des fichiers déposés tant qu'il est à l'écran
 *  (null en mode avancé). L'écoute de la fenêtre est posée une fois, par l'application. */
export const drop = { target: null as ((paths: string[]) => void) | null };

const VIDEO = /\.(mp4|mov|mkv|m4v|avi|mts|webm)$/i;

/** Premier fichier déposé que l'assistant sait ouvrir : une vidéo, sinon un fichier projet. */
export function droppedFile(paths: string[]): string | undefined {
  return paths.find((p) => VIDEO.test(p)) ?? paths.find((p) => /\.json$/i.test(p));
}

/** « 3:12 » : un moment de la vidéo, pour un bouton ou une phrase. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** « aujourd'hui », « hier », « le 3 novembre », « le 3 novembre 2024 ». */
export function dayText(unixSeconds: number): string {
  const date = new Date(unixSeconds * 1000);
  const today = new Date();
  const days = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() - new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()) / 86400000);
  if (days <= 0) return "aujourd'hui";
  if (days === 1) return "hier";
  if (days < 7) return `il y a ${days} jours`;
  const text = date.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: date.getFullYear() === today.getFullYear() ? undefined : "numeric" });
  return `le ${text}`;
}
