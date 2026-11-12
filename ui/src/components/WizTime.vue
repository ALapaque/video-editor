<script setup lang="ts">
// Position dans la vidéo, pour l'assistant du mode simple : curseur déplaçable ou simple barre.
// Composant à part : pendant la lecture, lui seul est redessiné à chaque image, pas tout l'assistant.
import { duration, time } from "../store";

defineProps<{ kind: "slider" | "bar" }>();
const emit = defineEmits<{ seek: [value: number] }>();
</script>

<template>
  <input
    v-if="kind === 'slider'" type="range" min="0" :max="duration" step="0.01" :value="time" aria-label="Moment de la vidéo"
    @input="emit('seek', +($event.target as HTMLInputElement).value)"
  />
  <span v-else class="bar still wiz-timebar"><span :style="{ transform: `scaleX(${duration ? Math.min(1, time / duration) : 0})` }"></span></span>
</template>
