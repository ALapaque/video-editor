<script setup lang="ts">
// Panneau latéral : passages à vérifier, zones de l'image courante, réglages.
import { computed, reactive, ref, watch } from "vue";
import type { Track } from "../api";
import {
  busy, current, deleteTrack, formatTime, kindLabel, notify, patchTrack, project, removeMute, saveManual,
  seekTime, send, state, time, visibleReview,
} from "../store";

const tab = ref<"review" | "zones" | "settings">("review");

// --- Vérification ---
const storageKey = computed(() => `moto-blur:verifies:${project.value?.path ?? ""}`);
const checked = reactive(new Set<string>());
watch(storageKey, (key) => {
  checked.clear();
  try {
    for (const k of JSON.parse(localStorage.getItem(key) ?? "[]")) checked.add(k);
  } catch {
    /* stockage indisponible : les coches ne sont simplement pas mémorisées */
  }
}, { immediate: true });
function toggleChecked(key: string): void {
  if (checked.has(key)) checked.delete(key);
  else checked.add(key);
  try {
    localStorage.setItem(storageKey.value, JSON.stringify([...checked]));
  } catch {
    /* idem */
  }
}
const remaining = computed(() => visibleReview.value.filter((r) => !checked.has(r.key)).length);
function jump(direction: 1 | -1): void {
  const list = visibleReview.value.filter((r) => !checked.has(r.key));
  const t = time.value;
  const target = direction > 0 ? list.find((r) => r.at - 0.4 > t + 0.05) : [...list].reverse().find((r) => r.at - 0.4 < t - 0.05);
  if (target) seekTime(Math.max(0, target.at - 0.4));
  else notify(direction > 0 ? "Plus de passage à vérifier après la tête de lecture." : "Pas de passage à vérifier avant.");
}

// --- Zones de l'image courante ---
const sourceTime = computed(() => (current.value ? state.frame / current.value.fps : 0));
const tracksHere = computed<Track[]>(() => {
  const s = current.value;
  if (!s || !project.value) return [];
  const t = sourceTime.value;
  const general = Number(project.value.settings.blur.extend_s ?? 0.3);
  return (project.value.tracks[s.clip.id] ?? [])
    .filter((tr) => {
      const before = tr.source === "manual" ? 0 : (tr.extend_before_s ?? general);
      const after = tr.source === "manual" ? 0 : (tr.extend_after_s ?? general);
      return t >= tr.start - before - 0.05 && t <= tr.end + after + 0.05;
    })
    .sort((a, b) => Number(a.disabled) - Number(b.disabled) || a.id - b.id);
});
function isSelected(tr: Track): boolean {
  return state.selectedTrack?.id === tr.id && state.selectedTrack.clip === current.value?.clip.id;
}
function select(tr: Track): void {
  if (current.value) state.selectedTrack = { clip: current.value.clip.id, id: tr.id };
}
async function extend(tr: Track, side: "extend_before_s" | "extend_after_s", delta: number): Promise<void> {
  const general = Number(project.value?.settings.blur.extend_s ?? 0.3);
  const value = Math.max(0, Math.round(((tr[side] ?? general) + delta) * 100) / 100);
  await patchTrack(current.value!.clip.id, tr.id, { [side]: value });
}
async function setEdge(tr: Track, edge: "start" | "end"): Promise<void> {
  if (!tr.keyframes || !current.value) return;
  const t = sourceTime.value;
  const frames = tr.keyframes.map((k) => [...k]);
  const inside = edge === "start" ? frames.filter((k) => k[0] > t) : frames.filter((k) => k[0] < t);
  const anchor = edge === "start" ? frames[0] : frames[frames.length - 1];
  if (!inside.length) {
    notify(edge === "start" ? "Le début doit être avant la fin de la zone." : "La fin doit être après le début de la zone.");
    return;
  }
  const moved = [t, ...anchor.slice(1)];
  await saveManual(current.value.clip.id, edge === "start" ? [moved, ...inside] : [...inside, moved], tr.id);
}
function redraw(tr: Track): void {
  select(tr);
  state.tool = "redraw";
}
const mutesList = computed(() => (project.value?.mutes ?? []).map((m, index) => ({ ...m, index })));

// --- Réglages ---
const blur = reactive<Record<string, string | number | boolean>>({});
const analysis = reactive<Record<string, string | number | boolean | string[]>>({});
watch(() => project.value?.settings, (settings) => {
  if (!settings) return;
  Object.assign(blur, settings.blur);
  Object.assign(analysis, settings.analysis);
}, { immediate: true, deep: true });
async function saveBlur(): Promise<void> {
  await send("PUT", "/api/settings", { blur: { ...blur } });
}
async function saveAnalysis(): Promise<void> {
  const { kinds: _kinds, ...rest } = analysis;
  if (await send("PUT", "/api/settings", { analysis: rest })) notify("Réglages d'analyse enregistrés. Relancez « Analyser » : toute l'analyse sera refaite.");
}
</script>

<template>
  <aside class="side">
    <nav class="tabs">
      <button :class="{ on: tab === 'review' }" @click="tab = 'review'">À vérifier <b v-if="remaining">{{ remaining }}</b></button>
      <button :class="{ on: tab === 'zones' }" @click="tab = 'zones'">Zones</button>
      <button :class="{ on: tab === 'settings' }" @click="tab = 'settings'">Réglages</button>
    </nav>

    <section v-if="tab === 'review'" class="pane">
      <p class="help">Passages où le logiciel n'est pas sûr de lui. Ils sont floutés quand même ; regardez-les image par image. Un objet jamais détecté n'apparaît pas ici.</p>
      <div class="row">
        <button @click="jump(-1)">◀ Précédent</button>
        <button @click="jump(1)">Suivant ▶</button>
      </div>
      <p v-if="!visibleReview.length" class="dim">Aucun passage douteux dans le montage.</p>
      <ul class="list">
        <li v-for="r in visibleReview" :key="r.key + r.at" :class="{ done: checked.has(r.key), here: time >= r.at - 0.5 && time <= r.end + 0.2 }">
          <input type="checkbox" :checked="checked.has(r.key)" title="Marquer comme vérifié" @change="toggleChecked(r.key)" />
          <a href="#" @click.prevent="seekTime(Math.max(0, r.at - 0.4))">
            <strong>{{ formatTime(r.at, 1) }}</strong>
            <span class="kinds">{{ r.item.kinds.map((k) => kindLabel[k] ?? k).join(" + ") }}</span>
            <small>{{ r.item.reasons.join(" ; ") }}</small>
          </a>
        </li>
      </ul>
    </section>

    <section v-else-if="tab === 'zones'" class="pane">
      <div class="row">
        <button :class="{ on: state.tool === 'manual' }" :disabled="busy" title="Tracer un rectangle à flouter sur l'image courante" @click="state.tool = state.tool === 'manual' ? 'none' : 'manual'">+ Zone manuelle</button>
        <button :class="{ on: state.tool === 'dash' }" :disabled="busy" title="Désigner le compteur sur l'image courante" @click="state.tool = state.tool === 'dash' ? 'none' : 'dash'">Compteur…</button>
      </div>
      <p class="help">Zones actives sur l'image affichée. Cliquez une zone dans l'image pour la sélectionner.</p>
      <p v-if="!tracksHere.length" class="dim">Aucune zone sur cette image.</p>
      <ul class="list zones">
        <li v-for="tr in tracksHere" :key="tr.id" :class="{ selected: isSelected(tr), off: tr.disabled }" @click="select(tr)">
          <div class="head">
            <span class="dot" :class="tr.kind"></span>
            <strong>{{ kindLabel[tr.kind] ?? tr.kind }}</strong>
            <span class="dim">n° {{ tr.id }}<template v-if="tr.source === 'auto'"> · score {{ tr.score.toFixed(2) }}</template><template v-else-if="tr.source === 'manual'"> · manuelle</template><template v-else> · suivi</template></span>
          </div>
          <div class="dim">source {{ formatTime(tr.start, 1) }} → {{ formatTime(tr.end, 1) }}</div>
          <div v-if="isSelected(tr)" class="actions">
            <template v-if="tr.source === 'manual'">
              <button :disabled="busy" @click.stop="setEdge(tr, 'start')">Début = cette image</button>
              <button :disabled="busy" @click.stop="setEdge(tr, 'end')">Fin = cette image</button>
              <button :disabled="busy" title="Retracer le rectangle sur l'image courante : la zone se déplacera progressivement jusqu'ici" @click.stop="redraw(tr)">Déplacer ici…</button>
              <button class="danger" :disabled="busy" @click.stop="deleteTrack(current!.clip.id, tr.id)">Supprimer</button>
            </template>
            <template v-else>
              <div class="stepper">Avant
                <button :disabled="busy" @click.stop="extend(tr, 'extend_before_s', -0.5)">−</button>
                <span>{{ (tr.extend_before_s ?? Number(blur.extend_s)).toFixed(1) }} s</span>
                <button :disabled="busy" @click.stop="extend(tr, 'extend_before_s', 0.5)">+</button>
              </div>
              <div class="stepper">Après
                <button :disabled="busy" @click.stop="extend(tr, 'extend_after_s', -0.5)">−</button>
                <span>{{ (tr.extend_after_s ?? Number(blur.extend_s)).toFixed(1) }} s</span>
                <button :disabled="busy" @click.stop="extend(tr, 'extend_after_s', 0.5)">+</button>
              </div>
              <button v-if="!tr.disabled" class="danger" :disabled="busy" title="Faux positif : ne pas flouter cette zone" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: true })">Ne pas flouter</button>
              <button v-else :disabled="busy" @click.stop="patchTrack(current!.clip.id, tr.id, { disabled: false })">Rétablir le flou</button>
              <button v-if="tr.source === 'tracker'" class="danger" :disabled="busy" @click.stop="deleteTrack(current!.clip.id, tr.id)">Supprimer le suivi</button>
            </template>
          </div>
        </li>
      </ul>
      <template v-if="mutesList.length">
        <h4>Passages muets</h4>
        <ul class="list">
          <li v-for="m in mutesList" :key="m.index">
            <span>{{ m.clip }} · source {{ formatTime(m.start, 1) }} → {{ formatTime(m.end, 1) }}</span>
            <button class="danger" @click="removeMute(m.index)">Retirer</button>
          </li>
        </ul>
      </template>
    </section>

    <section v-else class="pane">
      <h4>Flou</h4>
      <p class="help">Appliqué au prochain rendu, sans refaire l'analyse.</p>
      <label>Marge autour des zones <input v-model.number="blur.margin" type="number" min="0" max="2" step="0.05" @change="saveBlur" /></label>
      <label>Prolongation avant / après (s) <input v-model.number="blur.extend_s" type="number" min="0" max="5" step="0.1" @change="saveBlur" /></label>
      <label>Type de flou
        <select v-model="blur.style" @change="saveBlur"><option value="pixelate">mosaïque lissée</option><option value="solid">aplat de couleur</option></select>
      </label>
      <label>Forme pour les visages
        <select v-model="blur.face_shape" @change="saveBlur"><option value="ellipse">ellipse</option><option value="rounded">rectangle arrondi</option><option value="rect">rectangle</option></select>
      </label>
      <label>Forme pour les plaques
        <select v-model="blur.plate_shape" @change="saveBlur"><option value="rounded">rectangle arrondi</option><option value="rect">rectangle</option><option value="ellipse">ellipse</option></select>
      </label>
      <h4>Détection</h4>
      <p class="help">Modifier ces réglages refait toute l'analyse.</p>
      <label>Modèle de visages
        <select v-model="analysis.face_model"><option>yolo11n-face</option><option>yolo11s-face</option><option>yolo11m-face</option></select>
      </label>
      <label>Modèle de plaques
        <select v-model="analysis.plate_model"><option>yolo-v9-t-640</option><option>yolo-v9-s-608</option></select>
      </label>
      <label>Score minimal pour suivre un objet <input v-model.number="analysis.conf_new" type="number" min="0.01" max="0.9" step="0.05" /></label>
      <label>Taille des tuiles (0 = auto, -1 = aucune) <input v-model.number="analysis.tile_size" type="number" min="-1" step="64" /></label>
      <label>Détecter une image sur <input v-model.number="analysis.detect_every" type="number" min="1" max="10" step="1" /></label>
      <button :disabled="busy" @click="saveAnalysis">Enregistrer les réglages de détection</button>
    </section>
  </aside>
</template>
