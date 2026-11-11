<script setup lang="ts">
import { reactive } from "vue";
import { project, start } from "../store";

const emit = defineEmits<{ close: [] }>();
const p = project.value!;
const base = p.name.replace(/\.motoblur\.json$/, "").replace(/\.json$/, "");
const form = reactive({ output: `${base}_floute.mp4`, quality: 80, codec: "source", encoder: "auto", debug: false });

async function run(): Promise<void> {
  const output = form.debug && !/debug/i.test(form.output) ? form.output.replace(/(\.\w+)?$/, "_debug$1") : form.output;
  await start("/api/render", { ...form, output });
  emit("close");
}
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div class="dialog">
      <h3>Exporter la vidéo</h3>
      <p v-if="p.problem" class="error">{{ p.problem }}</p>
      <label>Fichier de sortie (dans le dossier du projet, ou chemin complet)
        <input v-model="form.output" type="text" spellcheck="false" />
      </label>
      <label>Qualité : {{ form.quality }}
        <input v-model.number="form.quality" type="range" min="40" max="100" step="5" />
      </label>
      <label>Codec
        <select v-model="form.codec"><option value="source">comme la source</option><option value="h264">H.264</option><option value="hevc">H.265</option></select>
      </label>
      <label>Encodeur
        <select v-model="form.encoder"><option value="auto">automatique (matériel si possible)</option><option value="hardware">matériel uniquement</option><option value="software">logiciel uniquement</option></select>
      </label>
      <label class="check"><input v-model="form.debug" type="checkbox" /> Vidéo de contrôle : cadres et scores dessinés, sans flou</label>
      <div class="row end">
        <button @click="emit('close')">Annuler</button>
        <button class="primary" :disabled="!!p.problem" @click="run">Exporter</button>
      </div>
    </div>
  </div>
</template>
