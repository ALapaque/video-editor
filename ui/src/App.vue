<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import ExportDialog from "./components/ExportDialog.vue";
import FilePicker from "./components/FilePicker.vue";
import Icon from "./components/Icon.vue";
import Logo from "./components/Logo.vue";
import Player from "./components/Player.vue";
import SidePanel from "./components/SidePanel.vue";
import Timeline from "./components/Timeline.vue";
import WelcomeArt from "./components/WelcomeArt.vue";
import {
  addMute, busy, deleteSegment, duration, formatTime, job, moveSegment, notify, project, refresh, segments, send,
  splitAtPlayhead, start, state, time,
} from "./store";

const picker = ref<"" | "open" | "add">("");
const exporting = ref(false);
const needsAnalysis = computed(() => project.value?.clips.some((c) => c.needs_analysis) ?? false);
const missing = computed(() => project.value?.clips.filter((c) => c.missing) ?? []);
const percent = computed(() => (job.value ? Math.min(100, Math.round((100 * job.value.done) / Math.max(1, job.value.total))) : 0));
const eta = computed(() => {
  const j = job.value;
  if (!j || j.fps <= 0) return "";
  const s = Math.round((j.total - j.done) / j.fps);
  return s > 90 ? `reste ${Math.round(s / 60)} min` : `reste ${s} s`;
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

async function dashChoice(mode: "track" | "fixed" | "cancel"): Promise<void> {
  const pending = state.pendingDash;
  state.pendingDash = null;
  if (!pending || mode === "cancel") return;
  await start("/api/dash", { ...pending, mode, direction: "both" });
}

function onKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement;
  if (event.key === "Escape" && state.pendingDash) {
    void dashChoice("cancel");
    return;
  }
  if (target.tagName === "INPUT" || target.tagName === "SELECT" || busy.value) return;
  if (event.key === "c" || event.key === "C") void splitAtPlayhead();
  else if ((event.key === "Delete" || event.key === "Backspace") && state.selected >= 0) void deleteSegment(state.selected);
  else if (event.key === "m" || event.key === "M") muteMark();
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  void refresh();
});
</script>

<template>
  <div class="app">
    <header class="top">
      <span class="brand"><Logo /><strong>moto-blur</strong></span>
      <span v-if="project" class="project" :title="project.path">{{ project.name }}</span>
      <span class="spacer"></span>
      <Transition name="fade">
        <div v-if="job && job.state === 'running'" class="job-wrap">
          <div class="job" role="status">
            <span class="job-text">
              <span class="job-label">{{ job.label }}</span>
              <span class="job-num">{{ percent }} %</span>
              <span class="job-meta"><template v-if="job.fps">{{ job.fps }} i/s</template><template v-if="job.fps && eta"> · </template><template v-if="eta">{{ eta }}</template></span>
            </span>
            <span class="bar"><span :style="{ transform: `scaleX(${percent / 100})` }"></span></span>
          </div>
          <button @click="send('POST', '/api/cancel')">Annuler</button>
        </div>
      </Transition>
      <button :disabled="busy" @click="picker = 'open'"><Icon name="folder" />Ouvrir…</button>
      <template v-if="project">
        <button :disabled="busy" @click="picker = 'add'"><Icon name="plus" />Ajouter un clip…</button>
        <button :class="{ attention: needsAnalysis, quiet: !needsAnalysis }" :disabled="busy || !segments.length" title="Détecter les visages et les plaques dans les passages gardés qui ne l'ont pas encore été" @click="start('/api/analyze')">
          <Icon :name="needsAnalysis ? 'scan' : 'check'" />{{ needsAnalysis ? "Analyser" : "Analyse à jour" }}
        </button>
        <button class="primary" :disabled="busy || !segments.length" @click="exporting = true"><Icon name="export" />Exporter…</button>
      </template>
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
        <div v-else-if="needsAnalysis && !busy" class="banner"><Icon name="alert" /><div>Des passages gardés n'ont pas encore été analysés : ils sortiraient sans flou. Cliquez « Analyser ».</div></div>
        <Player />
        <div class="tools">
          <button :disabled="busy" title="Couper le passage à la tête de lecture (C)" @click="splitAtPlayhead"><Icon name="scissors" />Couper ici</button>
          <button :disabled="busy || state.selected < 0" title="Supprimer le passage sélectionné (Suppr)" @click="deleteSegment(state.selected)"><Icon name="trash" />Supprimer le passage</button>
          <span class="joined">
            <button :disabled="busy || state.selected <= 0" title="Déplacer le passage sélectionné vers le début" @click="moveSegment(state.selected, -1)"><Icon name="arrowLeft" />Déplacer</button>
            <button :disabled="busy || state.selected < 0 || state.selected >= project.timeline.length - 1" title="Déplacer le passage sélectionné vers la fin" @click="moveSegment(state.selected, 1)">Déplacer<Icon name="arrowRight" /></button>
          </span>
          <button class="mute-btn" :class="{ on: state.muteIn !== null }" :disabled="busy" title="Poser le début puis la fin d'un passage muet (M)" @click="muteMark"><Icon name="volumeOff" />{{ state.muteIn === null ? "Muet : début" : "Muet : fin" }}</button>
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
      <ExportDialog v-if="exporting" @close="exporting = false" />
    </Transition>
    <Transition name="modal">
      <div v-if="state.pendingDash" class="modal">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dash-title">
          <h3 id="dash-title"><Icon name="gauge" :size="18" />Flouter le compteur</h3>
          <p>Que faire du rectangle que vous venez de tracer ?</p>
          <div class="choices">
            <button class="primary" @click="dashChoice('track')"><span>Suivre le compteur<small>Il est suivi image par image sur tout le clip, vers l'avant et l'arrière. À choisir si le cadrage bouge.</small></span></button>
            <button @click="dashChoice('fixed')"><span>Zone fixe<small>Le même rectangle sur tout le clip. À choisir si la caméra est fixée à la moto et le cadrage constant.</small></span></button>
          </div>
          <div class="row end"><button @click="dashChoice('cancel')">Annuler</button></div>
        </div>
      </div>
    </Transition>
  </div>
</template>
