<script setup lang="ts">
// Barre à deux poignées du mode simple : couper le début et la fin de la vidéo, ou choisir quand
// un flou posé à la main commence et finit.
// Les poignées se déplacent à la souris ou au clavier (← →, Maj pour aller plus vite).
// Par défaut la barre représente toute la vidéo (de 0 à `total`). Avec `origin`, elle n'en montre
// qu'une partie (de `origin` à `origin + total`) ; `min` et `max` bornent alors les poignées.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { state } from "../store";

const props = withDefaults(defineProps<{
  total: number; // durée représentée par la barre, en secondes
  start: number;
  end: number;
  step: number; // durée d'une image
  startText: string;
  endText: string;
  origin?: number; // instant représenté par le bord gauche de la barre
  min?: number; // le début ne descend pas en dessous (par défaut : le bord gauche)
  max?: number; // la fin ne monte pas au-dessus (par défaut : le bord droit)
  startLabel?: string;
  endLabel?: string;
}>(), { origin: 0, min: undefined, max: undefined, startLabel: "Début de la partie gardée", endLabel: "Fin de la partie gardée" });
const emit = defineEmits<{
  "update:start": [value: number];
  "update:end": [value: number];
  preview: [edge: "start" | "end"];
  commit: [edge: "start" | "end"];
}>();

const bar = ref<HTMLDivElement>();
/** Position de lecture dans la barre pendant la lecture (déplacée par transform, sans mise en page). */
const playhead = computed(() => {
  if (!state.playing) return null;
  const at = state.frame * props.step - props.origin;
  return at >= 0 && at <= props.total ? at : null;
});
const width = ref(1);
let dragging: "" | "start" | "end" = "";
let timer = 0;
const observer = new ResizeObserver(() => (width.value = bar.value?.clientWidth ?? 1));
onMounted(() => bar.value && observer.observe(bar.value));
onBeforeUnmount(() => {
  observer.disconnect();
  window.clearTimeout(timer);
});

const low = computed(() => Math.max(props.origin, props.min ?? props.origin));
const high = computed(() => Math.min(props.origin + props.total, props.max ?? props.origin + props.total));

function gap(): number {
  return Math.min(props.total, Math.max(props.step, 0.2)); // il reste toujours un peu de vidéo
}
function set(edge: "start" | "end", value: number): void {
  const snapped = Math.round(value / props.step) * props.step;
  if (edge === "start") emit("update:start", Math.min(Math.max(low.value, snapped), Math.max(low.value, props.end - gap())));
  else emit("update:end", Math.max(Math.min(high.value, snapped), Math.min(high.value, props.start + gap())));
  emit("preview", edge);
}
function down(edge: "start" | "end", event: PointerEvent): void {
  dragging = edge;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  emit("preview", edge);
}
function move(event: PointerEvent): void {
  if (!dragging || !bar.value) return;
  const rect = bar.value.getBoundingClientRect();
  set(dragging, props.origin + ((event.clientX - rect.left) / rect.width) * props.total);
}
function up(): void {
  if (dragging) emit("commit", dragging);
  dragging = "";
}
function key(edge: "start" | "end", event: KeyboardEvent): void {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  event.stopPropagation(); // le lecteur ne doit pas avancer d'une image en même temps
  const delta = (event.shiftKey ? 1 : 0.1) * (event.key === "ArrowLeft" ? -1 : 1);
  set(edge, (edge === "start" ? props.start : props.end) + delta);
  window.clearTimeout(timer);
  timer = window.setTimeout(() => emit("commit", edge), 500);
}
/** Place d'un instant dans la barre, en pour cent de sa largeur. */
function pct(value: number): string {
  return `${Math.min(100, Math.max(0, (100 * (value - props.origin)) / Math.max(props.total, 1e-6)))}%`;
}
function widthPct(length: number): string {
  return `${Math.min(100, Math.max(0, (100 * length) / Math.max(props.total, 1e-6)))}%`;
}
</script>

<template>
  <div class="trim-wrap">
    <div ref="bar" class="trim">
      <div class="trim-kept" :style="{ left: pct(start), width: widthPct(end - start) }"></div>
      <div v-if="playhead !== null" class="trim-head" :style="{ transform: `translateX(${(playhead / Math.max(total, 1e-6)) * width}px)` }"></div>
      <div
        class="trim-handle start" role="slider" tabindex="0" :aria-label="startLabel" :aria-valuemin="low" :aria-valuemax="high"
        :aria-valuenow="start" :aria-valuetext="startText" :style="{ left: pct(start) }"
        @pointerdown="down('start', $event)" @pointermove="move" @pointerup="up" @pointercancel="up" @keydown="key('start', $event)"
      ><span></span></div>
      <div
        class="trim-handle end" role="slider" tabindex="0" :aria-label="endLabel" :aria-valuemin="low" :aria-valuemax="high"
        :aria-valuenow="end" :aria-valuetext="endText" :style="{ left: pct(end) }"
        @pointerdown="down('end', $event)" @pointermove="move" @pointerup="up" @pointercancel="up" @keydown="key('end', $event)"
      ><span></span></div>
    </div>
    <div class="trim-labels">
      <span>Début : <strong>{{ startText }}</strong></span>
      <slot></slot>
      <span>Fin : <strong>{{ endText }}</strong></span>
    </div>
  </div>
</template>
