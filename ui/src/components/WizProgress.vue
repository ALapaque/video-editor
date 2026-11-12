<script setup lang="ts">
// Progression d'un traitement, affichée dans la page de l'assistant (mode simple) :
// grand pourcentage, barre, temps restant mesuré, et « Annuler » avec confirmation si demandé.
import { computed, ref } from "vue";
import { job, send } from "../store";

const props = defineProps<{ phase: string; confirm?: string; keepGoing?: string }>();
const asking = ref(false);
const percent = computed(() => (job.value ? Math.min(100, Math.round((100 * job.value.done) / Math.max(1, job.value.total))) : 0));
const downloading = computed(() => job.value?.label.startsWith("Téléchargement") ?? false);
const left = computed(() => {
  const j = job.value;
  if (!j || downloading.value) return "";
  if (j.fps <= 0 || j.done <= 0) return "Calcul du temps restant…";
  const s = Math.round((j.total - j.done) / j.fps);
  if (s > 5400) return `Temps restant : environ ${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
  if (s > 90) return `Temps restant : environ ${Math.round(s / 60)} min`;
  return s > 50 ? "Temps restant : environ 1 min" : `Temps restant : ${Math.max(1, s)} s`;
});
function cancel(): void {
  if (props.confirm && !asking.value) {
    asking.value = true;
    return;
  }
  asking.value = false;
  void send("POST", "/api/cancel");
}
defineExpose({ asking });
</script>

<template>
  <div class="wiz-progress" role="status" aria-live="off">
    <p class="wiz-percent mono">{{ percent }}<small> %</small></p>
    <span class="bar tall"><span :style="{ transform: `scaleX(${percent / 100})` }"></span></span>
    <p class="wiz-phase">{{ downloading ? "Téléchargement des modèles… (une seule fois)" : phase }}</p>
    <p class="wiz-left">{{ left || " " }}</p>
    <div v-if="asking" class="confirm wiz-confirm">
      <span class="grow">{{ confirm }}</span>
      <button @click="asking = false">{{ keepGoing ?? "Continuer" }}</button>
      <button class="danger" data-stop @click="cancel">Oui, arrêter</button>
    </div>
    <button v-else class="quiet" data-cancel @click="cancel">Annuler</button>
  </div>
</template>
