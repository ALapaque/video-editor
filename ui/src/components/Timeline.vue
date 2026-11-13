<script setup lang="ts">
// Timeline unique : passages gardés bout à bout (bande d'images et niveau sonore), passages
// muets, passages douteux, tête de lecture.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { currentEntry, explain, guided, loop, markEntries } from "../review";
import {
  busy, duration, formatTime, mapRange, moveSegment, project, saveMontage, seekTime, segments, state, time, trimSegment,
  visibleReview, type Resolved,
} from "../store";
import Icon from "./Icon.vue";
import TimelineStrip from "./TimelineStrip.vue";

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

/** Images de début et de fin affichées pour un passage (rognage en cours compris). */
function shown(s: Resolved): { inFrame: number; outFrame: number } {
  const t = trim.value;
  if (!t || t.index !== s.index) return s;
  return t.edge === "start" ? { inFrame: t.frame, outFrame: s.outFrame } : { inFrame: s.inFrame, outFrame: t.frame };
}

/** Position et largeur d'un passage, en pixels. */
function leftOf(s: Resolved): number {
  return (s.offset + (shown(s).inFrame - s.inFrame) / s.fps) * pxPerSecond.value;
}
function widthOf(s: Resolved): number {
  const { inFrame, outFrame } = shown(s);
  return Math.max(((outFrame - inFrame) / s.fps) * pxPerSecond.value, 2);
}

function blockStyle(s: Resolved) {
  const m = moving.value;
  return {
    left: `${leftOf(s)}px`, width: `${widthOf(s)}px`, backgroundColor: colorOf(s),
    transform: m && m.index === s.index ? `translateX(${m.dx}px)` : undefined,
  };
}

// Partie visible de la timeline, par paliers : les bandes d'images et le niveau sonore ne sont
// fabriqués que pour ce qui est à l'écran (une timeline zoomée peut mesurer des centaines de milliers de pixels).
const scrollX = ref(0);
const VIEW_STEP = 160;
const VIEW_MARGIN = 320;
function onScroll(): void {
  const x = Math.round((scroller.value?.scrollLeft ?? 0) / VIEW_STEP) * VIEW_STEP;
  if (x !== scrollX.value) scrollX.value = x;
}
function viewFrom(s: Resolved): number {
  return scrollX.value - 12 - VIEW_MARGIN - leftOf(s);
}
function viewTo(s: Resolved): number {
  return scrollX.value - 12 + width.value + VIEW_MARGIN - leftOf(s);
}

// --- Réordonner : glisser un passage par sa poignée (les boutons « Déplacer » restent) ---
const canMove = computed(() => segments.value.length > 1 && !busy.value && !guided.value);
/** Passage en cours de déplacement : décalage en pixels et place d'arrivée (0 = avant le premier). */
const moving = ref<{ index: number; dx: number; slot: number } | null>(null);

function slotAt(x: number): number {
  let slot = 0;
  for (const o of segments.value) if (x > (o.offset + o.duration / 2) * pxPerSecond.value) slot++;
  return slot;
}
/** Trait d'insertion : visible seulement si lâcher ici change l'ordre. */
const insertAt = computed(() => {
  const m = moving.value;
  if (!m) return null;
  const list = segments.value;
  const from = list.findIndex((s) => s.index === m.index);
  if (m.slot === from || m.slot === from + 1) return null;
  return (m.slot < list.length ? list[m.slot].offset : duration.value) * pxPerSecond.value;
});

function moveStart(event: PointerEvent, s: Resolved): void {
  if (event.button !== 0 || !canMove.value) return;
  state.selected = s.index;
  const box = scroller.value!;
  const origin = event.clientX;
  const startScroll = box.scrollLeft;
  const move = (e: PointerEvent) => {
    const rect = box.getBoundingClientRect();
    // près d'un bord : la timeline défile pour atteindre un passage hors de l'écran
    if (e.clientX > rect.right - 28) box.scrollLeft += 16;
    else if (e.clientX < rect.left + 28) box.scrollLeft -= 16;
    const dx = e.clientX - origin + box.scrollLeft - startScroll;
    if (!moving.value && Math.abs(dx) < 5) return; // simple clic sur la poignée
    moving.value = { index: s.index, dx, slot: slotAt(timeAt(e) * pxPerSecond.value) };
  };
  const up = async () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    const m = moving.value;
    moving.value = null;
    if (m) await moveTo(m.index, m.slot);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

/** Place le passage `index` à la place d'arrivée `slot` : une seule modification du montage. */
async function moveTo(index: number, slot: number): Promise<void> {
  const p = project.value;
  const list = segments.value;
  const from = list.findIndex((s) => s.index === index);
  if (!p || from < 0 || slot === from || slot === from + 1) return;
  let target = slot < list.length ? list[slot].index : p.timeline.length;
  if (target > index) target--; // le passage retiré décale les suivants
  const timeline = [...p.timeline];
  const [item] = timeline.splice(index, 1);
  timeline.splice(target, 0, item);
  await saveMontage(timeline);
  state.selected = target;
  const moved = segments.value.find((s) => s.index === target);
  if (moved) seekTime(moved.offset);
}

/** Au clavier : la poignée a le focus, ← et → déplacent le passage d'un cran. */
async function moveKey(event: KeyboardEvent, s: Resolved): Promise<void> {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  event.stopPropagation(); // sinon la tête de lecture avancerait d'une image
  await moveSegment(s.index, event.key === "ArrowLeft" ? -1 : 1);
  await nextTick();
  scroller.value?.querySelector<HTMLElement>(`[data-grip="${state.selected}"]`)?.focus();
}

const observer = new ResizeObserver((entries) => (width.value = entries[0].contentRect.width));
onMounted(() => scroller.value && observer.observe(scroller.value));
onBeforeUnmount(() => observer.disconnect());
</script>

<template>
  <div ref="scroller" class="timeline" @pointerdown="scrubStart" @pointermove="scrubMove" @pointerup="scrubEnd" @scroll.passive="onScroll">
    <div class="track" :style="{ width: `${total}px` }">
      <div class="ruler">
        <span v-for="t in ticks.list" :key="t" class="tick" :style="{ left: `${t * pxPerSecond}px` }">{{ formatTime(t, ticks.step < 1 ? 1 : 0) }}</span>
      </div>
      <div class="segments">
        <div
          v-for="(s, n) in segments" :key="s.index" class="segment"
          :class="{ selected: state.selected === s.index, trimming: trim?.index === s.index, moving: moving?.index === s.index }" :style="blockStyle(s)" :title="`${s.clip.name}, de ${formatTime(s.inFrame / s.fps)} à ${formatTime(s.outFrame / s.fps)} dans le clip`"
          @pointerdown="state.selected = s.index"
        >
          <span class="handle left" title="Glisser pour rogner ou rallonger le début" @pointerdown="trimStart($event, s, 'start')"></span>
          <span class="seg-head">
            <button
              v-if="canMove" class="grip" :data-grip="s.index" title="Glisser pour déplacer ce passage dans le montage (au clavier : ← et →)"
              :aria-label="`Déplacer le passage ${n + 1} sur ${segments.length} : ${s.clip.name}`" @pointerdown.stop="moveStart($event, s)" @keydown="moveKey($event, s)"
            ><Icon name="grip" :size="12" /></button>
            <span class="name">{{ s.clip.name }}</span>
            <span class="range">{{ formatTime(s.inFrame / s.fps, 1) }} → {{ formatTime(s.outFrame / s.fps, 1) }}</span>
          </span>
          <TimelineStrip
            :clip="s.clip" :start="shown(s).inFrame / s.fps" :end="shown(s).outFrame / s.fps" :pps="pxPerSecond"
            :view-from="viewFrom(s)" :view-to="viewTo(s)"
          />
          <span class="handle right" title="Glisser pour rogner ou rallonger la fin" @pointerdown="trimStart($event, s, 'end')"></span>
        </div>
        <div v-for="(m, i) in mutes" :key="`m${i}`" class="mute" :style="{ left: `${m.start * pxPerSecond}px`, width: `${Math.max((m.end - m.start) * pxPerSecond, 2)}px` }" title="Passage muet"></div>
        <div v-if="insertAt !== null" class="insert" :style="{ transform: `translateX(${insertAt}px)` }"></div>
      </div>
      <div class="markers">
        <div
          v-for="r in visibleReview" :key="r.key + r.at" class="marker" :class="[r.item.kinds[0], { done: r.item.done, current: currentEntry?.id === r.key }]"
          :style="{ left: `${r.at * pxPerSecond}px`, width: `${Math.max((r.end - r.at) * pxPerSecond, 4)}px` }"
          :title="`${r.item.done ? 'Passage validé' : 'Passage à vérifier'} : ${explain(r.item.reasons).map((x) => x.text).join(' ')}`"
          @pointerdown.stop="seekTime(Math.max(0, r.at - 0.4))"
        ></div>
      </div>
      <!-- signalements de relecture -->
      <div
        v-for="m in markEntries" :key="m.id" class="flag" :class="{ current: currentEntry?.id === m.id }"
        :style="{ transform: `translateX(${m.at * pxPerSecond}px)` }" :title="`Signalement à ${formatTime(m.at, 1)}`"
        @pointerdown.stop="seekTime(Math.max(0, m.at - 0.4))"
      ></div>
      <!-- passage lu en boucle pendant la vérification -->
      <div v-if="loop" class="loop-range" :style="{ left: `${loop.from * pxPerSecond}px`, width: `${(loop.to - loop.from) * pxPerSecond}px` }"></div>
      <div v-if="state.muteIn !== null" class="mutein" :style="{ left: `${state.muteIn * pxPerSecond}px` }"></div>
      <!-- déplacée par transform : aucune remise en page pendant la lecture -->
      <div class="playhead" :style="{ transform: `translateX(${time * pxPerSecond}px)` }"></div>
    </div>
  </div>
</template>
