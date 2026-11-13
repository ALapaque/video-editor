<script setup lang="ts">
// Préréglage d'analyse : Normal, Conditions difficiles, Rapide. Changer de préréglage oblige à
// refaire l'analyse : c'est dit avant, et confirmé quand une analyse existe déjà.
// `compact` : sur une ligne, sous la consigne, là où on lance l'analyse.
import { computed, ref, watch } from "vue";
import type { Preset } from "../api";
import { choosablePresets, presetInfo, setPreset } from "../extras";
import { busy, notify, project, segments } from "../store";
import Icon from "./Icon.vue";

defineProps<{ compact?: boolean }>();

const presets = choosablePresets;
const current = computed<Preset>(() => project.value?.preset ?? "normal");
const choice = ref<Preset>(current.value);
const confirming = ref(false);
watch(current, (value) => {
  choice.value = value;
  confirming.value = false;
});

/** Une analyse a déjà été faite : la changer de préréglage la rend inutilisable. */
const analysed = computed(() => {
  const p = project.value;
  if (!p || !segments.value.length) return false;
  return Object.values(p.tracks).some((list) => list.some((t) => t.source === "auto")) || p.clips.some((c) => !c.needs_analysis);
});

async function apply(): Promise<void> {
  const wanted = choice.value;
  const redo = analysed.value;
  confirming.value = false;
  await setPreset(wanted);
  if (project.value?.preset !== wanted) {
    choice.value = current.value; // refusé par le moteur : le message d'erreur est déjà affiché
    return;
  }
  notify(redo
    ? `Préréglage « ${presetInfo[wanted].label} » choisi. Relancez « Analyser » : toute l'analyse sera refaite.`
    : `Préréglage « ${presetInfo[wanted].label} » choisi.`);
}

function pick(): void {
  if (choice.value === current.value) confirming.value = false;
  else if (analysed.value) confirming.value = true;
  else void apply();
}

function keep(): void {
  choice.value = current.value;
  confirming.value = false;
}
</script>

<template>
  <div class="preset" :class="{ compact }">
    <label class="preset-pick">
      <span>Préréglage d'analyse</span>
      <select v-model="choice" :disabled="busy" aria-label="Préréglage d'analyse" title="Changer de préréglage oblige à refaire l'analyse" @change="pick">
        <option v-if="current === 'perso'" value="perso" disabled>{{ presetInfo.perso.label }}</option>
        <option v-for="p in presets" :key="p" :value="p">{{ presetInfo[p].label }}</option>
      </select>
    </label>
    <p class="preset-text">{{ presetInfo[choice].text }}</p>
    <p v-if="presetInfo[choice].warning" class="preset-warning"><Icon name="alert" /><span>{{ presetInfo[choice].warning }}</span></p>
    <div v-if="confirming" class="preset-confirm" role="alert">
      <span class="grow">Changer de préréglage oblige à refaire toute l'analyse : celle qui est déjà faite ne servira plus. Les scores minimaux de flou reviennent aussi à ceux du préréglage.</span>
      <button class="small" @click="keep">Garder « {{ presetInfo[current].label }} »</button>
      <button class="small attention" :disabled="busy" @click="apply">Changer de préréglage</button>
    </div>
    <p v-else-if="!compact" class="help">Changer de préréglage oblige à refaire toute l'analyse.</p>
  </div>
</template>
