// Appels vers les fonctions ajoutées au moteur : annuler / rétablir, préréglages, estimation,
// flou ajouté qui suit, relecture mémorisée, capture, vignettes, récents, ménage, profil.
// Couche commune aux deux modes : les composants l'importent, sans la dupliquer.

import { call, type ExportPreset, type Preset, type Recent, type RenderOptions, type Storage, type State } from "./api";
import { busy, notify, project, refresh, send, start, state } from "./store";

// --- Annuler / rétablir -------------------------------------------------------

/** Annule la dernière modification (montage, réglage de flou, correction). */
export async function undo(): Promise<void> {
  if (busy.value || !project.value?.history.undo) return;
  const next = await send("POST", "/api/undo");
  if (next?.undone) notify(`Annulé : ${next.undone}`);
}

export async function redo(): Promise<void> {
  if (busy.value || !project.value?.history.redo) return;
  const next = await send("POST", "/api/redo");
  if (next?.redone) notify(`Rétabli : ${next.redone}`);
}

/** Ctrl+Z / Ctrl+Y (ou Cmd+Z / Maj+Cmd+Z), hors des champs de saisie. À brancher une fois, dans App. */
export function historyShortcut(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null;
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  if (key === "z" && !event.shiftKey) {
    event.preventDefault();
    void undo();
  } else if (key === "y" || (key === "z" && event.shiftKey)) {
    event.preventDefault();
    void redo();
  }
}

// --- Analyse : préréglage et estimation ------------------------------------------

export const presetInfo: Record<Preset, { label: string; text: string; warning: string }> = {
  normal: { label: "Normal", text: "Le bon équilibre pour la plupart des vidéos.", warning: "" },
  difficile: {
    label: "Conditions difficiles",
    text: "Nuit, pluie, contre-jour, plaques sales ou lointaines : la détection est plus sensible.",
    warning: "Analyse nettement plus longue, et davantage de flous à vérifier.",
  },
  rapide: {
    label: "Rapide",
    text: "Pour une vidéo simple ou un premier essai : analyse environ deux fois plus courte.",
    warning: "Moins sûr : un visage ou une plaque petits ou furtifs peuvent être manqués. Relisez la vidéo avant de la publier.",
  },
};

/** Change le préréglage d'analyse. Ce qui était déjà analysé devra l'être à nouveau. */
export async function setPreset(preset: Preset): Promise<void> {
  await send("PUT", "/api/settings", { preset });
}

/** Mesure la vitesse d'analyse sur quelques images (quelques secondes) : le résultat arrive dans
 *  `project.estimate` à la fin du traitement de type "estimate". */
export async function measureEstimate(): Promise<void> {
  await start("/api/estimate");
}

/** « environ 9 min », « moins d'une minute », « environ 1 h 20 ». */
export function formatDuration(seconds: number): string {
  if (seconds < 50) return "moins d'une minute";
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `environ ${minutes} min`;
  return `environ ${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

// --- Flou ajouté qui suit ce qu'on a entouré --------------------------------------

/** Ajoute un flou qui suit ce qui est entouré (`box` en pixels de l'image source, à l'instant
 *  `time` du clip), dans la portion [start, end] du clip (secondes de temps source). Le suivi
 *  s'arrête de lui-même à un changement de plan ou si l'objet est perdu. Traitement "follow" ;
 *  à la fin, `job.created` est l'identifiant de la zone créée. S'annule avec « Annuler ». */
export async function followBlur(clip: string, time: number, box: number[], startAt: number, endAt: number): Promise<void> {
  await start("/api/follow", { clip, time, box, start: startAt, end: endAt });
}

// --- Corrections par lot ----------------------------------------------------------

/** Retire (ou remet) plusieurs flous d'un clip en une seule action annulable. */
export async function setTracksDisabled(clip: string, ids: number[], disabled: boolean): Promise<void> {
  if (ids.length) await send("PATCH", "/api/tracks", { clip, ids, disabled });
}

/** Adresse de la vignette d'un flou : l'image où il est le mieux visible, avec son contour. */
export function thumbUrl(clip: string, id: number, width = 220): string {
  return `/api/thumb?clip=${encodeURIComponent(clip)}&id=${id}&w=${width}&v=${state.data.version}`;
}

// --- Relecture complète mémorisée --------------------------------------------------

/** Mémorise où en est la relecture complète (secondes du montage) et si elle est terminée.
 *  N'entre pas dans l'historique d'annulation. */
export async function saveSweep(position: number, done: boolean): Promise<void> {
  try {
    state.data = { ...state.data, ...(await call<State>("PUT", "/api/sweep", { position, done })) };
  } catch {
    /* sans gravité : la position sera renvoyée au prochain arrêt */
  }
}

// --- Timeline : bande d'images et forme d'onde --------------------------------------

/** Bande de `count` vignettes du clip, côte à côte dans une seule image (hauteur `height`). */
export function filmstripUrl(clip: string, count = 40, height = 54): string {
  return `/api/filmstrip?clip=${encodeURIComponent(clip)}&count=${count}&h=${height}`;
}

const waves = new Map<string, Promise<number[]>>();
/** Niveau sonore du clip : `points` valeurs entre 0 et 1, réparties sur toute sa durée (vide : pas de son). */
export function waveform(clip: string, points = 1500): Promise<number[]> {
  const key = `${clip}:${points}`;
  let wave = waves.get(key);
  if (!wave) {
    wave = call<{ peaks: number[] }>("GET", `/api/waveform?clip=${encodeURIComponent(clip)}&points=${points}`)
      .then((r) => r.peaks)
      .catch(() => []);
    waves.set(key, wave);
  }
  return wave;
}

// --- Capture d'image ----------------------------------------------------------------

/** Enregistre l'image affichée en PNG pleine résolution à côté du projet (floutée par défaut). */
export async function snapshot(clip: string, frame: number, blur = true): Promise<string | undefined> {
  try {
    const result = await call<{ path: string }>("POST", "/api/snapshot", { clip, frame, blur });
    notify(`Image enregistrée : ${result.path}`, "info", true);
    return result.path;
  } catch (error) {
    notify((error as Error).message, "error");
    return undefined;
  }
}

// --- Export -------------------------------------------------------------------------

export const exportPresets: { id: ExportPreset; label: string; text: string }[] = [
  { id: "source", label: "Comme l'original", text: "Même taille et même format que vos vidéos. Le choix par défaut." },
  { id: "youtube", label: "YouTube", text: "Taille d'origine, qualité maximale : YouTube recompresse de toute façon." },
  { id: "social", label: "Réseaux sociaux", text: "1920 pixels au plus, H.264 : accepté partout, fichier raisonnable." },
  { id: "leger", label: "Fichier léger", text: "1280 pixels au plus : pour envoyer par messagerie." },
];

export async function render(options: RenderOptions): Promise<void> {
  await start("/api/render", options);
}

/** Exporte un court extrait autour de l'instant `around` (secondes du montage) pour contrôler
 *  le résultat réel avant le rendu complet. */
export async function renderExtract(output: string, around: number, duration = 10, options: Partial<RenderOptions> = {}): Promise<void> {
  await render({ ...options, output, window: { start: Math.max(0, around - duration / 2), duration } });
}

// --- Projets récents, place occupée, profil ---------------------------------------------

export async function recentProjects(): Promise<Recent[]> {
  try {
    return (await call<{ projects: Recent[] }>("GET", "/api/recent")).projects;
  } catch {
    return [];
  }
}

export async function storage(): Promise<Storage | undefined> {
  try {
    return await call<Storage>("GET", "/api/storage");
  } catch (error) {
    notify((error as Error).message, "error");
    return undefined;
  }
}

/** Supprime les fichiers d'aperçu d'un projet (ils se refabriquent à sa prochaine ouverture).
 *  Refusé par le moteur pour le projet ouvert. */
export async function deletePreviews(path: string): Promise<Storage | undefined> {
  try {
    return await call<Storage>("DELETE", `/api/storage?path=${encodeURIComponent(path)}`);
  } catch (error) {
    notify((error as Error).message, "error");
    return undefined;
  }
}

export function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1).replace(".", ",")} Go`;
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} Mo`;
  return `${Math.max(1, Math.round(bytes / 1e3))} ko`;
}

/** Enregistre les réglages du projet et ses objets suivis comme profil « ma moto ». */
export async function saveProfile(): Promise<void> {
  try {
    await call("POST", "/api/profile/save");
    await refresh();
    notify("Profil enregistré : il pourra être appliqué à vos prochaines vidéos.");
  } catch (error) {
    notify((error as Error).message, "error");
  }
}

/** Applique le profil au projet ouvert : réglages, puis recherche des objets mémorisés dans
 *  chaque clip (traitement "profile" ; son message final dit ce qui a été retrouvé ou non). */
export async function applyProfile(): Promise<void> {
  await start("/api/profile/apply");
}

// --- Glisser-déposer (application installée) ---------------------------------------------

/** L'application installée appelle `window.motoBlurDrop(chemins)` quand des fichiers sont déposés
 *  sur la fenêtre. Dans un navigateur, un fichier déposé n'a pas de chemin : on le dit. */
export function installDrop(open: (paths: string[]) => void): void {
  (window as unknown as { motoBlurDrop: (paths: string[]) => void }).motoBlurDrop = open;
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer?.files.length) {
      notify("Dans le navigateur, un fichier déposé ne donne pas son emplacement : utilisez « Ouvrir ». Le glisser-déposer fonctionne dans l'application installée.", "info", true);
    }
  });
}
