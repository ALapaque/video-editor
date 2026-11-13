<script setup lang="ts">
// Barre de position limitée au passage vérifié (mode simple) : on la fait glisser pour se placer
// dans le passage, sans jamais en sortir. La partie où se trouve le flou montré est éclairée.
// Composant à part : pendant la lecture, lui seul est redessiné à chaque image.
import { computed } from "vue";
import { time } from "../store";
import { clock } from "../wizard";

const props = defineProps<{ from: number; to: number; lit?: { from: number; to: number } | null }>();
const emit = defineEmits<{ seek: [value: number] }>();
const span = computed(() => Math.max(0.001, props.to - props.from));
const at = computed(() => Math.min(props.to, Math.max(props.from, time.value)));
const litStyle = computed(() => {
  const lit = props.lit;
  if (!lit) return null;
  const a = Math.max(0, (lit.from - props.from) / span.value);
  const b = Math.min(1, (lit.to - props.from) / span.value);
  return b > a ? { left: `${a * 100}%`, width: `${(b - a) * 100}%` } : null;
});
</script>

<template>
  <span class="wiz-span">
    <span v-if="litStyle" class="wiz-span-lit" :style="litStyle" aria-hidden="true"></span>
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
.wiz-span-lit { position: absolute; top: 50%; height: 14px; margin-top: -7px; background: var(--accent-soft); border: 1px solid var(--accent-line); border-radius: 5px; pointer-events: none; }
.wiz-span input::-webkit-slider-runnable-track { height: 6px; border-radius: 3px; }
.wiz-span input::-webkit-slider-thumb { width: 18px; height: 18px; margin-top: -6px; background: var(--accent); }
.wiz-span input::-moz-range-track { height: 6px; border-radius: 3px; }
.wiz-span input::-moz-range-thumb { width: 18px; height: 18px; background: var(--accent); }
</style>
