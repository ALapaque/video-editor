<script setup lang="ts">
// Timeline unique : passages gardés bout à bout, passages muets, passages douteux, tête de lecture.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  duration, formatTime, mapRange, project, seekTime, segments, state, time, trimSegment,
  visibleReview, type Resolved,
} from "../store";

const scroller = ref<HTMLDivElement>();
const width = ref(800);
const pxPerSecond = computed(() => (state.zoom > 0 ? state.zoom : Math.max(1, (width.value - 24) / Math.max(duration.value, 0.1))));
const total = computed(() => Math.max(width.value - 24, duration.value * pxPerSecond.value));
// Une teinte par clip, volontairement sourde : l'orange (à vérifier) et le rose (compteur) restent
// réservés aux repères, le blanc à la tête de lecture et à la sélection.
const palette = ["#3f6fc4", "#2a8f83", "#75893a", "#a85f45", "#8a62a8", "#5f6e82"];

// Rognage en cours : aperçu local, envoyé au moteur au relâchement.
const trim = ref<{ index: number; edge: "start" | "end"; frame: number } | null>(null);

function colorOf(s: Resolved): string {
  const index = project.value?.clips.findIndex((c) => c.id === s.clip.id) ?? 0;
  return palette[index % palette.length];
}

const ticks = computed(() => {
  const steps = [0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600];
  const step = steps.find((s) => s * pxPerSecond.value >= 70) ?? 1200;
  const out = [];
  for (let t = 0; t <= duration.value + 1e-6; t += step) out.push(t);
  return { step, list: out };
});

const mutes = computed(() =>
  (project.value?.mutes ?? []).flatMap((m, index) => mapRange(m).map((part) => ({ ...part, index }))),
);

function timeAt(event: PointerEvent): number {
  const rect = scroller.value!.getBoundingClientRect();
  return (event.clientX - rect.left + scroller.value!.scrollLeft - 12) / pxPerSecond.value;
}

let scrubbing = false;
function scrubStart(event: PointerEvent): void {
  if ((event.target as HTMLElement).classList.contains("handle")) return;
  scrubbing = true;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  seekTime(timeAt(event));
}
function scrubMove(event: PointerEvent): void {
  if (scrubbing) seekTime(timeAt(event));
}
function scrubEnd(): void {
  scrubbing = false;
}

function trimStart(event: PointerEvent, s: Resolved, edge: "start" | "end"): void {
  event.stopPropagation();
  const origin = event.clientX;
  const base = edge === "start" ? s.inFrame : s.outFrame;
  trim.value = { index: s.index, edge, frame: base };
  const move = (e: PointerEvent) => {
    let frame = base + Math.round(((e.clientX - origin) / pxPerSecond.value) * s.fps);
    frame = edge === "start" ? Math.min(Math.max(frame, 0), s.outFrame - 1) : Math.max(Math.min(frame, s.clip.frames), s.inFrame + 1);
    trim.value = { index: s.index, edge, frame };
  };
  const up = async () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    const result = trim.value;
    trim.value = null;
    if (result && result.frame !== base) await trimSegment(result.index, result.edge, result.frame);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function blockStyle(s: Resolved) {
  let left = s.offset * pxPerSecond.value;
  let w = s.duration * pxPerSecond.value;
  const t = trim.value;
  if (t && t.index === s.index) {
    const delta = ((t.frame - (t.edge === "start" ? s.inFrame : s.outFrame)) / s.fps) * pxPerSecond.value;
    if (t.edge === "start") {
      left += delta;
      w -= delta;
    } else w += delta;
  }
  return { left: `${left}px`, width: `${Math.max(w, 2)}px`, backgroundColor: colorOf(s) };
}

const observer = new ResizeObserver((entries) => (width.value = entries[0].contentRect.width));
onMounted(() => scroller.value && observer.observe(scroller.value));
onBeforeUnmount(() => observer.disconnect());
</script>

<template>
  <div ref="scroller" class="timeline" @pointerdown="scrubStart" @pointermove="scrubMove" @pointerup="scrubEnd">
    <div class="track" :style="{ width: `${total}px` }">
      <div class="ruler">
        <span v-for="t in ticks.list" :key="t" class="tick" :style="{ left: `${t * pxPerSecond}px` }">{{ formatTime(t, ticks.step < 1 ? 1 : 0) }}</span>
      </div>
      <div class="segments">
        <div
          v-for="s in segments" :key="s.index" class="segment"
          :class="{ selected: state.selected === s.index, trimming: trim?.index === s.index }" :style="blockStyle(s)" :title="`${s.clip.name} — source ${formatTime(s.inFrame / s.fps)} → ${formatTime(s.outFrame / s.fps)}`"
          @pointerdown="state.selected = s.index"
        >
          <span class="handle left" title="Glisser pour rogner ou rallonger le début" @pointerdown="trimStart($event, s, 'start')"></span>
          <span class="name">{{ s.clip.name }}</span>
          <span class="range">{{ formatTime(s.inFrame / s.fps, 1) }} → {{ formatTime(s.outFrame / s.fps, 1) }}</span>
          <span class="handle right" title="Glisser pour rogner ou rallonger la fin" @pointerdown="trimStart($event, s, 'end')"></span>
        </div>
        <div v-for="(m, i) in mutes" :key="`m${i}`" class="mute" :style="{ left: `${m.start * pxPerSecond}px`, width: `${Math.max((m.end - m.start) * pxPerSecond, 2)}px` }" title="Passage muet"></div>
      </div>
      <div class="markers">
        <div
          v-for="r in visibleReview" :key="r.key + r.at" class="marker" :class="r.item.kinds[0]"
          :style="{ left: `${r.at * pxPerSecond}px`, width: `${Math.max((r.end - r.at) * pxPerSecond, 4)}px` }"
          :title="`À vérifier : ${r.item.reasons.join(' ; ')}`"
          @pointerdown.stop="seekTime(Math.max(0, r.at - 0.4))"
        ></div>
      </div>
      <div v-if="state.muteIn !== null" class="mutein" :style="{ left: `${state.muteIn * pxPerSecond}px` }"></div>
      <!-- déplacée par transform : aucune remise en page pendant la lecture -->
      <div class="playhead" :style="{ transform: `translateX(${time * pxPerSecond}px)` }"></div>
    </div>
  </div>
</template>
