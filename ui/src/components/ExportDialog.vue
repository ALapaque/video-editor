<script lang="ts">
// Les choix du dialogue sont gardés tant que le même projet reste ouvert : après un essai sur un
// extrait, on retrouve le nom du fichier et le format pour lancer l'export complet.
import { reactive } from "vue";
import type { ExportPreset } from "../api";

const kept = reactive({
  path: "", output: "", preset: "source" as ExportPreset, custom: false, quality: 80, codec: "source", encoder: "auto", debug: false, details: false,
});
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { RenderOptions } from "../api";
import { exportPresets, render, renderExtract } from "../extras";
import { allClear, remaining } from "../review";
import { duration, formatTime, project, time } from "../store";
import Icon from "./Icon.vue";

const emit = defineEmits<{ close: [] }>();
const p = project.value!;
if (kept.path !== p.path) {
  const base = p.name.replace(/\.motoblur\.json$/, "").replace(/\.json$/, "");
  Object.assign(kept, { path: p.path, output: `${base}_floute.mp4`, preset: "source", custom: false, quality: 80, codec: "source", encoder: "auto", debug: false, details: false });
}
const form = kept;
const presetText = computed(() => exportPresets.find((e) => e.id === form.preset)?.text ?? "");

// Export alors qu'il reste quelque chose (relecture non faite, alertes, signalements) : il faut
// le confirmer explicitement. Si rien ne reste, rien n'est demandé.
const confirmed = ref(false);

/** Réglages envoyés au moteur : le format choisi, et la qualité ou le codec seulement s'ils ont été réglés à la main. */
function options(): RenderOptions {
  const output = form.debug && !/debug/i.test(form.output) ? form.output.replace(/(\.\w+)?$/, "_debug$1") : form.output;
  return {
    output, preset: form.preset, encoder: form.encoder, debug: form.debug,
    ...(form.custom ? { quality: form.quality, codec: form.codec } : {}),
  };
}

async function run(): Promise<void> {
  await render(options());
  emit("close");
}

/** Essai : 10 s autour de la tête de lecture, avec exactement les réglages de l'export complet.
 *  Près de la fin du montage, l'extrait est reculé pour durer quand même 10 s. */
const around = computed(() => Math.max(0, Math.min(time.value, duration.value - 5)));
async function tryExtract(): Promise<void> {
  const { output, ...rest } = options();
  await renderExtract(output, around.value, 10, rest);
  emit("close");
}

function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div class="dialog export-dialog" role="dialog" aria-modal="true" aria-labelledby="export-title">
      <h3 id="export-title"><Icon name="export" :size="18" />Exporter la vidéo</h3>
      <p v-if="p.problem" class="error">{{ p.problem }}</p>
      <div v-if="!allClear" class="unchecked">
        <p><Icon name="alert" /><span><strong>Avant d'exporter, il reste :</strong></span></p>
        <ul class="unchecked-list">
          <li v-if="!remaining.swept" data-left="sweep"><strong>La relecture n'est pas faite.</strong> Regarder la vidéo une fois est le seul moyen de voir ce que le logiciel n'a jamais détecté.</li>
          <li v-if="remaining.marks" data-left="marks"><strong>{{ remaining.marks > 1 ? `${remaining.marks} signalements ne sont pas traités` : "1 signalement n'est pas traité" }}.</strong> Vous y avez vu quelque chose de lisible.</li>
          <li v-if="remaining.risks" data-left="alerts"><strong>{{ remaining.risks > 1 ? `${remaining.risks} alertes ne sont pas regardées` : "1 alerte n'est pas regardée" }}.</strong> À ces endroits, quelque chose peut rester visible.</li>
        </ul>
        <label class="check"><input v-model="confirmed" type="checkbox" /> J'exporte quand même</label>
      </div>
      <label>Fichier de sortie (dans le dossier du projet, ou chemin complet)
        <input v-model="form.output" type="text" spellcheck="false" />
      </label>
      <label>Format de la vidéo
        <select v-model="form.preset" aria-describedby="export-preset-text">
          <option v-for="e in exportPresets" :key="e.id" :value="e.id">{{ e.label }}</option>
        </select>
        <span id="export-preset-text" class="preset-text">{{ presetText }}</span>
      </label>
      <div class="fold" :class="{ open: form.details }">
        <button class="fold-head" type="button" :aria-expanded="form.details" @click="form.details = !form.details">
          <Icon :name="form.details ? 'chevronDown' : 'chevronRight'" :size="14" />Réglages détaillés
        </button>
        <template v-if="form.details">
          <label class="check"><input v-model="form.custom" type="checkbox" /> Régler moi-même la qualité et le codec (sinon : ceux du format choisi)</label>
          <template v-if="form.custom">
            <label><span class="split">Qualité <span class="mono value">{{ form.quality }}</span></span>
              <input v-model.number="form.quality" type="range" min="40" max="100" step="5" />
            </label>
            <label>Codec
              <select v-model="form.codec"><option value="source">comme la source</option><option value="h264">H.264</option><option value="hevc">H.265</option></select>
            </label>
          </template>
          <label>Encodeur
            <select v-model="form.encoder"><option value="auto">automatique (matériel si possible)</option><option value="hardware">matériel uniquement</option><option value="software">logiciel uniquement</option></select>
          </label>
          <label class="check"><input v-model="form.debug" type="checkbox" /> Vidéo de contrôle : contours des flous dessinés (avec leur score) à la place du flou</label>
        </template>
      </div>
      <div class="try">
        <button :disabled="!!p.problem" title="Exporte seulement 10 secondes, autour de la tête de lecture, avec ces réglages" @click="tryExtract"><Icon name="film" />Essayer sur un extrait de 10 s</button>
        <span>Autour de <span class="mono">{{ formatTime(around, 0) }}</span>, pour vérifier le résultat réel avant le rendu complet.</span>
      </div>
      <div class="row end">
        <button @click="emit('close')">Annuler</button>
        <button class="primary" :disabled="!!p.problem || (!allClear && !confirmed)" @click="run"><Icon name="export" />Exporter</button>
      </div>
    </div>
  </div>
</template>

<style>
/* === nouveau flux : ce qui reste avant d'exporter === */
.unchecked-list { margin: -3px 0 0; padding: 0 0 0 41px; display: flex; flex-direction: column; gap: 4px; color: var(--dim); font-size: 12.5px; line-height: 1.45; }
.unchecked-list strong { color: var(--text); font-weight: 600; }
</style>
