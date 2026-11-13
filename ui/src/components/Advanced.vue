<script setup lang="ts">
// Mode avancé : toute l'interface de montage (timeline, panneau latéral, dialogues).
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ExportDialog from "./ExportDialog.vue";
import FilePicker from "./FilePicker.vue";
import JobDialog from "./JobDialog.vue";
import Icon from "./Icon.vue";
import Logo from "./Logo.vue";
import Player from "./Player.vue";
import PresetChoice from "./PresetChoice.vue";
import Shortcuts from "./Shortcuts.vue";
import SidePanel from "./SidePanel.vue";
import Timeline from "./Timeline.vue";
import WelcomeArt from "./WelcomeArt.vue";
import { followBlur, formatDuration, measureEstimate, redo, undo } from "../extras";
import { panels } from "../blurs";
import { addMark, doubtDone, doubtEntries, doubtLeft, guided, leave, markEntries, pending, resume, review, startQueue, summary, validate } from "../review";
import {
  addMute, busy, current, deleteSegment, duration, formatTime, job, moveSegment, needsAnalysis, notify, project, refresh, saveManual, segments, send,
  setMode, splitAtPlayhead, start, state, time,
} from "../store";

const picker = ref<"" | "open" | "add">("");
const exporting = ref(false);
const missing = computed(() => project.value?.clips.filter((c) => c.missing) ?? []);
const percent = computed(() => (job.value ? Math.min(100, Math.round((100 * job.value.done) / Math.max(1, job.value.total))) : 0));
const eta = computed(() => {
  const j = job.value;
  if (!j || j.fps <= 0) return "";
  const s = Math.round((j.total - j.done) / j.fps);
  return s > 90 ? `reste ${Math.round(s / 60)} min` : `reste ${s} s`;
});

// --- Guide en quatre étapes ---------------------------------------------------
// Le guide indique quoi faire maintenant ; il ne verrouille rien : chaque étape est cliquable
// et tous les outils restent utilisables à tout moment.
const steps = [
  { id: 1, label: "Montage" },
  { id: 2, label: "Analyse" },
  { id: 3, label: "Vérification" },
  { id: 4, label: "Export" },
];
const analysing = computed(() => busy.value && job.value?.kind === "analyze");
const analysed = computed(() => segments.value.length > 0 && !needsAnalysis.value);
/** Étape déduite de l'état du projet. */
const autoStep = computed(() => {
  if (analysing.value) return 2;
  if (!analysed.value) return 1;
  return pending.value > 0 ? 3 : 4;
});
const step = computed(() => state.step || autoStep.value);
function stepDone(id: number): boolean {
  if (id <= 2) return analysed.value && !analysing.value;
  if (id === 3) return analysed.value && pending.value === 0;
  return false;
}
function goStep(id: number): void {
  state.step = id;
  if (id === 3) state.tab = "review";
  else if (review.mode !== "home") leave(); // on quitte la vérification guidée en changeant d'étape
}
function analyse(): void {
  state.step = 2;
  void start("/api/analyze");
}
function beginReview(): void {
  goStep(3);
  if (doubtLeft.value > 0) startQueue("doubt");
}
// un autre projet : le guide repart de l'étape déduite
watch(() => project.value?.path, () => {
  state.step = 0;
  leave();
});

const guide = computed(() => {
  const empty = !segments.value.length;
  const plural = (n: number, one: string, many: string) => `${n} ${n > 1 ? many : one}`;
  if (empty) return { text: "Le montage est vide. Ajoutez un clip pour commencer.", action: "add" };
  switch (step.value) {
    case 1:
      return needsAnalysis.value
        ? { text: "Coupez ce que vous ne voulez pas garder, puis lancez l'analyse.", action: "analyse" }
        : { text: "Le montage est analysé. Si vous ajoutez un passage, il faudra relancer l'analyse.", action: "continue" };
    case 2:
      return needsAnalysis.value
        ? { text: "Des passages gardés n'ont pas encore été analysés : ils sortiraient sans flou.", action: "analyse", warn: true }
        : { text: "Analyse terminée. Vérifiez maintenant le floutage.", action: "review" };
    case 3:
      if (needsAnalysis.value) return { text: "Analysez d'abord le montage : la vérification porte sur ce que l'analyse a trouvé.", action: "analyse", warn: true };
      if (review.mode === "queue") return { text: "Regardez le passage en boucle et répondez dans le panneau de droite : tout ce qui doit être flouté l'est-il ?", action: "" };
      if (review.mode === "end") return { text: "Vérification terminée. Choisissez la suite dans le panneau de droite.", action: "" };
      if (review.mode === "sweep") return { text: "Relecture complète : appuyez sur S dès que quelque chose reste lisible. La lecture continue.", action: "" };
      if (doubtLeft.value) return { text: `${plural(doubtLeft.value, "passage douteux reste", "passages douteux restent")} à vérifier.`, action: "review" };
      if (markEntries.value.length) return { text: `${plural(markEntries.value.length, "signalement reste", "signalements restent")} à traiter.`, action: "marks" };
      return { text: doubtEntries.value.length ? "Tous les passages douteux sont validés. Faites une relecture complète, ou passez à l'export." : "Aucun passage douteux. Faites une relecture complète, ou passez à l'export.", action: "toExport" };
    default:
      if (needsAnalysis.value) return { text: "Des passages gardés n'ont pas encore été analysés : ils sortiraient sans flou.", action: "analyse", warn: true };
      return pending.value
        ? { text: `${summary()}. Tout n'est pas vérifié.`, action: "resume", warn: true }
        : { text: `${summary()}. Vous pouvez exporter.`, action: "export" };
  }
});

async function picked(path: string): Promise<void> {
  const mode = picker.value;
  picker.value = "";
  if (mode === "open") await send("POST", "/api/open", { path });
  else await send("POST", "/api/clips", { path });
  void refresh();
}

function muteMark(): void {
  if (state.muteIn === null) {
    state.muteIn = time.value;
    notify("Début du son coupé posé. Placez la tête de lecture à la fin puis cliquez « Couper le son : fin » (ou M).");
  } else {
    const from = state.muteIn;
    state.muteIn = null;
    void addMute(from, time.value);
  }
}

// --- Objet désigné à la main (compteur, téléphone, GPS…) --------------------
const quickNames = ["compteur", "téléphone", "GPS"];
const objectName = ref("");
/** Noms des objets déjà désignés dans le clip du rectangle en attente. */
const existingObjects = computed(() => {
  const clip = state.pendingDash?.clip;
  const tracks = clip ? (project.value?.tracks[clip] ?? []) : [];
  return [...new Set(tracks.filter((t) => t.kind === "dash").map((t) => t.name))];
});
/** Nom retenu : celui d'un objet existant s'il ne diffère que par la casse, sinon le texte saisi. */
const chosenName = computed(() => {
  const typed = objectName.value.trim();
  return existingObjects.value.find((n) => n.toLowerCase() === typed.toLowerCase()) ?? typed;
});
const replaces = computed(() => !!chosenName.value && existingObjects.value.includes(chosenName.value));
watch(() => state.pendingDash, (pending) => {
  // premier objet du clip : « compteur » proposé ; ensuite le nom est à donner
  if (pending) objectName.value = existingObjects.value.length ? "" : "compteur";
});
function capital(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

async function dashChoice(mode: "track" | "fixed" | "cancel"): Promise<void> {
  const pending = state.pendingDash;
  const label = chosenName.value;
  if (mode !== "cancel" && !label) return;
  state.pendingDash = null;
  if (!pending || mode === "cancel") return;
  await start("/api/dash", { ...pending, mode, label, direction: "both" });
}

// --- Flou ajouté à la main : il suit ce qui est entouré, ou reste fixe ----------
const followButton = ref<HTMLButtonElement>();
/** Suivi lancé depuis le dialogue : clip du flou à sélectionner à la fin, et retour à la boucle de vérification. */
let followed: { clip: string; guided: boolean } | null = null;
watch(() => state.pendingBlur, (pending) => {
  if (pending) void nextTick(() => followButton.value?.focus()); // Entrée = le choix par défaut
});

async function blurChoice(mode: "follow" | "fixed" | "cancel"): Promise<void> {
  const pending = state.pendingBlur;
  state.pendingBlur = null;
  if (!pending) return;
  const retry = () => {
    // vérification guidée : on reste sur « Il manque un flou », prêt pour un nouveau tracé
    if (pending.guided && review.sub === "missing") state.tool = "manual";
  };
  if (mode === "cancel") return retry();
  if (mode === "follow") {
    followed = { clip: pending.clip, guided: pending.guided };
    await followBlur(pending.clip, pending.time, pending.box, pending.start, pending.end);
    if (!busy.value) {
      followed = null; // refusé par le moteur : son message est affiché
      retry();
    }
    return;
  }
  await saveManual(pending.clip, pending.fixed);
  if (pending.guided) {
    notify("Flou ajouté sur tout le passage. Regardez la boucle : si l'objet bouge, retracez-le à un autre instant avec « Déplacer ici… » dans l'onglet « Flous ».");
    resume();
  } else notify("Flou ajouté pour 2 s. Réglez son début et sa fin dans l'onglet « Flous ».");
}

// Fin d'un traitement court : le suivi d'un flou ajouté et la mesure de vitesse n'ont pas besoin
// d'un écran de résultat. Leur dialogue se ferme seul ; en cas d'échec il reste, avec le message.
watch(() => job.value?.state, (now, before) => {
  const j = job.value;
  if (before !== "running" || !j || now === "running") return;
  if (j.kind === "follow") {
    const from = followed;
    followed = null;
    if (j.state === "done") {
      state.jobOpen = false;
      state.jobFinished = false;
      if (from && j.created !== null && j.created !== undefined) state.selectedTrack = { clip: from.clip, id: j.created };
      notify(`${j.message.replace(/(\d)\.(\d)/g, "$1,$2")} ${from?.guided ? "Regardez la boucle : s'il ne couvre pas tout, ajoutez-en un autre." : "Il est sélectionné : réglez-le dans l'onglet « Flous »."}`);
      if (from?.guided) resume();
    } else if (from?.guided && review.sub === "missing") state.tool = "manual";
  } else if (j.kind === "estimate" && j.state === "done") {
    state.jobOpen = false; // le résultat s'affiche à côté du bouton « Analyser »
    state.jobFinished = false;
  }
});

// --- Estimation de la durée d'analyse -------------------------------------------
const estimateText = computed(() => {
  const e = project.value?.estimate;
  if (!e) return "";
  const speed = e.fps > 0 ? `, à ${e.fps.toFixed(1).replace(".", ",")} images par seconde` : "";
  return `Analyse : ${formatDuration(e.seconds)}${speed}`;
});

/** « Annuler » de l'indicateur réduit : l'analyse et l'export demandent une confirmation, dans le dialogue. */
function cancelJob(): void {
  const kind = job.value?.kind;
  if (kind === "analyze" || kind === "render") {
    state.jobConfirm = true;
    state.jobOpen = true;
  } else void send("POST", "/api/cancel");
}

/** Pendant la vérification guidée, le montage ne se modifie pas : aucun risque de couper par erreur. */
const locked = computed(() => guided.value);
const lockedTip = "Quittez la vérification pour modifier le montage";
const muteTip = "Couper le son d'un passage : posez le début, puis la fin (M)";

/** Vers le mode simple : la vérification guidée en cours est quittée, le projet reste ouvert. */
function toSimple(): void {
  leave();
  state.tool = "none";
  state.pendingDash = null;
  state.pendingBlur = null;
  review.sheet = false;
  if (review.speed > 1) review.speed = 1; // l'assistant ne propose que « Normal » et « Ralenti »
  setMode("simple");
}

function onKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement;
  if (event.key === "Escape" && state.pendingDash) {
    void dashChoice("cancel");
    return;
  }
  if (event.key === "Escape" && state.pendingBlur) {
    void blurChoice("cancel");
    return;
  }
  if (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA") return;
  if (picker.value || exporting.value || state.pendingDash || state.pendingBlur || review.sheet || state.jobOpen || panels.shortcuts) return; // un dialogue est ouvert
  if (event.key === "?" && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    panels.shortcuts = true;
    return;
  }
  if (busy.value) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return; // Ctrl+Z, Ctrl+C… ne sont pas des raccourcis de montage
  // vérification guidée : Entrée valide le passage affiché, S pose un signalement pendant la relecture
  if (event.key === "Enter" && review.mode === "queue" && target.tagName !== "BUTTON" && target.tagName !== "A") {
    event.preventDefault();
    void validate();
    return;
  }
  if ((event.key === "s" || event.key === "S") && review.mode === "sweep") {
    void addMark();
    return;
  }
  if (locked.value) return; // C, Suppr et M sont neutralisés pendant la vérification guidée
  if (event.key === "c" || event.key === "C") void splitAtPlayhead();
  else if ((event.key === "Delete" || event.key === "Backspace") && state.selected >= 0) void deleteSegment(state.selected);
  else if (event.key === "m" || event.key === "M") muteMark();
}

// --- Vidéo verticale (1440 × 2560…) -----------------------------------------------
// La vidéo prend toute la hauteur de la fenêtre ; la consigne, les barres « Lecture » et
// « Montage », la légende et la timeline passent à sa droite au lieu de la réduire par-dessous.
const left = ref<HTMLDivElement>();
const portrait = computed(() => {
  const c = current.value?.clip;
  return !!c && c.height > c.width * 1.05;
});
const stageWidth = ref(0);
function layout(): void {
  const el = left.value;
  const c = current.value?.clip;
  if (!el || !c || !portrait.value) return;
  const style = getComputedStyle(el);
  const padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  const padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  const height = el.clientHeight - padY;
  const width = el.clientWidth - padX;
  const side = Math.min(600, Math.max(520, width * 0.45)); // place gardée à droite : commandes et timeline
  stageWidth.value = Math.round(Math.max(180, Math.min((height * c.width) / c.height, width - side - 14)));
}
const resizer = new ResizeObserver(layout);
watch(portrait, () => void nextTick(layout));
watch(() => [current.value?.clip.width, current.value?.clip.height], () => void nextTick(layout));
watch(left, (el, before) => {
  if (before) resizer.unobserve(before);
  if (el) resizer.observe(el);
});

onMounted(() => {
  window.addEventListener("keydown", onKey);
  // le mode avancé s'ouvre sur l'aperçu du flou ; « Voir les contours », sous la vidéo, pour les régler
  if (review.mode === "home") state.preview = "blur";
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  resizer.disconnect();
  panels.shortcuts = false;
});
</script>

<template>
  <div class="app">
    <header class="top">
      <span class="brand"><Logo /><strong>moto-blur</strong></span>
      <span v-if="project" class="project" :title="project.path">{{ project.name }}</span>
      <nav v-if="project" class="steps" aria-label="Étapes">
        <button
          v-for="s in steps" :key="s.id" class="step" :class="{ current: step === s.id, done: stepDone(s.id) }"
          :aria-current="step === s.id ? 'step' : undefined" :title="`Étape ${s.id} : ${s.label}`" @click="goStep(s.id)"
        >
          <span class="step-num"><Icon v-if="stepDone(s.id)" name="check" :size="12" /><template v-else>{{ s.id }}</template></span>
          <span class="step-label">{{ s.label }}</span>
        </button>
      </nav>
      <span class="spacer"></span>
      <span v-if="project" class="joined history">
        <button
          class="icon-btn" :disabled="busy || !project.history?.undo" :title="project.history?.undo ? `Annuler : ${project.history.undo} (Ctrl+Z)` : 'Rien à annuler'"
          :aria-label="project.history?.undo ? `Annuler : ${project.history.undo}` : 'Annuler (rien à annuler)'" @click="undo()"
        ><Icon name="undo" /></button>
        <button
          class="icon-btn" :disabled="busy || !project.history?.redo" :title="project.history?.redo ? `Rétablir : ${project.history.redo} (Ctrl+Y)` : 'Rien à rétablir'"
          :aria-label="project.history?.redo ? `Rétablir : ${project.history.redo}` : 'Rétablir (rien à rétablir)'" @click="redo()"
        ><Icon name="redo" /></button>
      </span>
      <button class="icon-btn keys-btn" title="Raccourcis clavier (?)" aria-label="Raccourcis clavier" @click="panels.shortcuts = true">?</button>
      <button :disabled="busy" @click="picker = 'open'"><Icon name="folder" />Ouvrir…</button>
      <button v-if="project" :disabled="busy" @click="picker = 'add'"><Icon name="plus" />Ajouter un clip…</button>
      <button class="mode-switch" title="L'assistant pas à pas, une question à la fois. Le projet ouvert est conservé." @click="toSimple"><Icon name="steps" />Passer en mode simple</button>
    </header>

    <Transition name="toast">
      <div v-if="state.toast" class="toast" :class="state.toastKind" :role="state.toastKind === 'error' ? 'alert' : 'status'" title="Cliquer pour fermer" @click="state.toast = ''">
        <Icon :name="state.toastKind === 'error' ? 'alert' : 'info'" />
        <span class="toast-text">{{ state.toast }}</span>
        <button class="icon-btn bare" aria-label="Fermer le message"><Icon name="close" :size="14" /></button>
      </div>
    </Transition>

    <main v-if="project" class="work">
      <div ref="left" class="left" :class="{ portrait }" :style="portrait && stageWidth ? { '--stage-w': `${stageWidth}px` } : undefined">
        <div v-if="missing.length" class="banner error"><Icon name="alert" /><div>Fichier source introuvable : {{ missing.map((c) => c.path).join(", ") }}</div></div>
        <div v-else-if="project.mismatch && !project.conform" class="banner">
          <Icon name="alert" />
          <div>
            Les clips du montage n'ont pas tous la même résolution ou la même fréquence d'images. Comment les assembler ?
            <span class="row">
              <button :disabled="busy" @click="send('PUT', '/api/settings', { conform: 'first' })">Aligner sur le premier clip</button>
              <button :disabled="busy" @click="send('PUT', '/api/settings', { conform: 'largest' })">Aligner sur la plus grande résolution et la plus haute fréquence</button>
            </span>
            <small class="dim">Mise à l'échelle avec bandes noires si le rapport d'image diffère ; images dupliquées ou supprimées si la fréquence diffère.</small>
          </div>
        </div>
        <!-- Guide : une phrase sur ce qu'il y a à faire maintenant, et le bouton de l'action suivante -->
        <div class="guide" :class="{ warn: guide.warn && !busy }">
          <template v-if="job && job.state === 'running'">
            <button class="job" title="Afficher le détail de la progression" @click="state.jobOpen = true">
              <span class="job-text">
                <span class="job-label">{{ job.label }}</span>
                <span class="job-num">{{ percent }} %</span>
                <span class="job-meta"><template v-if="job.fps">{{ job.fps }} i/s</template><template v-if="job.fps && eta"> · </template><template v-if="eta">{{ eta }}</template></span>
              </span>
              <span class="bar"><span :style="{ transform: `scaleX(${percent / 100})` }"></span></span>
            </button>
            <button @click="cancelJob">Annuler</button>
          </template>
          <template v-else-if="job && state.jobFinished">
            <button class="job done" :class="job.state" title="Afficher le résultat" @click="state.jobOpen = true">
              <Icon :name="job.state === 'done' ? 'check' : 'alert'" />
              <span class="job-label">{{ job.state === "error" ? "Le traitement a échoué" : job.state === "cancelled" ? "Traitement annulé" : "Traitement terminé" }} : afficher le résultat</span>
            </button>
          </template>
          <template v-else>
            <span class="guide-step">Étape {{ step }}</span>
            <p class="guide-text">{{ guide.text }}</p>
            <button v-if="guide.action === 'add'" class="primary" @click="picker = 'add'"><Icon name="plus" />Ajouter un clip…</button>
            <template v-else-if="guide.action === 'analyse'">
              <span v-if="estimateText" class="estimate" title="Durée estimée d'après la vitesse mesurée sur quelques images, avec les réglages actuels"><Icon name="clock" />{{ estimateText }}</span>
              <button v-else title="Mesurer la vitesse de l'analyse sur quelques images pour savoir combien de temps elle prendra. Quelques secondes." @click="measureEstimate()"><Icon name="clock" />Estimer la durée</button>
              <button class="primary" title="Détecter les visages et les plaques dans les passages gardés qui ne l'ont pas encore été" @click="analyse"><Icon name="scan" />Analyser</button>
            </template>
            <button v-else-if="guide.action === 'continue'" class="primary" @click="goStep(autoStep)">Continuer<Icon name="arrowRight" /></button>
            <button v-else-if="guide.action === 'review'" class="primary" @click="beginReview">{{ doubtDone ? "Reprendre la vérification" : "Commencer la vérification" }}<Icon name="arrowRight" /></button>
            <button v-else-if="guide.action === 'marks'" class="primary" @click="goStep(3), startQueue('marks')">Traiter les signalements<Icon name="arrowRight" /></button>
            <button v-else-if="guide.action === 'toExport'" class="primary" @click="goStep(4)">Passer à l'export<Icon name="arrowRight" /></button>
            <template v-else-if="guide.action === 'resume'">
              <button @click="exporting = true">Exporter quand même…</button>
              <button class="primary" @click="beginReview">Reprendre la vérification<Icon name="arrowRight" /></button>
            </template>
            <button v-else-if="guide.action === 'export'" class="primary" @click="exporting = true"><Icon name="export" />Exporter…</button>
          </template>
        </div>
        <!-- là où on lance l'analyse : le préréglage, avec son avertissement -->
        <div v-if="guide.action === 'analyse' && !busy && !(job && state.jobFinished)" class="analysis-bar"><PresetChoice compact /></div>
        <Player />
        <div class="tools" role="group" aria-labelledby="group-montage">
          <span id="group-montage" class="group-name">Montage</span>
          <button :disabled="busy || locked" :title="locked ? lockedTip : 'Couper le passage à la tête de lecture (C)'" @click="splitAtPlayhead"><Icon name="scissors" />Couper ici</button>
          <button :disabled="busy || locked || state.selected < 0" :title="locked ? lockedTip : 'Supprimer le passage sélectionné (Suppr)'" @click="deleteSegment(state.selected)"><Icon name="trash" />Supprimer le passage</button>
          <span class="joined">
            <button :disabled="busy || locked || state.selected <= 0" :title="locked ? lockedTip : 'Déplacer le passage sélectionné vers le début'" @click="moveSegment(state.selected, -1)"><Icon name="arrowLeft" />Déplacer à gauche</button>
            <button :disabled="busy || locked || state.selected < 0 || state.selected >= project.timeline.length - 1" :title="locked ? lockedTip : 'Déplacer le passage sélectionné vers la fin'" @click="moveSegment(state.selected, 1)">Déplacer à droite<Icon name="arrowRight" /></button>
          </span>
          <button class="mute-btn" :class="{ on: state.muteIn !== null }" :disabled="busy || locked" :title="locked ? lockedTip : muteTip" @click="muteMark"><Icon name="volumeOff" />{{ state.muteIn === null ? "Couper le son : début" : "Couper le son : fin" }}</button>
          <span class="spacer"></span>
          <span class="dim total">Durée du montage : <span class="mono">{{ formatTime(duration) }}</span></span>
          <label class="dim zoom" title="Zoom de la timeline (tout à gauche : tout le montage visible)"><Icon name="zoom" /><span>Zoom</span> <input v-model.number="state.zoom" type="range" min="0" max="400" step="5" aria-label="Zoom de la timeline" /></label>
        </div>
        <Timeline />
      </div>
      <SidePanel />
    </main>

    <main v-else-if="state.loaded" class="welcome">
      <WelcomeArt />
      <h1>moto-blur</h1>
      <p class="lead">Floutage automatique des visages, des plaques et du compteur, avec un montage simple. Tout reste sur cette machine.</p>
      <button class="primary large" @click="picker = 'open'"><Icon name="folder" />Ouvrir une vidéo ou un projet…</button>
      <ul class="points">
        <li><Icon name="face" />Visages</li>
        <li><Icon name="plate" />Plaques</li>
        <li><Icon name="gauge" />Compteur</li>
        <li><Icon name="scissors" />Montage simple</li>
        <li><Icon name="lock" />Aucun envoi en ligne</li>
      </ul>
    </main>

    <main v-else class="welcome loading">
      <span class="spinner large"></span>
      <p class="dim">Connexion au moteur…</p>
    </main>

    <Transition name="modal">
      <FilePicker v-if="picker" :title="picker === 'open' ? 'Ouvrir une vidéo ou un projet' : 'Ajouter un clip au montage'" @pick="picked" @close="picker = ''" />
    </Transition>
    <Transition name="modal">
      <JobDialog v-if="job && state.jobOpen" @review="beginReview" />
    </Transition>
    <Transition name="modal">
      <ExportDialog v-if="exporting" @close="exporting = false" />
    </Transition>
    <Transition name="modal">
      <Shortcuts v-if="panels.shortcuts" @close="panels.shortcuts = false" />
    </Transition>
    <Transition name="modal">
      <div v-if="state.pendingDash" class="modal">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dash-title">
          <h3 id="dash-title"><Icon name="target" :size="18" />Flouter un objet</h3>
          <label>Quel est cet objet ?
            <span class="row quick">
              <button v-for="n in quickNames" :key="n" type="button" class="small" :class="{ on: chosenName.toLowerCase() === n.toLowerCase() }" @click="objectName = n">{{ capital(n) }}</button>
              <input v-model="objectName" type="text" maxlength="40" spellcheck="false" placeholder="Autre nom" aria-label="Nom de l'objet" />
            </span>
          </label>
          <p v-if="replaces" class="note warn"><Icon name="alert" />« {{ chosenName }} » existe déjà dans ce clip : son flou sera remplacé à partir de cette image.</p>
          <p v-else-if="!chosenName" class="note"><Icon name="info" />Donnez un nom à l'objet pour continuer.</p>
          <p>Que faire du rectangle que vous venez de tracer ?</p>
          <div class="choices">
            <button class="primary" :disabled="!chosenName" @click="dashChoice('track')"><span>Suivre l'objet<small>Il est suivi image par image sur tout le clip, vers l'avant et l'arrière. À choisir s'il bouge dans l'image.</small></span></button>
            <button :disabled="!chosenName" @click="dashChoice('fixed')"><span>Flou fixe<small>Le même rectangle sur tout le clip. À choisir si l'objet ne bouge pas dans l'image (caméra fixée à la moto, cadrage constant).</small></span></button>
          </div>
          <div class="row end"><button @click="dashChoice('cancel')">Annuler</button></div>
        </div>
      </div>
    </Transition>
    <Transition name="modal">
      <div v-if="state.pendingBlur" class="modal">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="blur-title">
          <h3 id="blur-title"><Icon name="plus" :size="18" />Ajouter un flou</h3>
          <p>Que faire du rectangle que vous venez de tracer ?</p>
          <div class="choices">
            <button ref="followButton" class="primary" @click="blurChoice('follow')"><span>Suivre ce que j'ai entouré<small>Le flou suit ce que vous avez entouré, image par image, dans tout ce passage du montage. Il s'arrête seul à un changement de plan. À choisir pour une personne, une plaque, tout ce qui bouge dans l'image.</small></span></button>
            <button @click="blurChoice('fixed')"><span>Flou fixe<small>{{ state.pendingBlur.guided ? "Le même rectangle, immobile, pendant tout le passage vérifié." : "Le même rectangle, immobile, pendant 2 s. Vous réglerez son début et sa fin dans l'onglet « Flous »." }}</small></span></button>
          </div>
          <div class="row end"><button @click="blurChoice('cancel')">Annuler</button></div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style>
/* === 12 points : barre « Montage », vidéo verticale === */
.tools { flex-wrap: wrap; row-gap: 6px; }
.tools .zoom input { width: 84px; }
/* la durée du montage est déjà affichée sous la vidéo (« / 00:08.00 ») : elle ne s'ajoute ici
   que sur les grands écrans, pour garder la barre « Montage » sur une ligne */
@media (max-width: 1600px) { .tools .total { display: none; } }
@media (max-width: 1280px) {
  .tools button { padding: 0 8px; }
  .tools .zoom input { width: 64px; }
}
.keys-btn { font-weight: 650; font-size: 14px; color: var(--dim); }
/* Vidéo verticale : la vidéo occupe toute la hauteur ; la consigne, les barres « Lecture » et
   « Montage », la légende et la timeline se rangent dans la colonne à sa droite. */
.left.portrait {
  display: grid; grid-template-columns: var(--stage-w, 40%) minmax(0, 1fr);
  grid-template-rows: repeat(7, auto) minmax(0, 1fr); gap: 10px 16px;
}
.left.portrait > .player { display: contents; }
.left.portrait .stage { grid-column: 1; grid-row: 1 / 9; }
/* La timeline suit directement les barres ; la place restante, s'il y en a, est en bas. */
.left.portrait > .timeline { grid-column: 2; grid-row: 7; }
.left.portrait > :not(.player), .left.portrait .transport, .left.portrait .legend { grid-column: 2; min-width: 0; }
.left.portrait .timecode { flex: 1 0 150px; }
.left.portrait .legend { grid-column: 2; min-width: 0; }
.left.portrait .legend { height: auto; flex-wrap: wrap; white-space: normal; overflow: visible; }
.left.portrait .legend-tip { margin-left: 0; }
.left.portrait .transport, .left.portrait .tools { padding: 8px 10px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); }
.left.portrait .group-name { flex-basis: 100%; }
.left.portrait .guide { flex-wrap: wrap; }
.left.portrait .guide-text { flex-basis: 200px; }
</style>
