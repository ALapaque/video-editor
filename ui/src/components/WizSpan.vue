<script setup lang="ts">
// Barre de position limitée au passage affiché (mode simple : endroit signalé, alerte) : on la
// fait glisser pour se placer dans le passage, sans jamais en sortir.
// Composant à part : pendant la lecture, lui seul est redessiné à chaque image.
import { computed } from "vue";
import { time } from "../store";
import { clock } from "../wizard";

const props = defineProps<{ from: number; to: number }>();
const emit = defineEmits<{ seek: [value: number] }>();
const at = computed(() => Math.min(props.to, Math.max(props.from, time.value)));
</script>

<template>
  <span class="wiz-span">
    <input
      type="range" :min="from" :max="to" step="0.001" :value="at"
      aria-label="Position dans le passage" :aria-valuetext="clock(at)"
      @input="emit('seek', +($event.target as HTMLInputElement).value)"
    />
  </span>
</template>

<style>
.wiz-span { position: relative; flex: 1; min-width: 120px; display: flex; align-items: center; height: 26px; }
.wiz-span input[type="range"] { position: relative; width: 100%; height: 26px; margin: 0; background: transparent; }
.wiz-span input::-webkit-slider-runnable-track { height: 6px; border-radius: 3px; }
.wiz-span input::-webkit-slider-thumb { width: 18px; height: 18px; margin-top: -6px; background: var(--accent); }
.wiz-span input::-moz-range-track { height: 6px; border-radius: 3px; }
.wiz-span input::-moz-range-thumb { width: 18px; height: 18px; background: var(--accent); }
</style>
