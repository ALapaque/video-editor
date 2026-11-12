<script setup lang="ts">
// Mode avancé : toute l'interface de montage (timeline, panneau latéral, dialogues).
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ExportDialog from "./ExportDialog.vue";
import FilePicker from "./FilePicker.vue";
import JobDialog from "./JobDialog.vue";
import Icon from "./Icon.vue";
import Logo from "./Logo.vue";
import Player from "./Player.vue";
import SidePanel from "./SidePanel.vue";
import Timeline from "./Timeline.vue";
import WelcomeArt from "./WelcomeArt.vue";
import { addMark, doubtDone, doubtEntries, doubtLeft, guided, leave, markEntries, pending, review, startQueue, summary, validate } from "../review";
import {
  addMute, busy, deleteSegment, duration, formatTime, job, moveSegment, needsAnalysis, notify, project, refresh, segments, send,
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
    notify("Début du passage muet posé. Placez la tête de lecture à la fin puis cliquez « Muet : fin ».");
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

/** Vers le mode simple : la vérification guidée en cours est quittée, le projet reste ouvert. */
function toSimple(): void {
  leave();
  state.tool = "none";
  state.pendingDash = null;
  setMode("simple");
}

function onKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement;
  if (event.key === "Escape" && state.pendingDash) {
    void dashChoice("cancel");
    return;
  }
  if (target.tagName === "INPUT" || target.tagName === "SELECT" || busy.value) return;
  if (picker.value || exporting.value || state.pendingDash || state.jobOpen) return; // un dialogue est ouvert
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

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
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
      <div class="left">
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
            <button v-else-if="guide.action === 'analyse'" class="primary" title="Détecter les visages et les plaques dans les passages gardés qui ne l'ont pas encore été" @click="analyse"><Icon name="scan" />Analyser</button>
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
        <Player />
        <div class="tools">
          <button :disabled="busy || locked" :title="locked ? lockedTip : 'Couper le passage à la tête de lecture (C)'" @click="splitAtPlayhead"><Icon name="scissors" />Couper ici</button>
          <button :disabled="busy || locked || state.selected < 0" :title="locked ? lockedTip : 'Supprimer le passage sélectionné (Suppr)'" @click="deleteSegment(state.selected)"><Icon name="trash" />Supprimer le passage</button>
          <span class="joined">
            <button :disabled="busy || locked || state.selected <= 0" :title="locked ? lockedTip : 'Déplacer le passage sélectionné vers le début'" @click="moveSegment(state.selected, -1)"><Icon name="arrowLeft" />Déplacer</button>
            <button :disabled="busy || locked || state.selected < 0 || state.selected >= project.timeline.length - 1" :title="locked ? lockedTip : 'Déplacer le passage sélectionné vers la fin'" @click="moveSegment(state.selected, 1)">Déplacer<Icon name="arrowRight" /></button>
          </span>
          <button class="mute-btn" :class="{ on: state.muteIn !== null }" :disabled="busy || locked" :title="locked ? lockedTip : 'Poser le début puis la fin d\'un passage muet (M)'" @click="muteMark"><Icon name="volumeOff" />{{ state.muteIn === null ? "Muet : début" : "Muet : fin" }}</button>
          <span class="spacer"></span>
          <span class="dim total">Durée du montage : <span class="mono">{{ formatTime(duration) }}</span></span>
          <label class="dim zoom"><Icon name="zoom" /><span>Zoom</span> <input v-model.number="state.zoom" type="range" min="0" max="400" step="5" title="0 = tout le montage visible" /></label>
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
      <div v-if="state.pendingDash" class="modal">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dash-title">
          <h3 id="dash-title"><Icon name="target" :size="18" />Flouter un objet</h3>
          <label>Quel est cet objet ?
            <span class="row quick">
              <button v-for="n in quickNames" :key="n" type="button" class="small" :class="{ on: chosenName.toLowerCase() === n.toLowerCase() }" @click="objectName = n">{{ capital(n) }}</button>
              <input v-model="objectName" type="text" maxlength="40" spellcheck="false" placeholder="Autre nom" aria-label="Nom de l'objet" />
            </span>
          </label>
          <p v-if="replaces" class="note warn"><Icon name="alert" />« {{ chosenName }} » existe déjà dans ce clip : ses zones seront remplacées à partir de cette image.</p>
          <p v-else-if="!chosenName" class="note"><Icon name="info" />Donnez un nom à l'objet pour continuer.</p>
          <p>Que faire du rectangle que vous venez de tracer ?</p>
          <div class="choices">
            <button class="primary" :disabled="!chosenName" @click="dashChoice('track')"><span>Suivre l'objet<small>Il est suivi image par image sur tout le clip, vers l'avant et l'arrière. À choisir s'il bouge dans l'image.</small></span></button>
            <button :disabled="!chosenName" @click="dashChoice('fixed')"><span>Zone fixe<small>Le même rectangle sur tout le clip. À choisir si l'objet ne bouge pas dans l'image (caméra fixée à la moto, cadrage constant).</small></span></button>
          </div>
          <div class="row end"><button @click="dashChoice('cancel')">Annuler</button></div>
        </div>
      </div>
    </Transition>
  </div>
</template>
