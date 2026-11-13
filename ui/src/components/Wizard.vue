<script setup lang="ts">
// Mode simple : un assistant plein écran, une question par écran, un seul bouton principal.
// Six étapes : vidéo, quoi flouter (et désignation d'un objet), couper, analyse, vérification,
// enregistrement. L'assistant ne garde presque rien : l'écran affiché à l'ouverture d'un projet
// est déduit de l'état du projet, et tout ce qu'il fait passe par les mêmes appels au moteur
// que le mode avancé. Passer d'un mode à l'autre ne perd donc rien.
// Rien n'est à valider : tout ce que le logiciel trouve est flouté. La vérification tient en une
// relecture (un clic retire un flou en trop, un bouton signale ce qui reste lisible).
// Tout ce qui est facultatif (réglages habituels, type de vidéo, planche des flous, extrait
// d'essai…) se passe d'un clic sur le bouton principal : le chemin court mène toujours à la vidéo.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { call, type ExportPreset, type Job, type Preset, type RenderOptions, type ZoneTuple } from "../api";
import {
  appliedBlurs, exportPresets, followBlur, formatDuration, installDrop, measureEstimate, neverBlurHere, presetInfo,
  removeSimilar, render, renderExtract, restoreRemoved, saveProfile, saveSweep, setPreset, setTracksDisabled, undo,
} from "../extras";
import {
  addMark, currentEntry, currentIndex, droppedInPassage, entries, explain, extra, forceZone, leave, loop, markEntries, next, objectHere,
  previous, relocate, remaining, replay, restoreZone, resume, review, showObject, startQueue, startSweep, validate,
} from "../review";
import {
  busy, current, droppedHere, duration, fpsOf, job, needsAnalysis, notify, player, project, refresh, saveMontage, seekTime, send,
  setMode, start, state, time, trackById, zonesSteady,
} from "../store";
import { clock, dayText, droppedFile, wiz, type Screen } from "../wizard";
import FilePicker from "./FilePicker.vue";
import Icon, { type IconName } from "./Icon.vue";
import Logo from "./Logo.vue";
import Player from "./Player.vue";
import RecentList from "./RecentList.vue";
import StorageDialog from "./StorageDialog.vue";
import TrimBar from "./TrimBar.vue";
import WizProgress from "./WizProgress.vue";
import WizSheet from "./WizSheet.vue";
import WizSpan from "./WizSpan.vue";
import WizTime from "./WizTime.vue";

const steps = ["Vidéo", "Quoi flouter", "Couper", "Analyse", "Vérification", "Enregistrer"];
const stepOf: Record<Screen, number> = { video: 1, profile: 2, what: 2, object: 2, trim: 3, type: 4, analyse: 4, verify: 5, export: 6, done: 7 };
const step = computed(() => stepOf[wiz.screen]);

const picker = ref(false);
const storageOpen = ref(false);
const p = computed(() => project.value);
const running = computed(() => (busy.value ? (job.value?.kind ?? "") : ""));
let alive = true; // l'assistant est à l'écran (faux après un passage en mode avancé)

// --- Mots simples -------------------------------------------------------------
function human(t: number): string {
  // espaces insécables : « 1,5 s » ne se coupe jamais en fin de ligne
  if (t < 9.95) return `${t.toFixed(1).replace(".", ",")}\u00a0s`;
  if (t < 60) return `${Math.round(t)}\u00a0s`;
  const total = Math.round(t);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}\u00a0h\u00a0${String(m).padStart(2, "0")}\u00a0min` : `${m}\u00a0min\u00a0${s}\u00a0s`;
}
function plural(n: number, one: string, many: string): string {
  return `${n} ${n > 1 ? many : one}`;
}
function capital(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}
function list(words: string[]): string {
  return words.length > 1 ? `${words.slice(0, -1).join(", ")} et ${words[words.length - 1]}` : (words[0] ?? "");
}
/** Ponctuation française d'un texte venu d'ailleurs (couche commune, moteur) : espaces insécables. */
function nb(text: string): string {
  return text.replace(/ ([:;?!»])/g, "\u00a0$1").replace(/« /g, "«\u00a0");
}
function sleep(ms: number): Promise<void> {
  return new Promise((done) => window.setTimeout(done, ms));
}

// --- Traitements lancés par l'assistant --------------------------------------------
/** Attend la fin du traitement `kind` en cours ; renvoie son état final (null si l'assistant a été quitté). */
async function waitJob(kind: string): Promise<Job | null> {
  while (alive && busy.value && job.value?.kind === kind) await sleep(120);
  return alive && job.value?.kind === kind ? job.value : null;
}
/** Lance un traitement et attend sa fin. Renvoie null s'il n'a pas démarré (le message est déjà affiché). */
async function runJob(kind: string, launch: () => Promise<void>): Promise<Job | null> {
  const before = state.data;
  await launch();
  if (state.data === before || job.value?.kind !== kind) return null;
  return waitJob(kind);
}

// --- Position de l'assistant ----------------------------------------------------
const clips = computed(() => p.value?.clips ?? []);
const lost = computed(() => clips.value.filter((c) => c.missing));
/** La copie légère qui sert à l'aperçu est prête pour toutes les vidéos du projet. */
const previewReady = computed(() => clips.value.every((c) => c.proxy || c.missing));
const previewFailed = computed(() => !previewReady.value && job.value?.kind === "proxy" && (job.value.state === "error" || job.value.state === "cancelled"));
const kinds = computed(() => (p.value?.settings.analysis.kinds as string[] | undefined) ?? []);
/** Objets désignés à la main (compteur, téléphone…), par vidéo. */
const objects = computed(() => {
  const out: { clip: string; name: string }[] = [];
  for (const [clip, tracks] of Object.entries(p.value?.tracks ?? {})) {
    for (const name of new Set(tracks.filter((t) => t.kind === "dash").map((t) => t.name))) out.push({ clip, name });
  }
  return out;
});
const hasTracks = computed(() => Object.values(p.value?.tracks ?? {}).some((l) => l.length > 0));
/** Réglages habituels enregistrés sur cet ordinateur (profil « ma moto »), s'il y en a. */
const profile = computed(() => state.data.profile ?? null);
/** Vidéo toute neuve (rien d'analysé, aucun objet désigné) et réglages habituels enregistrés : on propose de les reprendre. */
const profileOffered = computed(() => !!profile.value && !!p.value && !hasTracks.value && needsAnalysis.value && wiz.profileAsked !== p.value.path);
function firstQuestion(): Screen {
  return profileOffered.value ? "profile" : "what";
}

/** Écran où reprendre, déduit de l'état du projet. */
function derive(): Screen {
  if (!p.value) return "video";
  if (running.value === "analyze") return "analyse";
  if (running.value === "render") return "export";
  if (running.value === "profile") return "profile";
  if (needsAnalysis.value) return hasTracks.value ? "analyse" : firstQuestion();
  if (!kinds.value.length && !hasTracks.value) return firstQuestion(); // rien de choisi encore
  // vidéo pas encore regardée, endroits signalés ou alertes en attente : la vérification
  return !remaining.value.swept || remaining.value.marks || remaining.value.risks ? "verify" : "export";
}

/** Cale l'assistant sur le projet ouvert : nouveau projet, ou état devenu incompatible avec l'écran. */
function sync(): void {
  if (!p.value) {
    wiz.path = "";
    if (wiz.screen !== "video") show("video");
    return;
  }
  if (!previewReady.value) return; // l'écran d'attente s'affiche ; on se cale quand l'aperçu est prêt
  if (wiz.path !== p.value.path) {
    wiz.path = p.value.path;
    wiz.launched = false;
    wiz.output = "";
    wiz.extract = "";
    wiz.format = "source";
    const target = derive();
    show(target);
    if (target !== "what" && target !== "profile") notify("Ce projet existait déjà : vous reprenez là où vous en étiez.");
  } else if ((wiz.screen === "verify" || wiz.screen === "export") && needsAnalysis.value && !busy.value) {
    show("analyse"); // le montage a changé (en mode avancé) : il faut analyser avant de vérifier
  } else enter();
}

function show(screen: Screen): void {
  quit();
  if (state.toastKind === "info") state.toast = ""; // un message d'information ne suit pas d'un écran à l'autre
  wiz.screen = screen;
  enter();
}

/** Quitte l'écran affiché : lecture arrêtée, outils rangés, vérification guidée fermée. */
function quit(): void {
  stopSweep(); // la relecture en cours retient où elle s'arrête ; un clic sur la vidéo ne retire plus rien
  player.pause();
  state.tool = "none";
  state.pendingDash = null;
  state.loopRange = null;
  state.showDropped = false;
  if (review.mode !== "home") leave();
  if (review.speed !== 1) review.speed = 1; // le 2× ne sert qu'à la relecture, le ralenti qu'aux passages
  window.clearTimeout(advanceTimer);
  finished.value = false;
  revealError.value = "";
  queued.value = false;
  added.value = null;
  state.focus = null;
}

function enter(): void {
  state.preview = "blur";
  switch (wiz.screen) {
    case "profile": return initProfile();
    case "what": return initWhat();
    case "object": return objects.value.length ? showObjects() : startDraw();
    case "trim": return initTrim();
    case "type": return initType();
    case "analyse": return void (measureFailed.value = false);
    case "verify": return initVerify();
    case "export": return initExport();
  }
}

// pas de retour pendant un traitement (ni pendant l'instant où le suivi démarre)
const canBack = computed(() => wiz.screen !== "video" && !busy.value && !(wiz.screen === "object" && osub.value === "tracking") && !following.value);
function afterTrim(): Screen {
  if (kinds.value.length) return "type"; // visages ou plaques à chercher : quel type de vidéo ?
  return needsAnalysis.value ? "analyse" : "verify";
}
function back(): void {
  if (!canBack.value) return;
  switch (wiz.screen) {
    case "profile": return show("video");
    case "what": return show("video");
    case "object":
      if (osub.value === "name") return startDraw();
      if (osub.value === "draw" && objects.value.length) return showObjects();
      return show("what");
    case "trim": return show(objects.value.length ? "object" : "what");
    case "type": return show("trim");
    case "analyse": return show(kinds.value.length ? "type" : "trim");
    case "verify":
      if (inQueue.value && review.sub !== "ask") return resume(); // abandon de la correction en cours
      if (inQueue.value && currentIndex.value > 0) return previous();
      if (vphase.value !== "intro") { // planche, relecture, premier endroit signalé, première alerte : retour à l'entrée de l'étape
        stopSweep();
        if (review.mode !== "home") leave();
        state.loopRange = null;
        vphase.value = "intro";
        return;
      }
      return show(needsAnalysis.value ? "analyse" : "trim");
    case "export": return show("verify");
    case "done": return show("export");
  }
}

function toAdvanced(): void {
  quit();
  state.step = 0;
  setMode("advanced");
}

// --- Annuler la dernière action ---------------------------------------------------------
// Le bouton de l'en-tête et Ctrl+Z (branché dans l'application) passent par le même appel ;
// l'écran affiché se recale ensuite sur ce que l'annulation a remis.
function reconcile(): void {
  if (!p.value || busy.value) return;
  if ((wiz.screen === "verify" || wiz.screen === "export" || wiz.screen === "done") && needsAnalysis.value) return show("analyse");
  if (wiz.screen === "trim") return initTrim();
  if (wiz.screen === "what") return initWhat();
  if (wiz.screen === "object" && osub.value === "result") {
    if (fresh.value && !objects.value.some((o) => o.name === fresh.value?.name && o.clip === fresh.value?.clip)) fresh.value = null;
    if (!objects.value.length) startDraw();
  }
  if (wiz.screen === "verify") {
    // un retrait d'un clic annulé : le bandeau « Flou retiré » n'a plus lieu d'être
    const last = state.lastRemoved;
    if (last && !last.ids.every((id) => trackById(last.clip, id)?.disabled)) state.lastRemoved = null;
    if (inQueue.value) {
      review.removed = review.removed.filter((r) => trackById(r.clip, r.id)?.disabled); // un élément disparu se recale seul
    }
  }
}
watch(() => state.data, (data) => {
  if (data.undone || data.redone) void nextTick(reconcile);
});

// --- 1. Vidéo ---------------------------------------------------------------------
async function picked(path: string): Promise<void> {
  picker.value = false;
  if (!(await send("POST", "/api/open", { path }))) return;
  wiz.path = ""; // même vidéo rechoisie : on reprend aussi à l'écran déduit
  await refresh(); // lance la préparation de l'aperçu si elle manque
  sync();
}
function retryPreview(): void {
  void start("/api/proxies");
}

// Glisser-déposer : l'application installée donne les chemins des fichiers déposés sur la fenêtre.
const dropAsk = ref(""); // fichier déposé alors qu'une vidéo est en cours : on demande avant d'ouvrir
function dropped(paths: string[]): void {
  const file = droppedFile(paths);
  if (!file) return notify("Déposez une vidéo (mp4, mov…) pour l'ouvrir.");
  if (busy.value) return notify("Un traitement est en cours : attendez sa fin pour ouvrir une autre vidéo.");
  picker.value = false;
  storageOpen.value = false;
  if (!p.value || wiz.screen === "video" || wiz.screen === "done") void picked(file);
  else dropAsk.value = file;
}
function confirmDrop(): void {
  const file = dropAsk.value;
  dropAsk.value = "";
  if (file) void picked(file);
}
function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}
/** Sans assistant à l'écran (mode avancé), un fichier déposé s'ouvre simplement. */
async function openDropped(paths: string[]): Promise<void> {
  const file = droppedFile(paths);
  if (file && (await send("POST", "/api/open", { path: file }))) await refresh();
}
let dropTarget: ((paths: string[]) => void) | null = null;
function listenDrop(): void {
  dropTarget = dropped;
  const handler = (paths: string[]): void => (dropTarget ? dropTarget(paths) : void openDropped(paths));
  if (!wiz.dropReady) {
    wiz.dropReady = true;
    installDrop(handler); // une seule fois : pose aussi les écouteurs de la fenêtre
  } else (window as unknown as { motoBlurDrop: (paths: string[]) => void }).motoBlurDrop = handler; // repris après un passage en mode avancé
}

// --- 1 bis. Reprendre vos réglages habituels ? -------------------------------------------
const psub = ref<"ask" | "running" | "result">("ask");
const profileNotes = ref<{ text: string; ok: boolean }[]>([]);
const profileError = ref("");
const profileText = computed(() => {
  const saved = profile.value;
  if (!saved) return "";
  const words: string[] = saved.kinds.map((k) => (k === "face" ? "les visages" : "les plaques"));
  words.push(...saved.objects.map((o) => `« ${capital(o)} »`));
  return list(words);
});
function initProfile(): void {
  if (running.value === "profile") void followProfile();
  else if (psub.value !== "result" || !profileNotes.value.length) psub.value = "ask";
}
/** Phrases du moteur, une par objet du profil ; la mesure technique de ressemblance est retirée. */
function profileSentences(message: string): { text: string; ok: boolean }[] {
  return message
    .replace(/\s*\(ressemblance [\d.,]+\)/g, "")
    .split(/(?=«\s*[^»]+»\s*:)/)
    .map((s) => s.trim())
    .filter((s) => s && s !== "Réglages du profil appliqués.")
    .map((text) => ({ text: nb(text.replace(/^Réglages du profil appliqués\.\s*/, "")), ok: !/non retrouvé|introuvable/.test(text) }));
}
async function followProfile(launch?: () => Promise<void>): Promise<void> {
  psub.value = "running";
  profileError.value = "";
  const path = p.value?.path ?? "";
  const done = launch ? await runJob("profile", launch) : await waitJob("profile");
  if (!alive || wiz.screen !== "profile" || p.value?.path !== path) return;
  if (!done) {
    psub.value = "ask"; // n'a pas démarré : le message est affiché, on peut réessayer ou passer
    return;
  }
  wiz.profileAsked = path;
  if (done.state === "cancelled") return show("what");
  if (done.state === "error") profileError.value = done.message;
  profileNotes.value = done.state === "done" ? profileSentences(done.message) : [];
  psub.value = "result";
}
/** Applique les réglages habituels (traitement « profile »). La demande part sans contenu : le
 *  moteur ne lit pas celui de cette route, et un contenu non lu brouille la requête suivante. */
async function applyProfile(): Promise<void> {
  if (await send("POST", "/api/profile/apply")) void refresh();
}
function skipProfile(): void {
  wiz.profileAsked = p.value?.path ?? "";
  show("what");
}

// --- 2. Que faut-il flouter ? -------------------------------------------------------
const choice = reactive({ face: true, plate: true, object: false });
function initWhat(): void {
  choice.face = kinds.value.includes("face");
  choice.plate = kinds.value.includes("plate");
  choice.object = objects.value.length > 0 || wiz.objectWanted;
  if (!hasTracks.value && !kinds.value.length && !wiz.objectWanted) choice.face = choice.plate = true; // projet tout neuf
}
const wantedKinds = computed(() => ["face", "plate"].filter((k) => (k === "face" ? choice.face : choice.plate)));
const kindsChanged = computed(() => wantedKinds.value.join() !== kinds.value.join());
/** Le projet a déjà été analysé : changer le choix refait l'analyse. */
const analysedOnce = computed(() => Object.values(p.value?.tracks ?? {}).some((l) => l.some((t) => t.source === "auto")) || (!needsAnalysis.value && kinds.value.length > 0));
const whatNote = computed(() => {
  if (!choice.face && !choice.plate && !choice.object) return "Cochez au moins une case pour continuer.";
  const notes: string[] = [];
  if (kindsChanged.value && analysedOnce.value) {
    notes.push(wantedKinds.value.length ? "Vous avez changé ce choix : l'analyse sera refaite." : "Les visages et les plaques déjà trouvés ne seront plus floutés.");
  }
  if (!choice.object && objects.value.length) notes.push(`${objects.value.length > 1 ? "Les objets déjà désignés" : "L'objet déjà désigné"} (${list(objects.value.map((o) => capital(o.name)))}) ne ${objects.value.length > 1 ? "seront" : "sera"} plus flouté${objects.value.length > 1 ? "s" : ""}.`);
  return notes.join(" ");
});
const whatValid = computed(() => choice.face || choice.plate || choice.object);
async function saveWhat(): Promise<void> {
  if (!whatValid.value) return;
  if (kindsChanged.value && !(await send("PUT", "/api/settings", { analysis: { kinds: wantedKinds.value } }))) return;
  if (!choice.object) {
    for (const o of [...objects.value]) await send("POST", "/api/dash", { clip: o.clip, mode: "clear", label: o.name });
  }
  wiz.objectWanted = choice.object;
  show(choice.object ? "object" : "trim");
}

// --- 2 bis. Montrez-moi l'objet -------------------------------------------------------
const quickNames = ["compteur", "téléphone", "GPS"];
const osub = ref<"draw" | "name" | "tracking" | "failed" | "result">("draw");
const objectName = ref(""); // nom choisi par une pastille
const customName = ref(""); // nom saisi à la main : il l'emporte sur la pastille
const fresh = ref<{ clip: string; name: string; at: number } | null>(null); // objet qui vient d'être désigné
const trackError = ref("");
const chosenName = computed(() => {
  const typed = customName.value.trim() || objectName.value;
  return objects.value.find((o) => o.name.toLowerCase() === typed.toLowerCase())?.name ?? typed;
});
const replaces = computed(() => !!chosenName.value && objects.value.some((o) => o.name === chosenName.value && o.clip === state.pendingDash?.clip));

function startDraw(): void {
  player.pause();
  state.loopRange = null;
  state.pendingDash = null;
  fresh.value = null;
  osub.value = "draw";
  state.tool = "dash";
}
function showObjects(): void {
  state.tool = "none";
  state.pendingDash = null;
  osub.value = "result";
}
// le rectangle est tracé : on demande le nom (désignation d'un objet), ou le flou part suivre ce
// qui est entouré (« Il manque un flou ») ; un tracé trop petit laisse l'outil en main
watch(() => state.pendingDash, (drawn) => {
  if (!drawn) return;
  if (wiz.screen === "verify" && inQueue.value && review.sub === "missing") return void follow(drawn);
  if (wiz.screen !== "object") return;
  objectName.value = objects.value.length ? "" : "compteur";
  customName.value = "";
  osub.value = "name";
});
watch(() => state.tool, (tool) => {
  if (wiz.screen === "object" && osub.value === "draw" && tool === "none" && !state.pendingDash) state.tool = "dash";
  // « Il manque un flou » : après un tracé trop petit, le lecteur rend l'outil du flou immobile ; ici le flou suit
  if (wiz.screen === "verify" && review.sub === "missing" && tool === "manual") state.tool = "dash";
});

async function designate(mode: "track" | "fixed"): Promise<void> {
  const drawn = state.pendingDash;
  const name = chosenName.value;
  if (!drawn || !name) return;
  fresh.value = { clip: drawn.clip, name, at: time.value };
  state.pendingDash = null;
  if (mode === "fixed") {
    if (await send("POST", "/api/dash", { ...drawn, mode, label: name })) playResult();
    else startDraw();
    return;
  }
  osub.value = "tracking";
  await start("/api/dash", { ...drawn, mode, label: name, direction: "both" });
  if (!busy.value && job.value?.kind !== "dash") startDraw(); // le suivi n'a pas pu démarrer (message affiché)
}
/** Quelques secondes en boucle, flou affiché, à partir de l'image où l'objet a été entouré. */
function playResult(): void {
  osub.value = "result";
  const at = fresh.value?.at ?? time.value;
  const from = Math.max(0, Math.min(at, duration.value - 4));
  state.loopRange = { from, to: Math.min(duration.value, from + 4) };
  seekTime(from);
  void player.play();
}
/** Déplacement du curseur sous la vidéo : la lecture (et sa boucle) s'arrête sur l'image choisie. */
function scrub(value: number): void {
  player.pause();
  state.loopRange = null;
  seekTime(value);
}
async function removeObject(o: { clip: string; name: string }): Promise<void> {
  await send("POST", "/api/dash", { clip: o.clip, mode: "clear", label: o.name });
}
async function redo(): Promise<void> {
  if (fresh.value) await removeObject(fresh.value);
  startDraw();
}

// --- 3. Couper le début ou la fin -------------------------------------------------------
const trim = reactive({ a: 0, b: 0 });
const trimClip = computed(() => clips.value[0]);
/** Montage fait en mode avancé : plusieurs passages, plusieurs vidéos ou des passages sans son. */
const advancedMontage = computed(() => !!p.value && (p.value.clips.length > 1 || p.value.timeline.length > 1 || p.value.mutes.length > 0));
const montageSummary = computed(() => {
  if (!p.value) return "";
  const parts = [plural(p.value.timeline.length, "passage gardé", "passages gardés")];
  if (p.value.clips.length > 1) parts.push(`${p.value.clips.length} vidéos`);
  if (p.value.mutes.length) parts.push(plural(p.value.mutes.length, "passage sans son", "passages sans son"));
  return `${parts.join(", ")}. Durée totale : ${human(duration.value)}.`;
});
const trimmed = computed(() => !!trimClip.value && (trim.a > 0.001 || trim.b < trimClip.value.duration - 0.001));
function round6(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}
function initTrim(): void {
  const clip = trimClip.value;
  if (!clip || advancedMontage.value) return;
  const seg = p.value?.timeline[0];
  trim.a = seg ? seg.start : 0;
  trim.b = seg ? seg.end : clip.duration;
  if (!seg) void saveTrim(); // montage vide : la vidéo entière, pour que l'aperçu ait quelque chose à montrer
  seekTime(0);
}
/** Pendant qu'une poignée bouge, l'aperçu montre l'image où elle se trouve. */
function previewTrim(edge: "start" | "end"): void {
  const clip = trimClip.value;
  if (!clip) return;
  player.pause();
  const fps = fpsOf(clip);
  state.seg = 0;
  state.frame = Math.min(clip.frames - 1, Math.max(0, edge === "start" ? Math.round(trim.a * fps) : Math.round(trim.b * fps) - 1));
}
async function saveTrim(edge?: "start" | "end"): Promise<boolean> {
  const clip = trimClip.value;
  if (!clip) return false;
  const seg = p.value?.timeline[0];
  const wanted = { clip: clip.id, start: round6(trim.a), end: round6(trim.b) };
  if (!seg || Math.abs(seg.start - wanted.start) > 1e-6 || Math.abs(seg.end - wanted.end) > 1e-6) {
    await saveMontage([wanted], []);
    const saved = p.value?.timeline[0];
    if (!saved || Math.abs(saved.start - wanted.start) > 1e-3 || Math.abs(saved.end - wanted.end) > 1e-3) return false;
  }
  if (edge) seekTime(edge === "start" ? 0 : duration.value);
  return true;
}
function playExtract(): void {
  if (state.playing) return player.pause();
  seekTime(0);
  void player.play();
}
async function keepAll(): Promise<void> {
  const clip = trimClip.value;
  if (!clip) return;
  trim.a = 0;
  trim.b = clip.duration;
  if (await saveTrim()) show(afterTrim());
}
async function keepPart(): Promise<void> {
  if (await saveTrim()) show(afterTrim());
}

// --- 4. Type de vidéo, puis analyse ----------------------------------------------------------
const presetIds: Preset[] = ["normal", "difficile", "rapide"];
const presetIcons: Record<Preset, IconName> = { normal: "film", difficile: "eye", rapide: "gauge", perso: "sliders" };
const typeChoice = ref<Preset>("normal");
function initType(): void {
  typeChoice.value = p.value?.preset ?? "normal";
}
const typeChanged = computed(() => !!p.value && typeChoice.value !== p.value.preset);
const typeNote = computed(() => {
  const notes: string[] = [];
  if (typeChoice.value === "perso") notes.push("Vos réglages ont été modifiés en mode avancé\u00a0: ils sont gardés tant que vous ne choisissez pas un type ici.");
  if (presetInfo[typeChoice.value].warning) notes.push(nb(presetInfo[typeChoice.value].warning));
  if (typeChanged.value && analysedOnce.value) notes.push("Vous avez changé ce choix : l'analyse sera refaite.");
  return notes.join(" ");
});
async function saveType(): Promise<void> {
  if (typeChanged.value) {
    await setPreset(typeChoice.value);
    if (p.value?.preset !== typeChoice.value) return; // refusé : le message est affiché
  }
  show(needsAnalysis.value ? "analyse" : "verify");
}

const finished = ref(false); // l'analyse vient de se terminer : résultat montré un instant
const queued = ref(false); // « Lancer l'analyse » demandé pendant la mesure de durée : elle part dès la mesure finie
let advanceTimer = 0;
const analyseView = computed(() => {
  const j = job.value;
  if (j?.kind === "analyze" && j.state === "running") return "running";
  if (queued.value) return "running"; // la mesure de durée se termine, l'analyse part aussitôt après
  if (!needsAnalysis.value) return "done";
  if (j?.kind === "analyze" && j.state === "error") return "error";
  if (j?.kind === "analyze" && wiz.launched && j.state !== "running") return "stopped";
  return "idle";
});
const looksFor = computed(() => list(kinds.value.map((k) => (k === "face" ? "les visages" : "les plaques"))));
const found = computed(() => {
  const count = { face: 0, plate: 0 };
  for (const tracks of Object.values(p.value?.tracks ?? {})) {
    for (const t of tracks) if (t.source === "auto" && !t.disabled && !t.below_min_score && (t.kind === "face" || t.kind === "plate")) count[t.kind]++;
  }
  const parts = [];
  if (kinds.value.includes("face")) parts.push(plural(count.face, "visage", "visages"));
  if (kinds.value.includes("plate")) parts.push(plural(count.plate, "plaque", "plaques"));
  return parts.length ? `Le logiciel a trouvé ${list(parts)}.` : "";
});
/** Durée annoncée : mesurée sur quelques images de cette vidéo, sur cet ordinateur. */
const measuring = computed(() => running.value === "estimate");
const estimateText = computed(() => {
  const e = p.value?.estimate;
  return e && e.frames > 0 && e.seconds > 0 ? formatDuration(e.seconds) : "";
});
const measuringText = computed(() => (job.value?.label.startsWith("Téléchargement") ? "Téléchargement des modèles… (une seule fois)" : "Mesure de la durée sur quelques images de votre vidéo…"));
const measureFailed = ref(false);
/** La mesure n'a de sens que s'il reste des visages ou des plaques à chercher, et tant qu'elle n'est pas faite. */
const canMeasure = computed(() => kinds.value.length > 0 && needsAnalysis.value && !estimateText.value);
/** « Combien de temps ça va prendre ? » : mesure sur quelques images. Rien n'en dépend : si elle
 *  échoue, on le dit et l'analyse se lance comme avant. Rien ne se passe sans ce clic. */
async function measure(): Promise<void> {
  if (busy.value || !canMeasure.value) return;
  measureFailed.value = false;
  const done = await runJob("estimate", measureEstimate);
  if (!alive || wiz.screen !== "analyse") return;
  if (!queued.value) {
    measureFailed.value = !estimateText.value;
    return;
  }
  // la progression reste affichée jusqu'à ce que l'analyse ait pris le relais : pas d'écran intermédiaire
  if (done?.state !== "cancelled" && !busy.value) await start("/api/analyze");
  queued.value = false;
}
function analyse(): void {
  wiz.launched = true;
  if (measuring.value) queued.value = true; // la mesure se termine (quelques secondes), puis l'analyse part
  else void start("/api/analyze");
}

// --- 5. Vérification --------------------------------------------------------------------
// Plus rien à valider : tout ce que le logiciel a trouvé est flouté, sans question. L'étape tient
// en une relecture (un clic sur un flou en trop le retire, un bouton signale ce qui reste
// lisible), puis les endroits signalés un par un, puis les alertes s'il y en a : les passages où
// quelque chose peut rester visible. La planche des flous qui se voient le plus est facultative.
type Phase = "intro" | "sheet" | "sweep" | "marks" | "risks";
const vphase = ref<Phase>("intro");
const entry = currentEntry;
/** Endroits signalés ou alertes, passés un par un (file commune : `doubtEntries` ne contient que les alertes). */
const inQueue = computed(() => vphase.value === "marks" || vphase.value === "risks");
/** Flous posés par le logiciel : s'il y en a, la planche facultative est proposée. */
const appliedCount = computed(() => Object.values(p.value?.tracks ?? {}).reduce((n, l) => n + appliedBlurs(l).length, 0));
/** Pourquoi le logiciel alerte ici : deux phrases au plus, sans jargon. */
const reasons = computed(() => {
  const item = entry.value?.item;
  if (!item) return [];
  // une raison sans traduction resterait du jargon : phrase générale à la place
  const texts = explain(item.reasons).map((r) => (r.text === r.raw ? "Quelque chose peut rester visible ici." : nb(r.text)));
  return [...new Set(texts)].slice(0, 2);
});
function initVerify(): void {
  vphase.value = "intro";
}

// --- Relecture : à 2× par défaut ; un clic retire un flou, un bouton signale ce qui manque ------
// Elle retient où elle s'arrête, et si elle est allée jusqu'au bout. En mode simple, la couche
// commune laisse à l'assistant le réglage du clic-retrait et de la vitesse : il les remet en quittant.
const sweepAt = computed(() => p.value?.sweep.position ?? 0);
const sweepDone = computed(() => p.value?.sweep.done ?? false);
const canResume = computed(() => !sweepDone.value && sweepAt.value > 1 && sweepAt.value < duration.value - 1);
const sweepEnded = computed(() => !state.playing && time.value >= duration.value - 0.2);
const fast = ref(true); // lecture à 2× ; le choix vaut pour toute la séance
let sweepTimer = 0;
let removedTimer = 0; // bandeau « Flou retiré »
function keepSweep(ended = false): void {
  if (!p.value) return;
  void saveSweep(ended ? duration.value : time.value, sweepDone.value || ended);
}
/** Quitte la relecture : position retenue, vitesse normale, et un clic sur la vidéo ne retire plus rien. */
function stopSweep(): void {
  window.clearInterval(sweepTimer);
  window.clearTimeout(removedTimer);
  if (wiz.screen === "verify" && vphase.value === "sweep" && review.mode === "sweep" && state.playing) keepSweep(); // à l'arrêt, la pause l'a déjà retenue
  state.clickRemoves = false;
  state.lastRemoved = null;
  if (review.speed !== 1) review.speed = 1;
}
async function beginSweep(from: "start" | "resume"): Promise<void> {
  if (review.mode !== "home") leave();
  vphase.value = "sweep";
  review.speed = fast.value ? 2 : 1;
  state.lastRemoved = null;
  state.clickRemoves = true; // le lecteur retire d'un clic le flou touché, sans arrêter la lecture
  await nextTick(); // le lecteur doit être à l'écran avant de lancer la lecture
  if (!alive || wiz.screen !== "verify" || vphase.value !== "sweep") return;
  if (from === "resume") seekTime(sweepAt.value);
  startSweep(from === "start");
  window.clearInterval(sweepTimer);
  sweepTimer = window.setInterval(() => state.playing && keepSweep(), 5000);
}
watch(() => state.playing, (playing) => {
  if (!playing && wiz.screen === "verify" && vphase.value === "sweep" && review.mode === "sweep") keepSweep(time.value >= duration.value - 0.2);
});
function setFast(on: boolean): void {
  fast.value = on;
  review.speed = on ? 2 : 1;
}
/** « Revenir de 5 s » : revoir ce qui vient de passer, sans quitter la relecture. */
function rewind(): void {
  const playing = state.playing;
  player.pause();
  seekTime(Math.max(0, time.value - 5));
  if (playing) void player.play();
}

// Bandeau « Flou retiré » (state.lastRemoved, rempli par le lecteur à chaque clic sur un flou) :
// il s'efface seul après quelques secondes, ou au retrait suivant. Même règles que le bandeau du
// mode avancé (RemovedBar), présenté en colonne à côté de la vidéo, et il compte une tache de
// plusieurs flous réunis pour ce qu'on en voit : un flou.
const REMOVED_MS = 8000;
let removedHeld = false; // pointeur ou clavier sur le bandeau : il reste affiché
const removed = computed(() => (wiz.screen === "verify" && vphase.value === "sweep" ? state.lastRemoved : null));
/** Flous réunis dans la tache cliquée (voir `wholeBlur`) : à l'écran, c'est un seul flou. */
const blobSize = ref(1);
/** Nombre de flous retirés, tels qu'on les voit : la tache cliquée, plus ceux retirés « au même endroit ». */
const removedCount = computed(() => (removed.value ? Math.max(1, wereBlurs(removed.value.clip, removed.value.ids) - blobSize.value + 1) : 0));
/** Flous encore en place au même endroit. */
const similarCount = computed(() => (removed.value ? wereBlurs(removed.value.clip, removed.value.similar) : 0));
/** Parmi ces flous, combien étaient vraiment appliqués : le moteur range aussi « au même endroit »
 *  des objets vus mais non floutés, qu'on n'annonce pas comme des flous. */
function wereBlurs(clip: string, ids: number[]): number {
  return ids.filter((id) => {
    const t = trackById(clip, id);
    return !t || !t.below_min_score || t.forced;
  }).length;
}
/** « Ne plus jamais flouter cet endroit » n'a de sens que si le logiciel s'y trompe plusieurs fois. */
const neverOffered = computed(() => !!removed.value && (removed.value.similar.length > 0 || removedCount.value > 1));
function armRemoved(): void {
  window.clearTimeout(removedTimer);
  if (state.lastRemoved && !removedHeld) removedTimer = window.setTimeout(() => (state.lastRemoved = null), REMOVED_MS);
}
function holdRemoved(on: boolean): void {
  removedHeld = on;
  armRemoved();
}
watch(() => state.lastRemoved, (last) => {
  if (!last) removedHeld = false;
  armRemoved();
  if (last?.ids.length === 1) {
    blobSize.value = 1;
    void wholeBlur(last);
  }
});
// Un flou affiché peut réunir plusieurs flous qui se touchent : à l'écran, une seule tache, que le
// lecteur entoure en entier au survol. Il n'en retire pourtant qu'un par clic ; l'assistant retire
// les autres dans la foulée, pour que « cliquez dessus, il disparaît » soit vrai. Les flous de
// l'image sont relevés à l'appui du bouton de la souris.
let pressed: { clip: string; zones: ZoneTuple[] } | null = null;
function notePress(): void {
  const s = current.value;
  pressed = state.clickRemoves && s ? { clip: s.clip.id, zones: zonesSteady(s.clip.id, state.frame) ?? [] } : null;
}
async function wholeBlur(last: NonNullable<typeof state.lastRemoved>): Promise<void> {
  const zone = pressed?.clip === last.clip ? pressed.zones.find((z) => (z[9] ?? [z[7]]).includes(last.ids[0])) : undefined;
  pressed = null;
  const others = (zone?.[9] ?? []).filter((id) => {
    const t = trackById(last.clip, id);
    return id !== last.ids[0] && !!t && t.source === "auto" && !t.disabled;
  });
  if (!others.length) return;
  await setTracksDisabled(last.clip, others, true);
  if (state.lastRemoved !== last) {
    // « Annuler » cliqué entre-temps : toute la tache revient ; un autre retrait a suivi : rien de plus à dire
    if (!trackById(last.clip, last.ids[0])?.disabled) await setTracksDisabled(last.clip, others, false);
    return;
  }
  if (others.some((id) => !trackById(last.clip, id)?.disabled)) return; // refusé : le message est affiché
  blobSize.value = others.length + 1;
  state.lastRemoved = { ...last, ids: [...last.ids, ...others], similar: last.similar.filter((id) => !others.includes(id)) };
}
/** « Ne plus jamais flouter cet endroit » : c'est dit, avec ce que cela implique et comment revenir en arrière. */
async function never(): Promise<void> {
  await neverBlurHere();
  if (state.lastRemoved || state.toastKind !== "info") return; // refusé : le message d'erreur est affiché
  notify("Cet endroit ne sera plus flouté. Attention : un visage ou une plaque qui s'y tiendrait ne serait pas flouté non plus. Pour revenir en arrière : « Annuler la dernière action », en haut.", "info", true);
}
// Le lecteur refuse de retirer d'un clic un flou venu de l'utilisateur, et le dit avec les mots
// du mode avancé (« la liste des flous ») : ici, la même chose avec ceux de l'assistant.
watch(() => state.toast, (toast) => {
  if (alive && toast.startsWith("Ce flou a été ajouté par vous")) notify("Ce flou vient de vous : un clic ne le retire pas. Pour le supprimer, passez en mode avancé.");
});

// --- Après la relecture : les endroits signalés, puis les alertes ---------------------------
// Les deux passent par la file commune de la vérification (une question, le passage en boucle).
/** Flou ajouté sur l'endroit signalé affiché : le bouton principal devient « C'est réglé ». */
const added = ref<{ entry: string; clip: string; id: number } | null>(null);
const addedHere = computed(() => !!added.value && added.value.entry === review.current && !!trackById(added.value.clip, added.value.id));
/** Suite de la relecture : les signalements un par un, sinon les alertes, sinon l'enregistrement. */
function afterSweep(): void {
  stopSweep();
  if (review.mode !== "home") leave();
  if (remaining.value.marks) void beginQueue("marks");
  else afterMarks();
}
function afterMarks(): void {
  if (remaining.value.risks) void beginQueue("risks");
  else show("export");
}
async function beginQueue(scope: "marks" | "risks"): Promise<void> {
  stopSweep();
  if (review.mode !== "home") leave();
  state.loopRange = null;
  vphase.value = scope;
  added.value = null;
  await nextTick(); // le lecteur doit être à l'écran avant de lancer la boucle
  if (!alive || wiz.screen !== "verify" || vphase.value !== scope) return;
  startQueue(scope === "marks" ? "marks" : "doubt");
}
// Après une correction, l'élément affiché peut avoir disparu (une alerte réglée par un nouveau
// suivi, par exemple). L'endroit corrigé tourne alors en boucle, le temps de voir le résultat
// (« Cet endroit a changé »), puis « Continuer » passe à l'élément le plus proche, ou à la suite.
watch(() => [inQueue.value, review.mode, review.current, review.sub, busy.value, !!currentEntry.value], () => {
  if (wiz.screen !== "verify" || !inQueue.value || review.mode !== "queue" || review.sub !== "ask" || busy.value) return;
  if (currentEntry.value || state.loopRange) return;
  const last = review.last;
  if (!last) return settle();
  state.loopRange = { from: Math.max(0, last.at - 0.5), to: Math.min(duration.value, last.end + 0.5) };
  seekTime(state.loopRange.from);
  void player.play();
});
function settle(): void {
  state.loopRange = null;
  relocate(); // s'il ne reste rien, la file est finie
  if (review.mode === "queue" && currentEntry.value) replay();
}
// la file est épuisée : après les signalements, les alertes ; après les alertes, l'enregistrement
watch(() => review.mode, (mode) => {
  if (wiz.screen !== "verify" || mode !== "end") return;
  const phase = vphase.value;
  leave();
  if (phase === "marks") afterMarks();
  else show("export");
});

// --- Se déplacer dans le passage affiché (endroit signalé, alerte) ---------------------------
const navSpan = computed(() => (wiz.screen === "verify" && inQueue.value && review.mode === "queue" ? loop.value : null));
function scrubPassage(value: number): void {
  player.pause();
  seekTime(value);
}
/** Image précédente ou suivante, sans sortir du passage. */
function stepFrame(frames: number): void {
  const span = navSpan.value;
  const s = current.value;
  if (!span || !s) return;
  player.pause();
  seekTime(Math.min(span.to - 0.5 / s.fps, Math.max(span.from, time.value + frames / s.fps)));
}

// « Il manque un flou » : ce qu'on entoure est suivi par le flou, dans le passage du montage affiché.
const following = ref(false);
function missing(): void {
  player.pause();
  review.sub = "missing";
  state.tool = "dash"; // le tracé revient par `pendingDash` : le flou suit, au lieu de rester immobile
}
async function follow(drawn: { clip: string; time: number; box: number[] }): Promise<void> {
  state.pendingDash = null;
  const s = current.value;
  if (!s || s.clip.id !== drawn.clip) return resume();
  const shown = review.current;
  following.value = true;
  const done = await runJob("follow", () => followBlur(drawn.clip, drawn.time, drawn.box, s.inFrame / s.fps, s.outFrame / s.fps));
  following.value = false;
  if (!alive || wiz.screen !== "verify" || !inQueue.value) return;
  if (done?.state === "done") {
    const made = done.created === null ? undefined : trackById(drawn.clip, done.created);
    const during = made ? ` pendant ${human(made.end - made.start + 1 / s.fps)}` : "";
    if (made) added.value = { entry: shown, clip: drawn.clip, id: made.id };
    notify(`Flou ajouté : il suit ce que vous avez entouré${during}. Regardez la vidéo : s'il ne couvre pas tout, ajoutez-en un autre.`);
  } else if (done?.state === "error") notify(`Le flou n'a pas pu être ajouté : ${done.message}`, "error");
  resume();
}
function toggle(): void {
  if (state.playing) player.pause();
  else void player.play();
}

// --- 6. Enregistrer ----------------------------------------------------------------------
const EXTRACT_S = 10; // durée de l'extrait d'essai
const presetQuality: Record<ExportPreset, number> = { source: 80, youtube: 90, social: 75, leger: 60 }; // qualité de chaque format, comme dans le moteur
const form = reactive({ output: "", quality: 80 });
const options = ref(false);
const confirmed = ref(false);
const revealError = ref("");
const stopped = ref(false);
const extracting = ref(false); // le traitement en cours (ou le dernier) est l'extrait d'essai
function initExport(): void {
  const base = (p.value?.name ?? "video").replace(/\.motoblur\.json$/, "").replace(/\.json$/, "");
  if (!form.output || wiz.exportFor !== p.value?.path) {
    form.output = `${base}_floute.mp4`;
    form.quality = presetQuality[wiz.format];
  }
  wiz.exportFor = p.value?.path ?? "";
  confirmed.value = false;
  stopped.value = false;
}
const blurred = computed(() => {
  const words: string[] = kinds.value.map((k) => (k === "face" ? "les visages" : "les plaques"));
  words.push(...new Set(objects.value.map((o) => `« ${capital(o.name)} »`)));
  const manual = Object.values(p.value?.tracks ?? {}).reduce((n, l) => n + l.filter((t) => t.kind === "manual" && !t.disabled).length, 0);
  if (manual) words.push(plural(manual, "flou ajouté à la main", "flous ajoutés à la main"));
  return list(words) || "rien";
});
const exportView = computed(() => {
  const j = job.value;
  if (j?.kind === "render" && j.state === "running") return "running";
  if (j?.kind === "render" && j.state === "error" && wiz.launchedExport) return "error";
  return "form";
});
const formatText = computed(() => nb(exportPresets.find((f) => f.id === wiz.format)?.text ?? ""));
function pickFormat(id: ExportPreset): void {
  wiz.format = id;
  form.quality = presetQuality[id];
}
function renderOptions(): Partial<RenderOptions> {
  // « Comme l'original » et « YouTube » gardent le format de la vidéo ; les deux autres laissent le moteur choisir
  const sameAsSource = wiz.format === "source" || wiz.format === "youtube";
  return { preset: wiz.format, quality: form.quality, ...(sameAsSource ? { codec: "source" } : {}), encoder: "auto", debug: false };
}
const exportReady = computed(() => !!p.value && !p.value.problem && !needsAnalysis.value && form.output.trim() !== "");
/** Endroits signalés pas encore réglés et alertes pas encore regardées : là, quelque chose peut rester lisible. */
const leftCount = computed(() => remaining.value.marks + remaining.value.risks);
const leftText = computed(() => {
  const { marks, risks } = remaining.value;
  const parts: string[] = [];
  if (marks) parts.push(`Vous avez signalé ${plural(marks, "endroit", "endroits")} sans ${marks > 1 ? "les" : "le"} régler.`);
  if (risks) parts.push(`Le logiciel a peut-être laissé quelque chose visible à ${plural(risks, "endroit", "endroits")}.`);
  return parts.join(" ");
});
const canExport = computed(() => exportReady.value && (leftCount.value === 0 || confirmed.value));
/** L'extrait n'a de sens que si la vidéo est nettement plus longue que lui. */
const canExtract = computed(() => exportReady.value && duration.value >= EXTRACT_S * 2);
function exportVideo(): void {
  if (!canExport.value) return;
  wiz.launchedExport = true;
  stopped.value = false;
  extracting.value = false;
  void render({ output: form.output.trim(), ...renderOptions() });
}
/** Dix secondes prises au milieu de la vidéo : le résultat réel, sans attendre toute la création. */
function exportExtract(): void {
  if (!canExtract.value) return;
  wiz.launchedExport = true;
  stopped.value = false;
  extracting.value = true;
  wiz.extract = "";
  void renderExtract(form.output.trim(), duration.value / 2, EXTRACT_S, renderOptions());
}
/** « La regarder » : directement la relecture, là où elle s'était arrêtée. */
function watchAll(): void {
  show("verify");
  void beginSweep(canResume.value ? "resume" : "start");
}
/** « Regarder ces endroits » : les endroits signalés, puis les alertes. */
function seeLeft(): void {
  show("verify");
  if (remaining.value.marks) void beginQueue("marks");
  else if (remaining.value.risks) void beginQueue("risks");
}
async function reveal(path: string = wiz.output): Promise<void> {
  revealError.value = "";
  try {
    await call("POST", "/api/reveal", { path });
  } catch (error) {
    revealError.value = (error as Error).message;
  }
}
function another(): void {
  show("video");
  picker.value = true;
}

// Fin de l'assistant : retenir ces réglages pour les prochaines vidéos (profil « ma moto »).
const savingProfile = ref(false);
const profileKept = computed(() => !!p.value && wiz.profileSaved === p.value.path);
async function keepProfile(): Promise<void> {
  if (!p.value || savingProfile.value) return;
  savingProfile.value = true;
  const before = profile.value?.saved_at;
  const path = p.value.path;
  await saveProfile(); // affiche lui-même la confirmation, ou l'erreur
  if (profile.value && (profile.value.saved_at !== before || before === undefined)) wiz.profileSaved = path;
  savingProfile.value = false;
}

// --- Fin des traitements ---------------------------------------------------------------
watch(() => job.value?.state, (now, before) => {
  const j = job.value;
  if (!j || before !== "running" || now === "running") return;
  if (j.kind === "analyze" && wiz.screen === "analyse" && now === "done" && !needsAnalysis.value) {
    finished.value = true; // résultat montré un instant, puis la vérification
    window.clearTimeout(advanceTimer);
    advanceTimer = window.setTimeout(() => wiz.screen === "analyse" && show("verify"), 2600);
  } else if (j.kind === "render" && wiz.screen === "export") {
    const path = now === "done" ? j.message.match(/^Vidéo écrite : (.+)$/s)?.[1] : "";
    const extract = now === "done" ? j.message.match(/^Extrait écrit : (.+)$/s)?.[1] : "";
    if (path) {
      wiz.output = path;
      show("done");
    } else if (extract) wiz.extract = extract;
    else if (now === "cancelled") stopped.value = true;
  } else if (j.kind === "dash" && wiz.screen === "object" && osub.value === "tracking") {
    if (now === "done") playResult();
    else if (now === "error") {
      trackError.value = j.message;
      osub.value = "failed";
    } else startDraw();
  }
});

// --- Clavier : Entrée = bouton principal, Échap = retour, S = signaler ----------------------
const root = ref<HTMLDivElement>();
const progress = ref<InstanceType<typeof WizProgress>>();
function onKey(event: KeyboardEvent): void {
  if (picker.value || storageOpen.value) return; // le sélecteur de fichiers et le dialogue ont leurs propres touches
  if (dropAsk.value) {
    if (event.key !== "Escape" && event.key !== "Enter") return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Enter") confirmDrop();
    else dropAsk.value = "";
    return;
  }
  const target = event.target as HTMLElement;
  const tag = target.tagName;
  const typing = tag === "TEXTAREA" || tag === "SELECT" || (tag === "INPUT" && (target as HTMLInputElement).type === "text");
  if (event.key === "Escape") {
    if (progress.value?.asking) {
      progress.value.asking = false;
      event.stopPropagation();
      return;
    }
    if (following.value) {
      event.stopPropagation(); // le suivi du flou ajouté va à son terme (ou s'arrête par « Annuler »)
      return;
    }
    if (wiz.screen === "verify" && review.sub !== "ask") return; // le lecteur abandonne la correction en cours
    event.stopPropagation();
    event.preventDefault();
    back();
  } else if (event.key === "Enter") {
    if (tag === "BUTTON" || tag === "A" || tag === "TEXTAREA" || tag === "SELECT") return; // le bouton qui a le focus agit lui-même
    const main = root.value?.querySelector<HTMLButtonElement>("[data-primary]:not(:disabled)");
    if (!main) return;
    event.preventDefault();
    event.stopPropagation();
    main.click();
  } else if ((event.key === "s" || event.key === "S") && !typing && review.mode === "sweep" && !sweepEnded.value && !event.ctrlKey && !event.metaKey) {
    void addMark(); // « Il manque un flou ici » : la lecture continue
  } else if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA" && navSpan.value && !event.ctrlKey && !event.metaKey && !event.altKey) {
    // dans le passage vérifié : image par image (Maj : une seconde), sans en sortir ; le lecteur ne s'en occupe pas
    event.preventDefault();
    event.stopPropagation();
    const fps = current.value?.fps ?? 30;
    stepFrame((event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? Math.round(fps) : 1));
  }
}
// Après un clic à la souris, le bouton ne garde pas le focus : Entrée déclenche toujours le bouton
// principal de l'écran affiché, pas le dernier bouton cliqué. Au clavier, le focus reste où il est.
function releaseFocus(event: MouseEvent): void {
  if (event.detail > 0 && document.activeElement instanceof HTMLButtonElement) document.activeElement.blur();
}

const withVideo = computed(() => {
  if (!p.value || !previewReady.value) return false;
  if (wiz.screen === "object") return true;
  if (wiz.screen === "trim") return !advancedMontage.value;
  return wiz.screen === "verify" && (vphase.value === "sweep" || inQueue.value);
});
/** La planche des flous occupe toute la largeur. */
const sheetShown = computed(() => state.loaded && !!p.value && !lost.value.length && previewReady.value && wiz.screen === "verify" && vphase.value === "sheet");

// --- Mise en page des écrans avec la vidéo -------------------------------------------------
// La vidéo prend toute la hauteur disponible (vidéo verticale comprise), ses commandes juste
// dessous, les questions à côté ; l'ensemble est centré. Recalculé au redimensionnement seulement.
const MIN_STAGE = 440; // largeur minimale sous la vidéo : les commandes y tiennent sur deux lignes
const STAGE_GAP = 12; // espace entre la vidéo et ses commandes (.wiz-stage)
const mainEl = ref<HTMLElement>();
const underEl = ref<HTMLElement>();
const panelEl = ref<HTMLElement>();
const fitBox = ref<{ stage: number; height: number } | null>(null);
function layout(): void {
  const main = mainEl.value;
  const under = underEl.value;
  const panel = panelEl.value;
  const clip = current.value?.clip ?? trimClip.value;
  if (!withVideo.value || !main || !under || !panel || !clip?.width || !clip.height) {
    fitBox.value = null;
    return;
  }
  const css = getComputedStyle(main);
  const width = main.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight) - panel.offsetWidth - (parseFloat(css.columnGap) || 0);
  const height = main.clientHeight - parseFloat(css.paddingTop) - parseFloat(css.paddingBottom) - under.offsetHeight - STAGE_GAP;
  if (width <= 0 || height <= 0) return;
  const ratio = clip.width / clip.height;
  const video = Math.max(1, Math.min(width, height * ratio)); // largeur de l'image
  const next = { stage: Math.floor(Math.min(width, Math.max(video, MIN_STAGE))), height: Math.floor(video / ratio) };
  const was = fitBox.value;
  if (!was || Math.abs(was.stage - next.stage) > 1 || Math.abs(was.height - next.height) > 1) fitBox.value = next;
}
const stageStyle = computed(() => (fitBox.value ? { flex: "none", width: `${fitBox.value.stage}px` } : undefined));
const playerStyle = computed(() => (fitBox.value ? { flex: "none", height: `${fitBox.value.height}px` } : undefined));
const resizer = new ResizeObserver(() => layout());
watch([mainEl, underEl, panelEl], (now, before) => {
  for (const el of before ?? []) if (el) resizer.unobserve(el);
  for (const el of now) if (el) resizer.observe(el);
  void nextTick(layout);
});
watch([withVideo, () => current.value?.clip.width, () => current.value?.clip.height, () => wiz.screen, vphase], () => void nextTick(layout));
const savedPreview = state.preview;
watch([() => p.value?.path, previewReady], sync);
onMounted(() => {
  window.addEventListener("keydown", onKey, true);
  listenDrop();
  sync();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey, true);
  resizer.disconnect();
  quit();
  alive = false;
  dropTarget = null;
  state.preview = savedPreview === "blur" ? "boxes" : savedPreview;
});
</script>

<template>
  <!-- data-phase : où en est l'étape Vérification, lisible par les essais automatiques -->
  <div ref="root" class="wiz" :data-phase="wiz.screen === 'verify' ? vphase : undefined" @click="releaseFocus">
    <header class="wiz-top">
      <span class="brand"><Logo /><strong>moto-blur</strong></span>
      <ol class="wsteps" aria-label="Étapes">
        <li v-for="(label, i) in steps" :key="label" class="wstep" :class="{ current: step === i + 1, done: step > i + 1 }" :aria-current="step === i + 1 ? 'step' : undefined">
          <span class="step-num"><Icon v-if="step > i + 1" name="check" :size="12" /><template v-else>{{ i + 1 }}</template></span>
          <span class="wstep-label">{{ label }}</span>
        </li>
      </ol>
      <button
        v-if="p?.history.undo" class="mode-switch wiz-undo" data-undo :disabled="busy" :title="`Annuler : ${p.history.undo}`"
        :aria-label="`Annuler la dernière action : ${p.history.undo}`" @click="undo()"
      >
        <svg class="icon" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 3.25 2.75 6.5 6 9.75M3 6.5h6.25a3.5 3.5 0 0 1 0 7H7" /></svg>
        <span class="wiz-undo-label">Annuler la dernière action</span>
      </button>
      <button class="mode-switch" title="Tous les outils : timeline, plusieurs vidéos, réglages. Le projet ouvert est conservé." @click="toAdvanced"><Icon name="sliders" />Passer en mode avancé</button>
    </header>

    <Transition name="toast">
      <div v-if="state.toast" class="toast wiz-toast" :class="state.toastKind" :role="state.toastKind === 'error' ? 'alert' : 'status'" title="Cliquer pour fermer" @click="state.toast = ''">
        <Icon :name="state.toastKind === 'error' ? 'alert' : 'info'" />
        <span class="toast-text">{{ state.toast }}</span>
        <button class="icon-btn bare" aria-label="Fermer le message"><Icon name="close" :size="14" /></button>
      </div>
    </Transition>

    <main ref="mainEl" class="wiz-main" :class="{ 'with-video': withVideo }">
      <button v-if="canBack && !withVideo" class="wiz-back quiet" data-back title="Revenir à l'écran précédent (Échap)" @click="back"><Icon name="arrowLeft" />Retour</button>

      <!-- Vidéo et commandes : seulement sur les écrans qui en ont besoin -->
      <div v-if="withVideo" class="wiz-stage" :style="stageStyle" @pointerdown.capture="notePress">
        <Player simple :style="playerStyle" />
        <div ref="underEl" class="wiz-under">
          <template v-if="wiz.screen === 'trim' && trimClip">
            <TrimBar
              v-model:start="trim.a" v-model:end="trim.b" :total="trimClip.duration" :step="1 / fpsOf(trimClip)"
:start-text="human(trim.a)" :end-text="human(trim.b)"
              @preview="previewTrim" @commit="saveTrim"
            >
              <button class="wiz-play" @click="playExtract"><Icon :name="state.playing ? 'pause' : 'play'" />{{ state.playing ? "Pause" : "Lire l'extrait" }}</button>
            </TrimBar>
          </template>
          <template v-else-if="wiz.screen === 'object' && osub === 'draw'">
            <label class="wiz-seek">
              <span>Déplacez ce curseur jusqu'à un moment où l'objet se voit bien</span>
              <WizTime kind="slider" @seek="scrub" />
            </label>
          </template>
          <template v-else-if="wiz.screen === 'object' && osub === 'result'">
            <div class="wiz-controls">
              <button class="wiz-play" @click="toggle"><Icon :name="state.playing ? 'pause' : 'play'" />{{ state.playing ? "Pause" : "Lecture" }}</button>
              <WizTime kind="slider" @seek="scrub" />
            </div>
          </template>
          <template v-else-if="wiz.screen === 'verify' && vphase === 'sweep'">
            <!-- relecture : lecture, retour de 5 s, avancement, vitesse (2× par défaut) -->
            <div class="wiz-controls">
              <button class="wiz-play" @click="toggle"><Icon :name="state.playing ? 'pause' : 'play'" />{{ state.playing ? "Pause" : "Lecture" }}</button>
              <button class="wiz-rewind" data-rewind title="Revenir 5 secondes en arrière" aria-label="Revenir 5 secondes en arrière" @click="rewind"><Icon name="stepBack" />5 s</button>
              <WizTime kind="bar" />
              <span class="wiz-speed" role="group" aria-label="Vitesse de lecture">
                <span class="dim">Vitesse</span>
                <span class="joined">
                  <button :class="{ on: !fast }" :aria-pressed="!fast" data-speed="1" @click="setFast(false)">Normale</button>
                  <button :class="{ on: fast }" :aria-pressed="fast" data-speed="2" title="Deux fois plus vite, pour tout regarder sans y passer trop de temps" @click="setFast(true)">2×</button>
                </span>
              </span>
            </div>
          </template>
          <template v-else-if="wiz.screen === 'verify'">
            <!-- endroit signalé, alerte : se déplacer dans le passage ; discret, ce n'est pas l'action principale -->
            <div class="wiz-controls wiz-nav">
              <WizSpan v-if="navSpan" :from="navSpan.from" :to="navSpan.to" @seek="scrubPassage" />
              <span v-else class="spacer"></span>
              <span class="wiz-speed" role="group" aria-label="Vitesse de lecture">
                <span class="dim">Vitesse</span>
                <span class="joined">
                  <button :class="{ on: review.speed === 1 }" :aria-pressed="review.speed === 1" @click="review.speed = 1">Normal</button>
                  <button :class="{ on: review.speed !== 1 }" :aria-pressed="review.speed !== 1" @click="review.speed = 0.5">Ralenti</button>
                </span>
              </span>
            </div>
            <div class="wiz-controls">
              <button class="wiz-play" @click="toggle"><Icon :name="state.playing ? 'pause' : 'play'" />{{ state.playing ? "Pause" : "Lecture" }}</button>
              <span class="joined wiz-frames" role="group" aria-label="Image par image">
                <button class="icon-btn" data-frame="-1" title="Image précédente (touche ←)" aria-label="Image précédente" :disabled="!navSpan" @click="stepFrame(-1)"><Icon name="stepBack" /></button>
                <button class="icon-btn" data-frame="1" title="Image suivante (touche →)" aria-label="Image suivante" :disabled="!navSpan" @click="stepFrame(1)"><Icon name="stepForward" /></button>
              </span>
              <button data-replay @click="replay"><Icon name="refresh" />Revoir depuis le début</button>
            </div>
          </template>
        </div>
      </div>

      <section ref="panelEl" class="wiz-panel" :class="[`s-${wiz.screen}`, { sheet: sheetShown }]">
        <!-- à côté de la vidéo, « Retour » est en tête des questions : aucune bande vide au-dessus de l'image -->
        <button v-if="canBack && withVideo" class="wiz-back quiet in-panel" data-back title="Revenir à l'écran précédent (Échap)" @click="back"><Icon name="arrowLeft" />Retour</button>
        <!-- 1. Vidéo -->
        <template v-if="!state.loaded">
          <span class="spinner large"></span>
          <p class="wiz-lead">Démarrage…</p>
        </template>
        <template v-else-if="p && lost.length">
          <span class="wiz-icon warn"><Icon name="alert" :size="34" /></span>
          <h1>La vidéo est introuvable</h1>
          <p class="wiz-lead">Elle a été déplacée ou renommée : <span class="mono">{{ lost[0].path }}</span></p>
          <div class="wiz-actions">
            <button class="primary big" data-primary @click="picker = true"><Icon name="folder" :size="18" />Choisir une vidéo…</button>
          </div>
        </template>
        <template v-else-if="p && !previewReady">
          <template v-if="previewFailed">
            <span class="wiz-icon warn"><Icon name="alert" :size="34" /></span>
            <h1>L'aperçu n'a pas pu être préparé</h1>
            <p class="wiz-error">{{ job?.message || "La préparation a été arrêtée." }}</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="retryPreview"><Icon name="refresh" :size="18" />Réessayer</button>
              <button class="big" @click="picker = true">Choisir une autre vidéo…</button>
            </div>
          </template>
          <template v-else>
            <h1>Préparation de l'aperçu…</h1>
            <p class="wiz-lead">Le logiciel prépare votre vidéo pour l'afficher sans saccade. C'est fait une seule fois par vidéo.</p>
            <WizProgress v-if="running === 'proxy'" phase="Votre vidéo d'origine n'est pas modifiée." />
            <span v-else class="spinner large"></span>
          </template>
        </template>
        <template v-else-if="wiz.screen === 'video'">
          <span class="wiz-icon"><Icon name="film" :size="34" /></span>
          <h1>Quelle vidéo voulez-vous flouter ?</h1>
          <template v-if="p && clips.length">
            <p class="wiz-lead">Vidéo choisie : <strong>{{ clips[0].name }}</strong> ({{ human(clips[0].duration) }}).</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="show(firstQuestion())">Continuer avec cette vidéo<Icon name="arrowRight" :size="18" /></button>
              <button class="big" @click="picker = true"><Icon name="folder" :size="18" />Choisir une autre vidéo…</button>
            </div>
          </template>
          <template v-else>
            <p class="wiz-lead">Choisissez-la sur cet ordinateur. Elle ne sera pas modifiée, et rien n'est envoyé sur Internet.</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="picker = true"><Icon name="folder" :size="18" />Choisir une vidéo…</button>
            </div>
          </template>
          <RecentList :exclude="p?.path" :disabled="busy" @open="picked" />
          <button class="wiz-link" data-storage @click="storageOpen = true"><Icon name="drive" />Libérer de la place</button>
        </template>

        <!-- 1 bis. Réglages habituels -->
        <template v-else-if="wiz.screen === 'profile'">
          <template v-if="psub === 'running'">
            <h1>Je reprends vos réglages…</h1>
            <p class="wiz-lead">{{ profile?.objects.length ? "Le logiciel cherche vos objets dans cette vidéo, puis les suit." : "Un instant." }}</p>
            <WizProgress ref="progress" vague phase="Vous verrez le résultat juste après." confirm="Arrêter ? Vous choisirez vous-même ce qu'il faut flouter." keep-going="Continuer" />
          </template>
          <template v-else-if="psub === 'result'">
            <span class="wiz-icon" :class="profileError ? 'warn' : 'ok'"><Icon :name="profileError ? 'alert' : 'check'" :size="34" /></span>
            <h1>{{ profileError ? "Vos réglages n'ont pas pu être repris" : "Vos réglages sont repris" }}</h1>
            <p v-if="profileError" class="wiz-error">{{ profileError }}</p>
            <p v-else class="wiz-lead">Vous pourrez encore tout changer aux écrans suivants.</p>
            <ul v-if="profileNotes.length" class="wiz-list wiz-notes">
              <li v-for="n in profileNotes" :key="n.text" :class="{ warn: !n.ok }"><Icon :name="n.ok ? 'check' : 'alert'" /><span class="grow">{{ n.text }}</span></li>
            </ul>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="show('what')">Continuer<Icon name="arrowRight" :size="18" /></button>
            </div>
          </template>
          <template v-else>
            <span class="wiz-icon"><Icon name="gauge" :size="34" /></span>
            <h1>Reprendre vos réglages habituels ?</h1>
            <p v-if="profile" class="wiz-lead">Vous les avez enregistrés {{ dayText(profile.saved_at) }} : flouter <strong>{{ profileText || "vos objets" }}</strong>{{ profile.preset !== "normal" ? `, vidéo de type « ${presetInfo[profile.preset].label} »` : "" }}.</p>
            <p v-if="profile?.objects.length" class="wiz-lead">Le logiciel cherchera {{ profile.objects.length > 1 ? "ces objets" : "cet objet" }} dans cette vidéo et {{ profile.objects.length > 1 ? "les" : "le" }} suivra tout seul.</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary :disabled="busy" @click="followProfile(applyProfile)"><Icon name="check" :size="18" />Oui, les reprendre</button>
              <button class="big" :disabled="busy" @click="skipProfile">Non merci</button>
            </div>
          </template>
        </template>

        <!-- 2. Que faut-il flouter ? -->
        <template v-else-if="wiz.screen === 'what'">
          <h1>Que faut-il flouter ?</h1>
          <p class="wiz-lead">Cochez tout ce qui doit être caché dans la vidéo.</p>
          <div class="wiz-cards">
            <label class="wiz-card" :class="{ on: choice.face }">
              <input v-model="choice.face" type="checkbox" />
              <span class="wiz-card-icon"><Icon name="face" :size="30" /></span>
              <span class="wiz-card-name">Les visages</span>
              <span class="wiz-card-text">Ceux des passants, trouvés automatiquement.</span>
            </label>
            <label class="wiz-card" :class="{ on: choice.plate }">
              <input v-model="choice.plate" type="checkbox" />
              <span class="wiz-card-icon"><Icon name="plate" :size="30" /></span>
              <span class="wiz-card-name">Les plaques d'immatriculation</span>
              <span class="wiz-card-text">Celles des véhicules, trouvées automatiquement.</span>
            </label>
            <label class="wiz-card" :class="{ on: choice.object }">
              <input v-model="choice.object" type="checkbox" />
              <span class="wiz-card-icon"><Icon name="target" :size="30" /></span>
              <span class="wiz-card-name">Un objet précis</span>
              <span class="wiz-card-text">Compteur, téléphone, GPS… Vous me le montrerez à l'écran suivant.</span>
            </label>
          </div>
          <p class="wiz-note" :class="{ shown: !!whatNote }" role="status"><Icon name="info" />{{ whatNote || " " }}</p>
          <div class="wiz-actions">
            <button class="primary big" data-primary :disabled="!whatValid" @click="saveWhat">Continuer<Icon name="arrowRight" :size="18" /></button>
          </div>
        </template>

        <!-- 2 bis. Montrez-moi l'objet -->
        <template v-else-if="wiz.screen === 'object'">
          <template v-if="osub === 'draw'">
            <h1>Montrez-moi l'objet</h1>
            <ol class="wiz-how">
              <li>Avec le curseur sous la vidéo, choisissez un moment où l'objet se voit bien.</li>
              <li><strong>Entourez l'objet avec la souris</strong> : cliquez, glissez, relâchez.</li>
            </ol>
            <p class="wiz-wait"><Icon name="pen" :size="18" />En attente de votre tracé sur la vidéo…</p>
          </template>
          <template v-else-if="osub === 'name'">
            <h1>Quel est cet objet ?</h1>
            <div class="wiz-names">
              <button v-for="n in quickNames" :key="n" class="big" :class="{ on: !customName.trim() && objectName === n }" :aria-pressed="!customName.trim() && objectName === n" @click="objectName = n, customName = ''">{{ capital(n) }}</button>
              <input v-model="customName" type="text" maxlength="40" spellcheck="false" placeholder="Autre nom" aria-label="Nom de l'objet" />
            </div>
            <p v-if="replaces" class="wiz-note shown"><Icon name="info" />« {{ capital(chosenName) }} » existe déjà : son suivi sera corrigé sur ce plan seulement.</p>
            <h2>L'objet bouge-t-il dans l'image ?</h2>
            <div class="wiz-answers">
              <button class="big answer" :disabled="!chosenName" data-answer="track" @click="designate('track')"><span><strong>Oui</strong><small>Le logiciel le suivra tout au long de la vidéo, et le retrouvera après chaque changement de plan.</small></span></button>
              <button class="big answer" :disabled="!chosenName" data-answer="fixed" @click="designate('fixed')"><span><strong>Non, il reste au même endroit</strong><small>Par exemple si la caméra est fixée à la moto.</small></span></button>
            </div>
          </template>
          <template v-else-if="osub === 'tracking'">
            <h1>Je suis l'objet dans la vidéo…</h1>
            <p class="wiz-lead">« {{ capital(fresh?.name ?? "") }} » est recherché image par image.</p>
            <WizProgress phase="Vous verrez le résultat juste après." />
          </template>
          <template v-else-if="osub === 'failed'">
            <h1>Le suivi n'a pas fonctionné</h1>
            <p class="wiz-error">{{ trackError }}</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="startDraw"><Icon name="refresh" :size="18" />Recommencer</button>
            </div>
          </template>
          <template v-else>
            <template v-if="fresh">
              <h1>L'objet est-il bien caché ?</h1>
              <p class="wiz-lead">Regardez ces quelques secondes : « {{ capital(fresh.name) }} » doit rester flouté.</p>
            </template>
            <template v-else>
              <h1>{{ objects.length > 1 ? "Vos objets à flouter" : "Votre objet à flouter" }}</h1>
              <p class="wiz-lead">Lancez la lecture pour voir le flou.</p>
            </template>
            <ul v-if="!fresh || objects.length > 1" class="wiz-list">
              <li v-for="o in objects" :key="o.clip + o.name">
                <Icon name="check" /><span class="grow">{{ capital(o.name) }}</span>
                <button v-if="!fresh" class="small quiet" :disabled="busy" :aria-label="`Retirer ${o.name}`" @click="removeObject(o)">Retirer</button>
              </li>
            </ul>
            <div class="wiz-actions column">
              <button class="primary big" data-primary :disabled="!objects.length" @click="show('trim')">{{ fresh ? "C'est bon" : "Continuer" }}<Icon name="arrowRight" :size="18" /></button>
              <button v-if="fresh" class="big" :disabled="busy" @click="redo"><Icon name="refresh" :size="18" />Recommencer</button>
              <button class="big" :disabled="busy" @click="startDraw"><Icon name="plus" :size="18" />Ajouter un autre objet</button>
            </div>
          </template>
        </template>

        <!-- 3. Couper -->
        <template v-else-if="wiz.screen === 'trim'">
          <template v-if="advancedMontage">
            <span class="wiz-icon"><Icon name="scissors" :size="34" /></span>
            <h1>Ce montage a été fait en mode avancé</h1>
            <p class="wiz-lead">{{ montageSummary }}</p>
            <p class="wiz-lead">Le mode simple ne sait couper que le début et la fin : il laisse ce montage tel quel.</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="show(afterTrim())">Continuer sans le modifier<Icon name="arrowRight" :size="18" /></button>
              <button class="big" @click="toAdvanced"><Icon name="sliders" :size="18" />Passer en mode avancé</button>
            </div>
          </template>
          <template v-else-if="trimClip">
            <h1>Voulez-vous couper le début ou la fin ?</h1>
            <p class="wiz-lead">Faites glisser les deux poignées orange sous la vidéo. Ce qui est grisé sera retiré.</p>
            <p class="wiz-kept">Vous gardez <strong>{{ human(trim.b - trim.a) }}</strong> sur {{ human(trimClip.duration) }}.</p>
            <div class="wiz-actions column">
              <button v-if="trimmed" class="primary big" data-primary :disabled="busy" @click="keepPart"><Icon name="scissors" :size="18" />Garder cette partie</button>
              <button class="big" :class="{ primary: !trimmed }" :data-primary="trimmed ? undefined : ''" :disabled="busy" @click="keepAll">Ne rien couper</button>
            </div>
          </template>
        </template>

        <!-- 4. Type de vidéo -->
        <template v-else-if="wiz.screen === 'type'">
          <h1>Comment est votre vidéo ?</h1>
          <p class="wiz-lead">Le logiciel règle sa recherche d'après ce choix. Dans le doute, gardez « Normal ».</p>
          <div class="wiz-cards" role="radiogroup" aria-label="Type de vidéo">
            <label v-for="id in presetIds" :key="id" class="wiz-card" :class="{ on: typeChoice === id }" :data-type="id">
              <input v-model="typeChoice" type="radio" name="wiz-type" :value="id" />
              <span class="wiz-card-icon"><Icon :name="presetIcons[id]" :size="30" /></span>
              <span class="wiz-card-name">{{ presetInfo[id].label }}</span>
              <span class="wiz-card-text">{{ nb(presetInfo[id].text) }}</span>
            </label>
          </div>
          <p class="wiz-note" :class="{ shown: !!typeNote }" role="status"><Icon name="info" />{{ typeNote || " " }}</p>
          <div class="wiz-actions">
            <button class="primary big" data-primary :disabled="busy" @click="saveType">Continuer<Icon name="arrowRight" :size="18" /></button>
          </div>
        </template>

        <!-- 4 bis. Analyse -->
        <template v-else-if="wiz.screen === 'analyse'">
          <template v-if="analyseView === 'running'">
            <h1>Analyse en cours…</h1>
            <p class="wiz-lead">Vous pouvez laisser l'ordinateur travailler. Ne fermez pas cette fenêtre.</p>
            <WizProgress ref="progress" :phase="looksFor ? `Recherche : ${looksFor}.` : 'Suivi de vos objets.'" confirm="Arrêter l'analyse ? Ce qui est déjà fait est conservé." keep-going="Continuer l'analyse" />
          </template>
          <template v-else-if="analyseView === 'done'">
            <span class="wiz-icon ok"><Icon name="check" :size="34" /></span>
            <h1>{{ finished ? "Analyse terminée" : "L'analyse est déjà faite" }}</h1>
            <p class="wiz-lead">{{ found }} {{ appliedCount ? "Tout est flouté." : "" }}</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="show('verify')">Continuer<Icon name="arrowRight" :size="18" /></button>
            </div>
          </template>
          <template v-else-if="analyseView === 'error'">
            <span class="wiz-icon warn"><Icon name="alert" :size="34" /></span>
            <h1>L'analyse a échoué</h1>
            <p class="wiz-error">{{ job?.message }}</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="analyse"><Icon name="refresh" :size="18" />Réessayer</button>
            </div>
          </template>
          <template v-else>
            <span class="wiz-icon"><Icon name="scan" :size="34" /></span>
            <h1>{{ analyseView === "stopped" ? "L'analyse a été arrêtée" : "Lançons l'analyse" }}</h1>
            <p v-if="analyseView === 'stopped'" class="wiz-lead">Ce qui est déjà fait est conservé : l'analyse reprendra là où elle s'est arrêtée.</p>
            <p v-else class="wiz-lead">Le logiciel va regarder votre vidéo ({{ human(duration) }}) image par image pour y trouver {{ looksFor || "vos objets" }}.</p>
            <p v-if="estimateText" class="wiz-lead wiz-estimate" data-estimate><Icon name="clock" />L'analyse prendra <strong>{{ estimateText }}</strong> sur cet ordinateur. Le temps restant s'affichera pendant qu'elle avance.</p>
            <p v-else class="wiz-lead dim"><Icon name="clock" />La durée dépend de votre ordinateur : de quelques minutes à plus d'une heure pour une longue vidéo. Le temps restant s'affichera après quelques secondes.</p>
            <p class="wiz-measure" :class="{ shown: measuring || measureFailed }" role="status">
              <template v-if="measuring"><span class="spinner small"></span>{{ measuringText }}</template>
              <template v-else-if="measureFailed">La durée n'a pas pu être mesurée. Lancez l'analyse : le temps restant s'affichera.</template>
              <template v-else>&nbsp;</template>
            </p>
            <div class="wiz-actions">
              <button class="primary big" data-primary :disabled="busy && !measuring" @click="analyse"><Icon name="scan" :size="18" />{{ analyseView === "stopped" ? "Reprendre l'analyse" : "Lancer l'analyse" }}</button>
              <button v-if="canMeasure" class="big" data-measure :disabled="busy" @click="measure"><Icon name="clock" :size="18" />Combien de temps ça va prendre ?</button>
            </div>
          </template>
        </template>

        <!-- 5. Vérification : rien à valider ; une relecture, puis les endroits signalés et les alertes -->
        <template v-else-if="wiz.screen === 'verify'">
          <!-- planche facultative : jamais sur le chemin principal -->
          <WizSheet v-if="vphase === 'sheet'" @done="vphase = 'intro'" />

          <template v-else-if="vphase === 'intro'">
            <span class="wiz-icon" :class="{ ok: sweepDone }"><Icon :name="sweepDone ? 'check' : 'eye'" :size="34" /></span>
            <template v-if="sweepDone">
              <h1>Vous avez déjà regardé toute la vidéo</h1>
              <p class="wiz-lead">Si vous avez changé des flous depuis, vous pouvez la regarder à nouveau.</p>
              <div class="wiz-actions">
                <button class="primary big" data-primary @click="afterSweep">Continuer<Icon name="arrowRight" :size="18" /></button>
                <button class="big" data-again @click="beginSweep('start')"><Icon name="refresh" :size="18" />La regarder à nouveau</button>
              </div>
            </template>
            <template v-else>
              <h1>Tout ce que le logiciel a trouvé est flouté</h1>
              <p class="wiz-lead">Regardez la vidéo une fois pour être sûr.</p>
              <ul class="wiz-rules">
                <li><Icon name="close" :size="18" /><span><strong>Un flou en trop ?</strong> Cliquez dessus, il disparaît.</span></li>
                <li><Icon name="flag" :size="18" /><span><strong>Quelque chose reste lisible ?</strong> Appuyez sur le bouton.</span></li>
              </ul>
              <p class="wiz-hint" data-length>La vidéo dure {{ human(duration) }}.{{ duration >= 30 ? ` Elle est lue deux fois plus vite : comptez ${human(duration / 2)}.` : "" }}</p>
              <div class="wiz-actions">
                <template v-if="canResume">
                  <button class="primary big" data-primary @click="beginSweep('resume')"><Icon name="play" :size="18" />Reprendre à {{ clock(sweepAt) }}</button>
                  <button class="big" data-restart @click="beginSweep('start')">Depuis le début</button>
                </template>
                <button v-else class="primary big" data-primary @click="beginSweep('start')"><Icon name="play" :size="18" />Regarder la vidéo</button>
              </div>
            </template>
            <div class="wiz-links">
              <button v-if="appliedCount" class="wiz-link" data-sheet @click="vphase = 'sheet'"><Icon name="grid" />Retirer des flous en trop d'un coup</button>
              <button v-if="!sweepDone" class="wiz-link" data-skip @click="show('export')">Passer cette étape<Icon name="chevronRight" /></button>
            </div>
          </template>

          <!-- la relecture : un gros bouton ; un clic sur un flou de la vidéo le retire -->
          <template v-else-if="vphase === 'sweep'">
            <template v-if="sweepEnded">
              <h1>Vous avez tout regardé</h1>
              <p class="wiz-lead">{{ markEntries.length ? `Vous avez signalé ${plural(markEntries.length, "endroit", "endroits")}. Réglons-${markEntries.length > 1 ? "les" : "le"} maintenant.` : "Vous n'avez rien signalé." }}</p>
            </template>
            <template v-else>
              <h1>Regardez la vidéo</h1>
              <ul class="wiz-rules compact">
                <li><Icon name="close" /><span><strong>Un flou en trop ?</strong> Cliquez dessus, il disparaît.</span></li>
                <li><Icon name="flag" /><span><strong>Quelque chose reste lisible ?</strong> Appuyez sur le bouton.</span></li>
              </ul>
            </template>
            <div class="wiz-actions column">
              <button v-if="!sweepEnded" class="primary big signal" data-primary :disabled="busy" @click="addMark"><Icon name="flag" :size="20" />Il manque un flou ici<kbd>S</kbd></button>
              <button v-else class="primary big" data-primary @click="afterSweep">Continuer<Icon name="arrowRight" :size="18" /></button>
              <p class="flash" :class="{ shown: !!review.flash }" role="status">{{ review.flash || " " }}</p>
              <p class="wiz-marks">{{ markEntries.length ? `${plural(markEntries.length, "endroit signalé", "endroits signalés")} : vous ${markEntries.length > 1 ? "les" : "le"} réglerez après` : "Aucun signalement pour l'instant" }}</p>
              <button v-if="sweepEnded" class="big" data-again @click="beginSweep('start')"><Icon name="refresh" :size="18" />Tout regarder à nouveau</button>
              <button v-else class="big quiet" data-next @click="afterSweep">Passer à la suite<Icon name="chevronRight" :size="18" /></button>
            </div>
            <!-- dernier flou retiré d'un clic : il s'efface seul, ou au retrait suivant -->
            <div
              v-if="removed" class="wiz-removed" data-removed role="status"
              @pointerenter="holdRemoved(true)" @pointerleave="holdRemoved(false)" @focusin="holdRemoved(true)" @focusout="holdRemoved(false)"
            >
              <p>
                <Icon name="check" />
                <span class="grow"><strong>{{ removedCount > 1 ? `${removedCount} flous retirés` : "Flou retiré" }}</strong><small v-if="removedCount === 1">{{ removed.name }}</small></span>
                <button class="small" data-restore :disabled="busy" @click="restoreRemoved()">Annuler</button>
              </p>
              <button
                v-if="similarCount" class="small" data-similar :disabled="busy"
                title="D'autres flous du même genre sont au même endroit de l'image, à d'autres moments : c'est sans doute la même erreur qui revient" @click="removeSimilar()"
              >Retirer aussi {{ similarCount > 1 ? `les ${similarCount} flous` : "l'autre flou" }} au même endroit</button>
              <button v-if="neverOffered" class="small" data-never :disabled="busy" title="Le logiciel ne floutera plus rien à cet endroit de l'image, dans toute la vidéo" @click="never">Ne plus jamais flouter cet endroit</button>
            </div>
          </template>

          <!-- après la relecture : les endroits signalés un par un, puis les alertes -->
          <template v-else>
            <template v-if="following">
              <h1>Le flou suit ce que vous avez entouré…</h1>
              <p class="wiz-lead">Il s'arrêtera seul à la fin de ce passage ou au prochain changement de plan.</p>
              <WizProgress ref="progress" phase="Vous verrez le résultat juste après." />
            </template>
            <template v-else-if="running === 'dash'">
              <h1>Je suis l'objet dans ce plan…</h1>
              <p class="wiz-lead">Il est recherché image par image, jusqu'au prochain changement de plan.</p>
              <WizProgress ref="progress" phase="Vous verrez le résultat juste après." />
            </template>
            <template v-else-if="!entry">
              <h1>Cet endroit a changé</h1>
              <p class="wiz-lead">Votre correction l'a modifié. Regardez le résultat sur la vidéo.</p>
              <div class="wiz-actions"><button class="primary big" data-primary @click="settle">Continuer<Icon name="arrowRight" :size="18" /></button></div>
            </template>
            <template v-else-if="review.sub === 'missing'">
              <h1>Entourez ce qui doit être flouté</h1>
              <p class="wiz-lead">Sur la vidéo : cliquez, glissez, relâchez. Le flou suivra ce que vous avez entouré.</p>
              <p class="wiz-hint">Pas la bonne image ? Déplacez le curseur sous la vidéo.</p>
              <div v-if="droppedInPassage" class="wiz-dropped">
                <template v-if="!state.showDropped">
                  <p>Le logiciel avait repéré {{ plural(droppedInPassage, "élément", "éléments") }} ici sans {{ droppedInPassage > 1 ? "les" : "le" }} flouter, par prudence.</p>
                  <button @click="state.showDropped = true"><Icon name="eye" />{{ droppedInPassage > 1 ? "Les montrer" : "Le montrer" }}</button>
                </template>
                <template v-else>
                  <p>{{ droppedInPassage > 1 ? "Ils sont entourés" : "Il est entouré" }} en gris sur la vidéo, avec un numéro. Si l'un d'eux est ce qui manque, floutez-le :</p>
                  <p v-if="!droppedHere.length" class="dim">Rien sur cette image. Avancez avec la touche → du clavier.</p>
                  <ul class="wiz-list">
                    <li v-for="tr in droppedHere.slice(0, 4)" :key="tr.id">
                      <span class="grow">{{ capital(tr.name) }} n° {{ tr.id }}</span>
                      <button class="small" :disabled="busy" @click="forceZone(entry.clip, tr.id)">Flouter</button>
                    </li>
                  </ul>
                </template>
              </div>
              <div class="wiz-actions"><button class="big" data-cancel-sub @click="resume">Annuler</button></div>
            </template>
            <template v-else-if="review.sub === 'object'">
              <h1>Entourez « {{ objectHere }} »</h1>
              <p class="wiz-lead">Sur la vidéo : cliquez, glissez, relâchez autour de « {{ objectHere }} ». Le logiciel le suivra de nouveau à partir d'ici, jusqu'au prochain changement de plan.</p>
              <div class="wiz-actions"><button class="big" data-cancel-sub @click="resume">Annuler</button></div>
            </template>
            <template v-else-if="review.sub === 'extra'">
              <h1>Cliquez sur le flou en trop</h1>
              <p class="wiz-lead">Sur la vidéo, les flous sont entourés. Celui que vous cliquez sera retiré ; vous pourrez le rétablir juste après.</p>
              <div class="wiz-actions"><button class="big" data-cancel-sub @click="resume">Annuler</button></div>
            </template>

            <!-- endroit signalé pendant la relecture : entourer ce qui manque, le flou suit -->
            <template v-else-if="entry.type === 'mark'">
              <p class="wiz-count" data-count="marks"><strong>Endroit signalé {{ currentIndex + 1 }} sur {{ entries.length }}</strong><span class="bar still"><span :style="{ transform: `scaleX(${(currentIndex + 1) / Math.max(1, entries.length)})` }"></span></span></p>
              <template v-if="addedHere">
                <h1>Le flou est ajouté</h1>
                <p class="wiz-lead">Il suit ce que vous avez entouré. Regardez la vidéo : tout est-il bien caché ?</p>
                <div class="wiz-actions column">
                  <button class="primary big" data-primary data-settled :disabled="busy" @click="validate"><Icon name="check" :size="18" />C'est réglé</button>
                  <button class="big" data-missing :disabled="busy" @click="missing"><Icon name="plus" :size="18" />Ajouter un autre flou</button>
                  <button class="big quiet" data-later :disabled="busy" @click="next">Plus tard<Icon name="chevronRight" :size="18" /></button>
                </div>
              </template>
              <template v-else>
                <h1>Vous avez signalé cet endroit</h1>
                <p class="wiz-lead">Ce moment tourne en boucle. Quand vous voyez ce qui reste lisible, appuyez sur le bouton, puis entourez-le.</p>
                <div class="wiz-actions column">
                  <button class="primary big" data-primary data-missing :disabled="busy" @click="missing"><Icon name="pen" :size="18" />Entourer ce qui manque</button>
                  <button class="big" data-settled :disabled="busy" @click="validate">Rien ne manque ici</button>
                  <button class="big quiet" data-later :disabled="busy" @click="next">Plus tard<Icon name="chevronRight" :size="18" /></button>
                </div>
              </template>
            </template>

            <!-- alerte : quelque chose peut rester visible ; une question pour tout le passage -->
            <template v-else>
              <p class="wiz-count" data-count="risks"><strong>Alerte {{ currentIndex + 1 }} sur {{ entries.length }}</strong><span class="bar still"><span :style="{ transform: `scaleX(${(currentIndex + 1) / Math.max(1, entries.length)})` }"></span></span><span v-if="entry.done" class="tag ok"><Icon name="check" :size="12" />déjà regardée</span></p>
              <h1>Tout ce qui doit être flouté <span class="wiz-keep-together">l'est-il ?</span></h1>
              <p v-for="r in reasons" :key="r" class="wiz-lead">{{ r }}</p>
              <div class="wiz-actions column">
                <button class="primary big" data-primary :disabled="busy" @click="validate"><Icon name="check" :size="18" />Oui, c'est bon</button>
                <button v-if="objectHere" class="big" data-object :disabled="busy" @click="showObject"><Icon name="target" :size="18" />Montrer « {{ objectHere }} » ici</button>
                <button class="big" data-missing :disabled="busy" @click="missing">Il manque un flou</button>
                <button class="big" data-extra :disabled="busy" @click="extra">Un flou est en trop</button>
                <button class="big quiet" data-later :disabled="busy" @click="next">Plus tard<Icon name="chevronRight" :size="18" /></button>
              </div>
              <ul v-if="review.removed.length" class="wiz-list">
                <li v-for="r in review.removed" :key="`${r.clip}:${r.id}`">
                  <span class="grow">Flou retiré</span>
                  <button class="small" :disabled="busy" @click="restoreZone(r)">Rétablir</button>
                </li>
              </ul>
            </template>
          </template>
        </template>

        <!-- 6. Enregistrer -->
        <template v-else-if="wiz.screen === 'export'">
          <template v-if="exportView === 'running'">
            <h1>{{ extracting ? "Création de l'extrait…" : "Création de la vidéo floutée…" }}</h1>
            <p class="wiz-lead">Ne fermez pas cette fenêtre. Votre vidéo d'origine n'est pas modifiée.</p>
            <WizProgress ref="progress" :phase="extracting ? 'Écriture de l\'extrait.' : 'Écriture de la vidéo.'" :confirm="extracting ? undefined : 'Arrêter ? La vidéo en cours de création sera abandonnée.'" keep-going="Continuer" />
          </template>
          <template v-else-if="exportView === 'error'">
            <span class="wiz-icon warn"><Icon name="alert" :size="34" /></span>
            <h1>{{ extracting ? "L'extrait n'a pas pu être créé" : "La vidéo n'a pas pu être créée" }}</h1>
            <p class="wiz-error">{{ job?.message }}</p>
            <div class="wiz-actions">
              <button class="primary big" data-primary @click="extracting ? exportExtract() : exportVideo()"><Icon name="refresh" :size="18" />Réessayer</button>
            </div>
          </template>
          <template v-else-if="p">
            <h1>Enregistrer la vidéo</h1>
            <ul class="wiz-summary">
              <li><Icon name="clock" :size="18" /><span>Durée : <strong>{{ human(duration) }}</strong></span></li>
              <li><Icon name="lock" :size="18" /><span>Flouté : <strong>{{ blurred }}</strong></span></li>
              <li v-if="remaining.swept && !leftCount" data-watched><Icon name="check" :size="18" /><span>Vous avez regardé <strong>toute la vidéo</strong></span></li>
            </ul>
            <p v-if="needsAnalysis" class="wiz-note shown warn"><Icon name="alert" />La vidéo a changé depuis l'analyse : il faut l'analyser à nouveau avant de l'enregistrer.</p>
            <p v-else-if="p.problem" class="wiz-error">{{ p.problem }}</p>
            <div v-else-if="leftCount" class="unchecked wiz-unchecked" data-left>
              <p>
                <Icon name="alert" /><span><strong>{{ leftText }}</strong>{{ remaining.risks ? "" : " Quelque chose peut rester lisible dans la vidéo." }}</span>
                <button class="small" data-see-left @click="seeLeft">{{ leftCount > 1 ? "Les regarder" : "Le regarder" }}</button>
              </p>
              <label class="check"><input v-model="confirmed" type="checkbox" data-confirm /> Je crée la vidéo quand même</label>
            </div>
            <p v-if="!needsAnalysis && !p.problem && !remaining.swept" class="wiz-note shown wiz-unwatched" data-unwatched>
              <Icon name="info" /><span>Vous n'avez pas regardé la vidéo en entier. C'est le seul moyen de repérer ce que le logiciel n'a jamais vu.</span>
              <button class="small" @click="watchAll">La regarder</button>
            </p>
            <p v-if="stopped" class="wiz-note shown"><Icon name="info" />La création a été arrêtée. Aucune vidéo n'a été enregistrée.</p>
            <div v-if="wiz.extract" class="wiz-extract" data-extract role="status">
              <p><Icon name="check" /><span><strong>L'extrait est prêt.</strong> Regardez-le, puis créez la vidéo entière.</span></p>
              <p class="mono wiz-extract-path">{{ wiz.extract }}</p>
              <button class="small" @click="reveal(wiz.extract)"><Icon name="folderOpen" />Ouvrir le dossier</button>
              <small v-if="revealError" class="dim">Le dossier n'a pas pu s'ouvrir tout seul : l'extrait est bien à l'emplacement indiqué.</small>
            </div>
            <label class="wiz-field">Nom du fichier
              <input v-model="form.output" type="text" spellcheck="false" />
              <small class="dim">Il sera enregistré dans le même dossier que votre vidéo.</small>
            </label>
            <div class="wiz-format">
              <span id="wiz-format-label" class="wiz-format-label">Format de la vidéo</span>
              <div class="wiz-format-choices" role="group" aria-labelledby="wiz-format-label">
                <button v-for="f in exportPresets" :key="f.id" :aria-pressed="wiz.format === f.id" :class="{ on: wiz.format === f.id }" :data-format="f.id" @click="pickFormat(f.id)">{{ f.label }}</button>
              </div>
              <small class="dim" role="status">{{ formatText }}</small>
            </div>
            <button class="fold-head" :aria-expanded="options" @click="options = !options"><Icon :name="options ? 'chevronDown' : 'chevronRight'" />Options</button>
            <label v-if="options" class="wiz-field quality">
              <span class="split">Qualité <span class="mono">{{ form.quality }}</span></span>
              <input v-model.number="form.quality" type="range" min="40" max="100" step="5" />
              <span class="split dim"><small>Fichier plus léger</small><small>Meilleure image</small></span>
            </label>
            <div class="wiz-actions">
              <button v-if="needsAnalysis" class="primary big" data-primary @click="show('analyse')">Analyser la vidéo<Icon name="arrowRight" :size="18" /></button>
              <button v-else class="primary big" data-primary :disabled="!canExport" @click="exportVideo"><Icon name="export" :size="18" />Créer la vidéo floutée</button>
              <button v-if="canExtract" class="big" data-try :disabled="busy" @click="exportExtract">Essayer d'abord sur 10 secondes</button>
            </div>
            <p v-if="canExtract" class="wiz-hint">L'essai crée un court extrait, pris au milieu de la vidéo : vous voyez le vrai résultat sans attendre la vidéo entière.</p>
          </template>
        </template>

        <!-- Fin -->
        <template v-else>
          <span class="wiz-icon ok"><Icon name="check" :size="34" /></span>
          <h1>Votre vidéo est prête</h1>
          <p class="wiz-lead">Elle a été enregistrée ici :</p>
          <p class="wiz-path mono" tabindex="0">{{ wiz.output }}</p>
          <p v-if="revealError" class="wiz-note shown warn" role="alert"><Icon name="alert" /><span>Le dossier n'a pas pu s'ouvrir tout seul. Votre vidéo est bien à l'emplacement indiqué ci-dessus.<small class="dim">{{ revealError }}</small></span></p>
          <div class="wiz-actions">
            <button class="primary big" data-primary @click="reveal()"><Icon name="folderOpen" :size="18" />Ouvrir le dossier</button>
            <button class="big" @click="another">Flouter une autre vidéo</button>
          </div>
          <div v-if="p" class="wiz-keep">
            <button class="big" data-keep :disabled="savingProfile || profileKept" @click="keepProfile"><Icon :name="profileKept ? 'check' : 'gauge'" :size="18" />{{ profileKept ? "Réglages enregistrés" : "Enregistrer ces réglages pour mes prochaines vidéos" }}</button>
            <small>À la prochaine vidéo, le logiciel vous proposera de flouter les mêmes choses{{ objects.length ? ` et cherchera tout seul ${list(objects.map((o) => `« ${capital(o.name)} »`))}` : "" }}.{{ profile && !profileKept ? " Vos réglages enregistrés auparavant seront remplacés." : "" }}</small>
          </div>
        </template>
      </section>
    </main>

    <Transition name="modal">
      <FilePicker v-if="picker" simple title="Choisir une vidéo" @pick="picked" @close="picker = false" />
    </Transition>
    <Transition name="modal">
      <StorageDialog v-if="storageOpen" @close="storageOpen = false" />
    </Transition>
    <Transition name="modal">
      <div v-if="dropAsk" class="modal" @pointerdown.self="dropAsk = ''">
        <div class="dialog wiz-drop" role="alertdialog" aria-modal="true" aria-labelledby="wiz-drop-title">
          <h3 id="wiz-drop-title"><Icon name="film" :size="18" />Ouvrir cette vidéo ?</h3>
          <p><strong>{{ fileName(dropAsk) }}</strong></p>
          <p>La vidéo en cours est enregistrée telle qu'elle est : vous la retrouverez dans vos vidéos récentes.</p>
          <div class="row end">
            <button data-drop-cancel @click="dropAsk = ''">Annuler</button>
            <button class="primary" data-drop-open @click="confirmDrop">Ouvrir</button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style>
/* Ajouts du mode simple : ils vivent avec l'assistant, la feuille commune n'est pas touchée. */
.wiz-undo { margin-right: 2px; }
.wiz-undo .icon { color: var(--faint); }
.wiz-link { align-self: flex-start; height: 32px; margin-top: 6px; padding: 0 9px 0 6px; background: none; border-color: transparent; color: var(--faint); font-size: 14px; font-weight: 500; }
.wiz-link .icon { color: var(--faint); }
@media (hover: hover) { .wiz-link:hover:not(:disabled) { background: none; border-color: transparent; color: var(--text); } }

/* cartes à choix unique : type de vidéo */
.wiz-card input[type="radio"] { appearance: none; -webkit-appearance: none; margin: 0; border-radius: 50%; background: var(--bg); border: 1px solid var(--line-strong); cursor: pointer; transition: border-color var(--m-out) var(--ease), border-width var(--m-out) var(--ease); }
.wiz-card input[type="radio"]:checked { border: 7px solid var(--accent); background: var(--accent-ink); }

.wiz-estimate { color: var(--text); }
.wiz-estimate .icon { color: var(--accent); }
.wiz-measure { display: flex; align-items: center; gap: 9px; min-height: 22px; color: var(--dim); font-size: 14.5px; opacity: 0; transition: opacity var(--m-in) var(--ease); }
.wiz-measure.shown { opacity: 1; }
.wiz-notes li { align-items: flex-start; padding: 11px 14px; font-size: 15.5px; line-height: 1.45; }
.wiz-notes li > .icon { margin-top: 4px; }
.wiz-notes li.warn > .icon { color: var(--accent); }

/* planche des flous : toute la largeur, la grille défile, les boutons restent visibles */
.wiz-panel.sheet { width: min(1120px, 100%); align-self: stretch; margin: 0 auto; max-height: 100%; }
.wiz-panel.sheet h1 { font-size: 26px; }
.wiz-panel.sheet .wiz-lead { max-width: none; }
.wiz-panel.sheet .wiz-actions { margin-top: 0; }

/* écrans avec la vidéo : pas de bande vide au-dessus de l'image ; « Retour » en tête des questions.
   La largeur de la vidéo et sa hauteur sont calculées (layout) : vidéo verticale aussi haute que possible. */
.wiz .wiz-main.with-video { justify-content: center; padding: 16px 28px 20px; }
.wiz .wiz-main.with-video .wiz-panel { align-self: stretch; }
.wiz .wiz-stage .stage { align-items: center; }
.wiz-back.in-panel { position: sticky; top: 0; left: auto; z-index: 2; flex: none; margin: 0 0 2px; background: var(--bg); }
.wiz .wiz-under { gap: 10px; }
.wiz .wiz-under .wiz-controls { flex-wrap: wrap; row-gap: 8px; }
.wiz-nav { min-height: 26px; }
.wiz .wiz-timebar { min-width: 40px; }
.wiz-rewind .icon { color: var(--dim); }
.wiz-frames .icon-btn { width: 40px; height: 40px; padding: 0; justify-content: center; }
.wiz-frames .icon-btn .icon { color: var(--dim); }

/* vérification : la règle du jeu en deux lignes, les liens discrets, le bandeau « Flou retiré » */
.wiz-rules { align-self: stretch; margin: 2px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; color: var(--dim); font-size: 17px; line-height: 1.45; }
.wiz-rules li { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); }
.wiz-rules li > .icon { flex: none; margin-top: 4px; color: var(--accent); }
.wiz-rules strong { color: var(--text); font-weight: 600; }
.wiz-rules.compact { gap: 6px; font-size: 15px; }
.wiz-rules.compact li { gap: 10px; padding: 8px 11px; }
.wiz-rules.compact li > .icon { margin-top: 3px; }
.wiz-keep-together { white-space: nowrap; }
.wiz-links { display: flex; flex-wrap: wrap; gap: 2px 18px; margin-top: 2px; }
.wiz-links .wiz-link { margin-top: 0; }
.wiz-removed { align-self: stretch; display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; background: var(--raised); border: 1px solid var(--line-strong); border-radius: var(--r-md); font-size: 15px; animation: rise 200ms var(--ease-enter); }
.wiz-removed p { display: flex; align-items: center; gap: 9px; }
.wiz-removed p > .icon { flex: none; color: var(--face); }
.wiz-removed p strong { font-weight: 600; }
.wiz-removed p small { margin-left: 8px; color: var(--dim); font-size: 13.5px; }
.wiz-removed button.small { height: auto; min-height: 34px; padding: 5px 12px; font-size: 14px; white-space: normal; text-align: left; line-height: 1.3; }
.wiz-removed p button.small { flex: none; }
.wiz-panel .wiz-hint + .wiz-actions { margin-top: 6px; }

/* enregistrement : format, extrait d'essai, relecture oubliée */
.wiz-format { align-self: stretch; display: flex; flex-direction: column; gap: 7px; }
.wiz-format-label { color: var(--dim); font-size: 14px; }
.wiz-format-choices { display: flex; flex-wrap: wrap; gap: 8px; }
.wiz-format-choices button { height: 40px; padding: 0 14px; border-radius: 10px; font-size: 15px; }
.wiz-format small { min-height: 20px; font-size: 14px; }
.wiz-panel .wiz-unwatched { align-self: stretch; align-items: center; color: var(--dim); }
.wiz-panel .wiz-unwatched .icon { margin-top: 0; color: var(--faint); }
.wiz-unwatched span { flex: 1; min-width: 0; }
.wiz-unwatched button.small, .wiz-unchecked button.small { flex: none; height: 32px; padding: 0 12px; font-size: 14px; }
.wiz-unchecked p span { flex: 1; min-width: 0; }
.wiz-extract { align-self: stretch; display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 12px 14px; background: rgba(74, 222, 128, .08); border: 1px solid rgba(74, 222, 128, .4); border-radius: var(--r-md); font-size: 15px; }
.wiz-extract p { display: flex; gap: 9px; }
.wiz-extract p .icon { margin-top: 4px; color: var(--face); }
.wiz-extract-path { font-size: 13.5px; overflow-wrap: anywhere; user-select: text; -webkit-user-select: text; }
.wiz-extract button.small { height: 32px; padding: 0 12px; font-size: 14px; }
.wiz-hint { max-width: 62ch; color: var(--faint); font-size: 14px; }

/* fin : retenir ces réglages */
.wiz-keep { align-self: stretch; display: flex; flex-direction: column; align-items: flex-start; gap: 8px; margin-top: 14px; padding-top: 18px; border-top: 1px solid var(--line); }
.wiz-keep button.big { height: auto; min-height: 52px; padding: 8px 20px; white-space: normal; text-align: left; }
.wiz-keep small { max-width: 62ch; color: var(--dim); font-size: 14px; line-height: 1.45; }

.wiz-drop { width: min(460px, 92vw); font-size: 14px; }
.wiz-drop p { overflow-wrap: anywhere; line-height: 1.5; }
.wiz-drop p strong { color: var(--text); }
.wiz-drop button { height: 34px; padding: 0 14px; }

@media (max-width: 1280px), (max-height: 800px) {
  .wiz .wiz-main.with-video { padding: 12px 20px 14px; }
  .wiz-rules.compact { font-size: 14.5px; }
}
@media (max-width: 1100px) {
  .wiz-undo-label { display: none; }
}
</style>
