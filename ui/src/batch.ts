// File d'attente : analyser plusieurs vidéos à la suite (on les dépose le soir, on les retrouve
// analysées le matin). État commun aux deux modes : la liste en préparation, le dialogue ouvert
// ou non, et le dernier résultat. Le résultat est gardé dans le navigateur : il reste lisible
// après l'ouverture d'une des vidéos (qui lance un autre traitement) et après un rechargement
// de la page. Rien ne l'efface tout seul ; « Nouvelle file » le retire.

import { computed, reactive, watch } from "vue";
import { call, type BatchResult, type Job, type Preset, type State } from "./api";
import { apply, busy, job, refresh, state } from "./store";

export type BatchPreset = Exclude<Preset, "perso">;

/** Résultat de la dernière file terminée. */
export interface BatchLast {
  state: "done" | "cancelled" | "error";
  message: string;
  results: BatchResult[];
  /** Nombre de vidéos de la file (plus grand que `results.length` si elle a été arrêtée). */
  total: number;
  /** Heure de fin (millisecondes), si cette page était ouverte à ce moment-là. */
  at: number | null;
  sig: string;
}

const KEY = "moto-blur:batch";
const VIDEO = /\.(mp4|mov|mkv|m4v|avi|mts|webm)$/i;
const PROJECT = /\.json$/i;

export const batch = reactive({
  open: false,
  /** Vidéos (ou projets) choisies pour la prochaine file : chemins complets. */
  paths: [] as string[],
  preset: "normal" as BatchPreset,
  /** Reprendre les réglages habituels (profil « ma moto ») pour les vidéos neuves. */
  profile: true,
  /** Ouvrir le dialogue directement sur la confirmation d'arrêt. */
  confirm: false,
  /** Message court affiché dans le dialogue (fichier déposé refusé…). */
  note: "",
  /** Noms des vidéos de la file lancée, dans l'ordre : de quoi montrer où elle en est. */
  launched: [] as string[],
  last: null as BatchLast | null,
  /** Signature du résultat que l'utilisateur a retiré avec « Nouvelle file » : il ne revient pas. */
  dismissed: "",
});

/** Le traitement en cours (ou le dernier) est une file d'attente. */
export const batchJob = computed<Job | null>(() => (job.value?.kind === "batch" ? job.value : null));
export const batchRunning = computed(() => busy.value && job.value?.kind === "batch");
/** « Vidéo 2 sur 5 : sortie.mp4 » (espaces insécables : la ligne ne se coupe pas avant les deux-points). */
export const batchItemText = computed(() => {
  const item = batchJob.value?.item;
  return item ? `Vidéo ${item[0]} sur ${item[1]} : ${item[2]}` : "Préparation de la file…";
});
/** Avancement de l'analyse de la vidéo en cours, en pour cent. */
export const batchPercent = computed(() => {
  const j = batchJob.value;
  return j ? Math.min(100, Math.round((100 * j.done) / Math.max(1, j.total))) : 0;
});

export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

// --- Mémoire du navigateur ----------------------------------------------------------------
function load(): void {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as { last?: BatchLast | null; dismissed?: string; launched?: string[] } | null;
    if (!saved) return;
    batch.last = saved.last && Array.isArray(saved.last.results) ? saved.last : null;
    batch.dismissed = saved.dismissed ?? "";
    batch.launched = Array.isArray(saved.launched) ? saved.launched : [];
  } catch {
    /* stockage indisponible ou illisible : le résultat ne vaut que pour cette séance */
  }
}
function save(): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ last: batch.last, dismissed: batch.dismissed, launched: batch.launched }));
  } catch {
    /* stockage indisponible */
  }
}
load();

// --- Liste en préparation -----------------------------------------------------------------
/** Ajoute des vidéos (ou des projets) à la liste, sans doublon. Renvoie le nombre de fichiers ajoutés. */
export function addBatchPaths(paths: string[]): number {
  let added = 0;
  for (const path of paths) {
    if (!(VIDEO.test(path) || PROJECT.test(path)) || batch.paths.includes(path)) continue;
    batch.paths.push(path);
    added++;
  }
  return added;
}

export function removeBatchPath(path: string): void {
  batch.paths = batch.paths.filter((p) => p !== path);
}

/** Ouvre le dialogue ; `confirm` : directement sur « Arrêter la file ? ». */
export function openBatch(confirm = false): void {
  batch.note = "";
  batch.confirm = confirm;
  batch.open = true;
}

/** Fichiers déposés sur la fenêtre (application installée). Vrai si le dialogue les a pris. */
export function dropToBatch(paths: string[]): boolean {
  if (!batch.open) return false;
  if (batchRunning.value) batch.note = "La file est en cours : attendez sa fin pour ajouter des vidéos.";
  else if (batch.last) batch.note = "Cliquez d'abord sur « Nouvelle file », puis déposez vos vidéos.";
  else {
    const added = addBatchPaths(paths);
    const known = paths.some((p) => batch.paths.includes(p));
    batch.note = added ? "" : known ? "Ces vidéos sont déjà dans la liste." : "Déposez des vidéos (mp4, mov…).";
  }
  return true;
}

// --- Lancement, résultat --------------------------------------------------------------------
/** Lance la file. Renvoie le message d'erreur du moteur, ou "" si elle a démarré. */
export async function launchBatch(): Promise<string> {
  const saved = state.data.profile;
  const useProfile = batch.profile && !!saved;
  // avec les réglages habituels, le type de vidéo est celui du profil (le moteur l'applique de
  // toute façon aux vidéos neuves) ; « personnalisé » ne se demande pas : rien n'est envoyé
  const preset = useProfile ? (saved.preset === "perso" ? undefined : saved.preset) : batch.preset;
  try {
    const next = await call<State>("POST", "/api/batch", { paths: batch.paths, preset, profile: useProfile });
    batch.launched = batch.paths.map(fileName);
    batch.paths = [];
    batch.last = null;
    batch.note = "";
    save();
    apply(next);
    void refresh();
    return "";
  } catch (error) {
    return (error as Error).message;
  }
}

/** « Nouvelle file » : le résultat affiché est retiré, la liste est prête pour d'autres vidéos. */
export function newBatch(): void {
  if (batch.last) batch.dismissed = batch.last.sig;
  batch.last = null;
  batch.launched = [];
  batch.note = "";
  save();
}

function signature(j: Job): string {
  return `${j.state}|${j.message}|${(j.results ?? []).map((r) => `${r.path}:${r.seconds}`).join("|")}`;
}

// La file démarre ou tourne déjà (page rechargée) : son dialogue s'ouvre. Elle se termine : le
// résultat est retenu et le dialogue s'ouvre dessus, même s'il avait été réduit.
watch(() => [job.value?.kind, job.value?.state] as const, ([kind, now], before) => {
  const j = job.value;
  if (!j || kind !== "batch" || !now) return;
  const wasRunning = before?.[0] === "batch" && before[1] === "running";
  if (now === "running") {
    if (!wasRunning) batch.open = true;
    return;
  }
  const sig = signature(j);
  if (sig === batch.dismissed || sig === batch.last?.sig) return;
  const results = j.results ?? [];
  batch.last = { state: now, message: j.message, results, total: j.item?.[1] ?? Math.max(results.length, batch.launched.length), at: wasRunning ? Date.now() : null, sig };
  batch.confirm = false;
  batch.note = "";
  save();
  batch.open = true;
});
