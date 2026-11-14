// Réglages de lecture communs aux deux modes : qualité de l'image de l'aperçu.
// Trois niveaux : « Rapide » (l'aperçu allégé, 540 pixels sur le petit côté), « Nette » (aperçu
// de 1080 pixels, fabriqué à la demande, une fois par vidéo) et « Image exacte » (à l'arrêt
// seulement : l'image en pleine résolution, floutée comme à l'export ou sans flou).
// Le choix est mémorisé dans le navigateur. Il ne concerne que l'aperçu : la vidéo enregistrée
// garde toujours la qualité d'origine.

import { computed, reactive, watch } from "vue";
import type { Clip, Job } from "./api";
import { busy, current, job, notify, project, send, startQuiet, state } from "./store";

/** "" : rapide ; "fine" : nette ; "blur" / "source" : image exacte, floutée ou sans flou. */
export type Quality = "" | "fine" | "blur" | "source";

export const QUALITY_NOTE = "L'aperçu est allégé pour rester fluide. La vidéo enregistrée garde toujours la qualité d'origine.";
export const FINE_NOTE = "L'aperçu net se prépare une seule fois par vidéo, et cela prend un moment. La lecture continue en aperçu rapide en attendant.";

const QUALITY_KEY = "moto-blur:qualite-apercu";

function stored(): { quality: Quality; video: "" | "fine" } {
  try {
    const saved = JSON.parse(localStorage.getItem(QUALITY_KEY) ?? "null");
    if (saved && ["", "fine", "blur", "source"].includes(saved.quality)) {
      return { quality: saved.quality, video: saved.video === "fine" ? "fine" : "" };
    }
  } catch {
    /* stockage indisponible ou valeur illisible : aperçu rapide */
  }
  return { quality: "", video: "" };
}

const initial = stored();
export const playback = reactive({
  /** Choix mémorisé. */
  quality: initial.quality,
  /** Vidéo lue (rapide ou nette) : le dernier choix qui n'est pas « image exacte ». */
  video: initial.video,
  /** Panneau « Réglages de lecture » du lecteur simple : ouvert ? Le clavier lui appartient alors. */
  open: false,
});

function remember(): void {
  try {
    localStorage.setItem(QUALITY_KEY, JSON.stringify({ quality: playback.quality, video: playback.video }));
  } catch {
    /* stockage indisponible : le choix vaut pour cette séance seulement */
  }
}

/** Le traitement en cours (ou terminé) est-il la fabrication d'un aperçu net ? */
function isFineJob(j: Job | null | undefined): boolean {
  return !!j && j.kind === "proxy" && /aperçus? nets?/.test(`${j.label} ${j.message}`);
}
/** Fabrication de l'aperçu net en cours. */
export const preparingFine = computed(() => busy.value && isFineJob(job.value));
export const finePercent = computed(() => (job.value ? Math.min(100, Math.round((100 * job.value.done) / Math.max(1, job.value.total))) : 0));

/** L'aperçu net de ce clip : sans intérêt (source petite), prêt, en préparation, ou à préparer. */
export function fineState(clip: Clip | undefined): "useless" | "ready" | "preparing" | "todo" {
  if (!clip || !clip.fine_useful) return "useless";
  if (clip.proxy_fine) return "ready";
  return preparingFine.value ? "preparing" : "todo";
}
export const fineNow = computed(() => fineState(current.value?.clip));

/** Choix à montrer comme actif. « Nette » ne l'est que si l'aperçu net de la vidéo affichée existe
 *  ou se prépare : pour une vidéo où il reste à préparer, c'est « Rapide » qui est en service. */
export const shownQuality = computed<Quality>(() => {
  if (playback.quality !== "fine") return playback.quality;
  return fineNow.value === "ready" || fineNow.value === "preparing" ? "fine" : "";
});

/** Adresse de la vidéo d'aperçu du clip : nette si elle est demandée et fabriquée, sinon rapide. */
export function mediaUrl(clip: Clip): string {
  return playback.video === "fine" && clip.fine_useful && clip.proxy_fine ? `/media/${clip.id}?q=fine` : `/media/${clip.id}`;
}

/** Choisit la qualité de l'aperçu. « Nette » fait fabriquer l'aperçu net s'il manque. */
export async function setQuality(quality: Quality): Promise<void> {
  if (quality === "fine") {
    const p = project.value;
    const missing = p?.clips.some((c) => c.fine_useful && !c.proxy_fine && !c.missing) ?? false;
    if (missing && !preparingFine.value) {
      if (busy.value) {
        notify("Un traitement est en cours. Choisissez « Nette » quand il sera terminé : l'aperçu net sera préparé à ce moment-là.");
        return;
      }
      if (!(await startQuiet("/api/proxies", { fine: true }))) return; // refusé : le message est affiché
      if (state.mode !== "simple") notify(`${FINE_NOTE} Les modifications attendront la fin (« Annuler », en haut, arrête la préparation).`, "info", true);
    }
  }
  playback.quality = quality;
  if (quality === "" || quality === "fine") playback.video = quality;
  remember();
}

/** Arrête la fabrication de l'aperçu net : la lecture reste en aperçu rapide. */
export async function cancelFine(): Promise<void> {
  if (preparingFine.value) await send("POST", "/api/cancel");
}

// Fin de la fabrication : prête, la lecture bascule seule (le lecteur suit `mediaUrl`) ; arrêtée
// ou en échec, le choix revient à « Rapide ».
watch(() => job.value?.state, (now, before) => {
  const j = job.value;
  if (before !== "running" || !j || now === "running" || !isFineJob(j)) return;
  if (now === "done") {
    notify("L'aperçu net est prêt.");
    return;
  }
  if (playback.quality === "fine") playback.quality = "";
  playback.video = "";
  remember();
  if (now === "error") notify(`L'aperçu net n'a pas pu être préparé : ${j.message}`, "error");
  else notify("Préparation de l'aperçu net arrêtée. La lecture reste en aperçu rapide.");
});
