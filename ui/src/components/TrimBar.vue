<script setup lang="ts">
// Barre du mode simple pour couper le début et la fin : une bande, deux poignées.
// Les poignées se déplacent à la souris ou au clavier (← →, Maj pour aller plus vite).
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { state } from "../store";

const props = defineProps<{
  total: number; // durée de la vidéo, en secondes
  start: number;
  end: number;
  step: number; // durée d'une image
  startText: string;
  endText: string;
}>();
const emit = defineEmits<{
  "update:start": [value: number];
  "update:end": [value: number];
  preview: [edge: "start" | "end"];
  commit: [edge: "start" | "end"];
}>();

const bar = ref<HTMLDivElement>();
/** Position de lecture dans la vidéo pendant « Lire l'extrait » (déplacée par transform, sans mise en page). */
const playhead = computed(() => (state.playing ? state.frame * props.step : null));
const width = ref(1);
let dragging: "" | "start" | "end" = "";
let timer = 0;
const observer = new ResizeObserver(() => (width.value = bar.value?.clientWidth ?? 1));
onMounted(() => bar.value && observer.observe(bar.value));
onBeforeUnmount(() => {
  observer.disconnect();
  window.clearTimeout(timer);
});

function gap(): number {
  return Math.min(props.total, Math.max(props.step, 0.2)); // il reste toujours un peu de vidéo
}
function set(edge: "start" | "end", value: number): void {
  const snapped = Math.round(value / props.step) * props.step;
  if (edge === "start") emit("update:start", Math.min(Math.max(0, snapped), props.end - gap()));
  else emit("update:end", Math.max(Math.min(props.total, snapped), props.start + gap()));
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
  set(dragging, ((event.clientX - rect.left) / rect.width) * props.total);
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
function pct(value: number): string {
  return `${(100 * value) / Math.max(props.total, 1e-6)}%`;
}
</script>

<template>
  <div class="trim-wrap">
    <div ref="bar" class="trim">
      <div class="trim-kept" :style="{ left: pct(start), width: pct(end - start) }"></div>
      <div v-if="playhead !== null" class="trim-head" :style="{ transform: `translateX(${(playhead / Math.max(total, 1e-6)) * width}px)` }"></div>
      <div
        class="trim-handle start" role="slider" tabindex="0" aria-label="Début de la partie gardée" :aria-valuemin="0" :aria-valuemax="total"
        :aria-valuenow="start" :aria-valuetext="startText" :style="{ left: pct(start) }"
        @pointerdown="down('start', $event)" @pointermove="move" @pointerup="up" @pointercancel="up" @keydown="key('start', $event)"
      ><span></span></div>
      <div
        class="trim-handle end" role="slider" tabindex="0" aria-label="Fin de la partie gardée" :aria-valuemin="0" :aria-valuemax="total"
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
