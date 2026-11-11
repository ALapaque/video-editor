<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import ExportDialog from "./components/ExportDialog.vue";
import FilePicker from "./components/FilePicker.vue";
import Player from "./components/Player.vue";
import SidePanel from "./components/SidePanel.vue";
import Timeline from "./components/Timeline.vue";
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
      <strong class="brand">moto-blur</strong>
      <span v-if="project" class="dim" :title="project.path">{{ project.name }}</span>
      <span class="spacer"></span>
      <template v-if="job && job.state === 'running'">
        <span class="job">
          {{ job.label }} — {{ percent }} %<template v-if="job.fps"> · {{ job.fps }} i/s</template><template v-if="eta"> · {{ eta }}</template>
          <span class="bar"><span :style="{ width: `${percent}%` }"></span></span>
        </span>
        <button @click="send('POST', '/api/cancel')">Annuler</button>
      </template>
      <button :disabled="busy" @click="picker = 'open'">Ouvrir…</button>
      <template v-if="project">
        <button :disabled="busy" @click="picker = 'add'">Ajouter un clip…</button>
        <button :class="{ attention: needsAnalysis }" :disabled="busy || !segments.length" title="Détecter les visages et les plaques dans les passages gardés qui ne l'ont pas encore été" @click="start('/api/analyze')">
          {{ needsAnalysis ? "Analyser" : "Analyse à jour" }}
        </button>
        <button class="primary" :disabled="busy || !segments.length" @click="exporting = true">Exporter…</button>
      </template>
    </header>

    <div v-if="state.toast" class="toast" :class="state.toastKind" @click="state.toast = ''">{{ state.toast }} <span class="dim">(cliquer pour fermer)</span></div>

    <main v-if="project" class="work">
      <div class="left">
        <div v-if="missing.length" class="banner error">Fichier source introuvable : {{ missing.map((c) => c.path).join(", ") }}</div>
        <div v-else-if="project.mismatch && !project.conform" class="banner">
          Les clips du montage n'ont pas tous la même résolution ou la même fréquence d'images. Comment les assembler ?
          <span class="row">
            <button :disabled="busy" @click="send('PUT', '/api/settings', { conform: 'first' })">Aligner sur le premier clip</button>
            <button :disabled="busy" @click="send('PUT', '/api/settings', { conform: 'largest' })">Aligner sur la plus grande résolution et la plus haute fréquence</button>
          </span>
          <small class="dim">Mise à l'échelle avec bandes noires si le rapport d'image diffère ; images dupliquées ou supprimées si la fréquence diffère.</small>
        </div>
        <div v-else-if="needsAnalysis && !busy" class="banner">Des passages gardés n'ont pas encore été analysés : ils sortiraient sans flou. Cliquez « Analyser ».</div>
        <Player />
        <div class="tools">
          <button :disabled="busy" title="Couper le passage à la tête de lecture (C)" @click="splitAtPlayhead">✂ Couper ici</button>
          <button :disabled="busy || state.selected < 0" title="Supprimer le passage sélectionné (Suppr)" @click="deleteSegment(state.selected)">Supprimer le passage</button>
          <button :disabled="busy || state.selected <= 0" title="Déplacer le passage sélectionné vers le début" @click="moveSegment(state.selected, -1)">◀ Déplacer</button>
          <button :disabled="busy || state.selected < 0 || state.selected >= project.timeline.length - 1" title="Déplacer le passage sélectionné vers la fin" @click="moveSegment(state.selected, 1)">Déplacer ▶</button>
          <button :class="{ on: state.muteIn !== null }" :disabled="busy" title="Poser le début puis la fin d'un passage muet (M)" @click="muteMark">{{ state.muteIn === null ? "🔇 Muet : début" : "🔇 Muet : fin" }}</button>
          <span class="spacer"></span>
          <span class="dim">Durée du montage : {{ formatTime(duration) }}</span>
          <label class="dim">Zoom <input v-model.number="state.zoom" type="range" min="0" max="400" step="5" title="0 = tout le montage visible" /></label>
        </div>
        <Timeline />
      </div>
      <SidePanel />
    </main>

    <main v-else-if="state.loaded" class="welcome">
      <h1>moto-blur</h1>
      <p>Floutage automatique des visages, des plaques et du compteur, avec un montage simple. Tout reste sur cette machine.</p>
      <button class="primary" @click="picker = 'open'">Ouvrir une vidéo ou un projet…</button>
    </main>

    <FilePicker v-if="picker" :title="picker === 'open' ? 'Ouvrir une vidéo ou un projet' : 'Ajouter un clip au montage'" @pick="picked" @close="picker = ''" />
    <ExportDialog v-if="exporting" @close="exporting = false" />
    <div v-if="state.pendingDash" class="modal">
      <div class="dialog">
        <h3>Flouter le compteur</h3>
        <p>Que faire du rectangle que vous venez de tracer ?</p>
        <div class="choices">
          <button class="primary" @click="dashChoice('track')">Suivre le compteur<small>Il est suivi image par image sur tout le clip, vers l'avant et l'arrière. À choisir si le cadrage bouge.</small></button>
          <button @click="dashChoice('fixed')">Zone fixe<small>Le même rectangle sur tout le clip. À choisir si la caméra est fixée à la moto et le cadrage constant.</small></button>
        </div>
        <div class="row end"><button @click="dashChoice('cancel')">Annuler</button></div>
      </div>
    </div>
  </div>
</template>
