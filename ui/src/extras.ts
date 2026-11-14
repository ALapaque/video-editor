// Appels vers les fonctions ajoutées au moteur : annuler / rétablir, préréglages, estimation,
// flou ajouté qui suit, relecture mémorisée, capture, vignettes, récents, ménage, profil.
// Couche commune aux deux modes : les composants l'importent, sans la dupliquer.

import { call, type ExportPreset, type Preset, type Recent, type RenderOptions, type Storage, type State, type Track, type Tune } from "./api";
import { computed } from "vue";
import { busy, mapRange, notify, project, refresh, send, start, state, trackById } from "./store";

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
    text: "Nuit, pluie, contre-jour, plaques sales ou lointaines : la détection est plus sensible.",
    warning: "Analyse nettement plus longue, et davantage de flous en trop.",
  },
  rapide: {
    label: "Rapide",
    text: "Pour une vidéo simple ou un premier essai : analyse environ deux fois plus courte.",
    warning: "Moins sûr : un visage ou une plaque petits ou furtifs peuvent être manqués. Relisez la vidéo avant de la publier.",
  },
  perso: {
    label: "Personnalisé",
    text: "Réglages de détection modifiés à la main.",
    warning: "",
  },
};

/** Préréglages que l'on peut choisir (« Personnalisé » ne fait que constater des réglages modifiés). */
export const choosablePresets: Preset[] = ["normal", "difficile", "rapide"];

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

// --- Tout à la main : aucun flou automatique, seuls les flous posés par l'utilisateur -----------

/** Catégories floutées automatiquement dans le projet ouvert : "face", "plate", ou aucune. */
export const autoKinds = computed<string[]>(() => (project.value?.settings.analysis.kinds as string[] | undefined) ?? []);
/** Rien n'est flouté automatiquement : le projet ne demande aucune analyse, seul ce qui est
 *  entouré à la main est flouté. */
export const manualOnly = computed(() => !!project.value && autoKinds.value.length === 0);
/** La contrepartie, dite avec les mêmes mots dans les deux modes. */
export const MANUAL_WARNING = "Rien n'est flouté automatiquement : ce que vous n'entourez pas reste visible.";

/** Choisit ce qui est flouté automatiquement (liste vide : tout se pose à la main). Les flous
 *  automatiques d'une catégorie retirée disparaissent ; l'analyse est à refaire pour ce qui reste
 *  ou revient. Vrai si le moteur a accepté. */
export async function setAutoKinds(kinds: string[]): Promise<boolean> {
  return !!(await send("PUT", "/api/settings", { analysis: { kinds } }));
}

/** Flou posé à la main dont le début et la fin se règlent : flou fixe, ou flou ajouté qui suit ce
 *  qu'on a entouré (celui-là ne peut qu'être raccourci). */
export function spanEditable(track: Track): boolean {
  return track.source === "manual" || (track.source === "tracker" && track.kind === "manual");
}

/** Début et fin d'un flou posé à la main, en secondes de temps source du clip (annulable). Un flou
 *  fixe se rallonge ou se raccourcit ; un flou qui suit ne peut qu'être raccourci. Vrai si accepté. */
export async function setBlurSpan(clip: string, id: number, startAt: number, endAt: number): Promise<boolean> {
  return !!(await send("PATCH", "/api/tracks", { clip, id, span: [startAt, endAt] }));
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
  // le projet fait partie de l'adresse : « c1 » d'un autre projet ne doit pas ressortir du cache
  return `/api/filmstrip?clip=${encodeURIComponent(clip)}&count=${count}&h=${height}&p=${encodeURIComponent(project.value?.path ?? "")}`;
}

const waves = new Map<string, Promise<number[]>>();
/** Niveau sonore du clip : `points` valeurs entre 0 et 1, réparties sur toute sa durée (vide : pas de son). */
export function waveform(clip: string, points = 1500): Promise<number[]> {
  const key = `${project.value?.path}:${clip}:${points}`; // « c1 » désigne un autre fichier dans un autre projet
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
  { id: "youtube", label: "YouTube", text: "Taille d'origine, qualité maximale : YouTube recompresse de toute façon." },
  { id: "social", label: "Réseaux sociaux", text: "1920 pixels au plus, H.264 : accepté partout, fichier raisonnable." },
  { id: "leger", label: "Fichier léger", text: "1280 pixels au plus : pour envoyer par messagerie." },
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
    notify("Réglages enregistrés\u00a0: ils pourront être repris pour vos prochaines vidéos.");
  } catch (error) {
    notify((error as Error).message, "error");
  }
}

/** Applique le profil au projet ouvert : réglages, puis recherche des objets mémorisés dans
 *  chaque clip (traitement "profile" ; son message final dit ce qui a été retrouvé ou non). */
export async function applyProfile(): Promise<void> {
  await start("/api/profile/apply");
}

// --- Régler d'après mes corrections ---------------------------------------------------------
// Les flous que l'utilisateur a retirés sont des erreurs du logiciel. Le moteur cherche, sans rien
// réanalyser, les réglages de flou qui n'en produiraient plus le plus grand nombre, sans perdre un
// seul flou gardé. La proposition s'applique comme un réglage de flou ordinaire (annulable).

/** Proposition du moteur (réponse immédiate). `undefined` si le moteur ne répond pas. */
export async function tuneSuggestion(): Promise<Tune | undefined> {
  try {
    return await call<Tune>("GET", "/api/tune");
  } catch {
    return undefined;
  }
}

/** « 14 des 18 flous que vous avez retirés ne seraient plus produits » (sans majuscule ni point). */
export function tuneGain(tune: Tune): string {
  const n = tune.best.wrong_avoided;
  const total = tune.corrections;
  if (total <= 1) return "le flou que vous avez retiré ne serait plus produit";
  if (n >= total) return `les ${total} flous que vous avez retirés ne seraient plus produits`;
  return `${n} des ${total} flous que vous avez retirés ne ${n > 1 ? "seraient plus produits" : "serait plus produit"}`;
}

/** Applique les réglages proposés. Vrai si le moteur les a acceptés. */
export async function applyTune(tune: Tune): Promise<boolean> {
  return !!(await send("PUT", "/api/settings", { blur: tune.settings }));
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

// --- Vocabulaire commun : un « flou » et son niveau de confiance ------------------------------
// L'interface parle de « flou » partout (jamais de « zone », « détection » ou « piste ») et
// remplace les scores chiffrés par trois niveaux. Règle unique, utilisée par les deux modes.

export type Confidence = "sur" | "verifier" | "douteux" | "ecarte" | "main";

export const confidenceInfo: Record<Confidence, { label: string; help: string }> = {
  sur: { label: "sûr", help: "Le logiciel est sûr de ce qu'il a trouvé." },
  verifier: { label: "à vérifier", help: "Le logiciel hésite un peu : jetez un œil." },
  douteux: { label: "douteux", help: "Le logiciel hésite beaucoup : c'est peut-être une erreur." },
  ecarte: { label: "non flouté", help: "Trop peu sûr pour être flouté. Vous pouvez le flouter quand même." },
  main: { label: "ajouté par vous", help: "Flou que vous avez ajouté ou objet que vous avez désigné." },
};

/** Niveau de confiance d'un flou, d'après son score habituel (médian) sur toute sa durée. */
export function confidence(track: Track): Confidence {
  if (track.source !== "auto") return "main";
  if (track.below_min_score && !track.forced) return "ecarte";
  if (track.score >= 0.6) return "sur";
  if (track.score >= 0.4) return "verifier";
  return "douteux";
}

/** Nom d'un flou en clair, avec une majuscule : « Visage », « Plaque », « Compteur »… */
export function blurName(track: Track): string {
  const name = track.source === "manual" && track.kind === "manual" ? "Flou ajouté" : track.name;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** « de 0:12 à 0:15 » (temps du clip, sans décimales). */
export function blurSpan(track: Track): string {
  const t = (x: number) => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`;
  return t(track.start) === t(track.end) ? `à ${t(track.start)}` : `de ${t(track.start)} à ${t(track.end)}`;
}

// --- Plus rien à valider -----------------------------------------------------------------------
// Tout ce que le logiciel a détecté est flouté : un flou douteux ne fait courir aucun risque, il
// est au pire inutile. On ne demande donc plus de confirmer les flous. Il reste :
//  - la relecture (seul moyen de voir ce qui n'a jamais été détecté), où un clic retire un flou ;
//  - les flous « gênants » (gros ou longs), à retirer par lot si on veut ;
//  - les alertes (`risk`) : endroits où quelque chose peut rester visible.

/** Gêne à partir de laquelle un flou inutile se remarque (taille en % de l'image × durée en s). */
export const NUISANCE_MIN = 0.3;

/** Flous automatiques appliqués (ni retirés, ni écartés pour manque de confiance). */
export function appliedBlurs(tracks: Track[]): Track[] {
  return tracks.filter((t) => t.source === "auto" && !t.disabled && (!t.below_min_score || t.forced));
}

/** Parmi les flous appliqués, ceux qui se voient : les plus gênants d'abord. */
export function annoyingBlurs(tracks: Track[]): Track[] {
  return appliedBlurs(tracks).filter((t) => t.nuisance >= NUISANCE_MIN).sort((a, b) => b.nuisance - a.nuisance);
}

/** Objets que le logiciel a vus mais n'a PAS floutés (trop peu sûr), les plus probables d'abord.
 *  C'est là qu'un vrai visage ou une vraie plaque peut se cacher. */
export function almostBlurs(tracks: Track[]): Track[] {
  return tracks
    .filter((t) => t.source === "auto" && !t.disabled && t.below_min_score && !t.forced && t.best_score >= 0.2)
    .sort((a, b) => b.best_score - a.best_score);
}

/** Alertes : passages où quelque chose peut rester visible, pas encore regardés. */
export const pendingRisks = computed(() =>
  // seulement dans ce qui est gardé au montage : une alerte dans un passage coupé n'a pas à être regardée
  (project.value?.review ?? []).filter((r) => r.risk && !r.done && mapRange(r).length > 0),
);

/** Ce qui reste avant d'exporter l'esprit tranquille. Rien d'autre n'est demandé. */
export const beforeExport = computed(() => ({
  swept: project.value?.sweep.done ?? false,
  risks: pendingRisks.value.length,
  marks: project.value?.marks.length ?? 0,
}));

/** Retire un flou d'un clic (relecture, liste, planche) et prépare les suites possibles dans
 *  `state.lastRemoved` : annuler, retirer les flous au même endroit, ne plus jamais flouter là. */
export async function removeBlur(clip: string, id: number, group: number[] = []): Promise<void> {
  const track = trackById(clip, id);
  if (!track || track.disabled) return;
  if (track.source !== "auto") {
    notify("Ce flou a été ajouté par vous : supprimez-le depuis la liste des flous.");
    return;
  }
  // `group` : les flous réunis à l'écran dans la même tache que celui-ci. On les retire ensemble,
  // en une seule action, pour que « cliquez dessus, il disparaît » soit vrai.
  const others = group.filter((g) => {
    const t = trackById(clip, g);
    return g !== id && !!t && t.source === "auto" && !t.disabled;
  });
  if (!(await send("PATCH", "/api/tracks", { clip, ids: [id, ...others], disabled: true }))) return;
  let similar: number[] = [];
  try {
    similar = (await call<{ ids: number[] }>("GET", `/api/similar?clip=${encodeURIComponent(clip)}&id=${id}`)).ids;
  } catch {
    /* sans gravité : pas de proposition */
  }
  state.lastRemoved = { clip, ids: [id, ...others], name: blurName(track), similar: similar.filter((s) => !others.includes(s)) };
}

/** « Retirer aussi les N flous au même endroit ». */
export async function removeSimilar(): Promise<void> {
  const last = state.lastRemoved;
  if (!last?.similar.length) return;
  await setTracksDisabled(last.clip, last.similar, true);
  state.lastRemoved = { ...last, ids: [...last.ids, ...last.similar], similar: [] };
}

/** « Annuler » : remet les flous du dernier retrait. */
export async function restoreRemoved(): Promise<void> {
  const last = state.lastRemoved;
  if (!last) return;
  await setTracksDisabled(last.clip, last.ids, false);
  state.lastRemoved = null;
}

/** « Ne plus jamais flouter cet endroit » : zone à ne jamais flouter autour du flou retiré.
 *  Vaut pour tout le clip, maintenant et après une nouvelle analyse. */
export async function neverBlurHere(): Promise<void> {
  const last = state.lastRemoved;
  if (!last) return;
  const next = await send("POST", "/api/ignore", { clip: last.clip, id: last.ids[0] });
  if (next) {
    const n = (next as State & { ignored?: number }).ignored ?? 0;
    notify(n ? `Cet endroit ne sera plus flouté (${n} autre${n > 1 ? "s" : ""} flou${n > 1 ? "s" : ""} retiré${n > 1 ? "s" : ""}).` : "Cet endroit ne sera plus flouté.");
    state.lastRemoved = null;
  }
}

/** Zone à ne jamais flouter tracée à la main (`box` en pixels de l'image source). */
export async function addIgnoreZone(clip: string, box: number[]): Promise<number | undefined> {
  const next = await send("POST", "/api/ignore", { clip, box });
  return next ? ((next as State & { ignored?: number }).ignored ?? 0) : undefined;
}

/** Retire une zone à ne jamais flouter ; les flous qu'elle écartait reviennent. */
export async function removeIgnoreZone(clip: string, index: number): Promise<void> {
  await send("DELETE", `/api/ignore?clip=${encodeURIComponent(clip)}&index=${index}`);
}
