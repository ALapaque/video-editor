<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { doubtLeft, markEntries, pending } from "../review";
import { project, start } from "../store";
import Icon from "./Icon.vue";

const emit = defineEmits<{ close: [] }>();
const p = project.value!;
const base = p.name.replace(/\.motoblur\.json$/, "").replace(/\.json$/, "");
const form = reactive({ output: `${base}_floute.mp4`, quality: 80, codec: "source", encoder: "auto", debug: false });

// Export alors que tout n'est pas vérifié : il faut le confirmer explicitement.
const confirmed = ref(false);

async function run(): Promise<void> {
  const output = form.debug && !/debug/i.test(form.output) ? form.output.replace(/(\.\w+)?$/, "_debug$1") : form.output;
  await start("/api/render", { ...form, output });
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
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="export-title">
      <h3 id="export-title"><Icon name="export" :size="18" />Exporter la vidéo</h3>
      <p v-if="p.problem" class="error">{{ p.problem }}</p>
      <div v-if="pending" class="unchecked">
        <p><Icon name="alert" /><span><strong>La vérification n'est pas terminée.</strong> {{ doubtLeft }} {{ doubtLeft > 1 ? "passages douteux ne sont pas validés" : "passage douteux n'est pas validé" }}<template v-if="markEntries.length">, {{ markEntries.length }} {{ markEntries.length > 1 ? "signalements ne sont pas traités" : "signalement n'est pas traité" }}</template>. Un visage ou une plaque peut rester lisible dans la vidéo exportée.</span></p>
        <label class="check"><input v-model="confirmed" type="checkbox" /> J'exporte quand même, sans avoir tout vérifié</label>
      </div>
      <label>Fichier de sortie (dans le dossier du projet, ou chemin complet)
        <input v-model="form.output" type="text" spellcheck="false" />
      </label>
      <label><span class="split">Qualité <span class="mono value">{{ form.quality }}</span></span>
        <input v-model.number="form.quality" type="range" min="40" max="100" step="5" />
      </label>
      <div class="cols">
        <label>Codec
          <select v-model="form.codec"><option value="source">comme la source</option><option value="h264">H.264</option><option value="hevc">H.265</option></select>
        </label>
        <label>Encodeur
          <select v-model="form.encoder"><option value="auto">automatique (matériel si possible)</option><option value="hardware">matériel uniquement</option><option value="software">logiciel uniquement</option></select>
        </label>
      </div>
      <label class="check"><input v-model="form.debug" type="checkbox" /> Vidéo de contrôle : cadres et scores dessinés, sans flou</label>
      <div class="row end">
        <button @click="emit('close')">Annuler</button>
        <button class="primary" :disabled="!!p.problem || (pending > 0 && !confirmed)" @click="run"><Icon name="export" />Exporter</button>
      </div>
    </div>
  </div>
</template>
