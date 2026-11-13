<script lang="ts">
// Un flou en une ligne : vignette, puis « Visage · de 0:12 à 0:15 · douteux ».
// Vignettes déjà affichées : gardées pendant la lecture. Une nouvelle vignette coûte au moteur la
// lecture d'une image de la vidéo : avec `defer`, elle attend l'arrêt de la lecture.
import { reactive } from "vue";
const loaded = reactive(new Set<string>());
</script>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { Track } from "../api";
import { blurSpan, confidence, confidenceInfo, thumbUrl } from "../extras";
import { displayName } from "../blurs";
import { state } from "../store";

const props = defineProps<{ clip: string; track: Track; defer?: boolean; size?: "small" | "large" }>();

const level = computed(() => confidence(props.track));
const url = computed(() => thumbUrl(props.clip, props.track.id, props.size === "large" ? 220 : 120));
const failed = ref(false);
const showThumb = computed(() => !failed.value && (!props.defer || !state.playing || loaded.has(url.value)));
</script>

<template>
  <span class="blur-row" :class="size">
    <span class="blur-thumb" :class="track.kind">
      <img v-if="showThumb" :src="url" alt="" loading="lazy" @load="loaded.add(url)" @error="failed = true" />
    </span>
    <span class="blur-text">
      <span class="blur-line"><strong>{{ displayName(track) }}</strong><span class="dim"> · {{ blurSpan(track) }} · </span><span class="level" :class="level" :title="confidenceInfo[level].help">{{ confidenceInfo[level].label }}</span></span>
      <slot></slot>
    </span>
  </span>
</template>

<style>
.blur-row { display: flex; align-items: center; gap: 9px; min-width: 0; }
.blur-thumb { position: relative; flex: none; width: 56px; height: 42px; overflow: hidden; background: #000; border-radius: 5px; box-shadow: inset 0 0 0 1px var(--line); }
.blur-thumb::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: #fff; }
.blur-thumb.face::after { background: var(--face); }
.blur-thumb.plate::after { background: var(--plate); }
.blur-thumb.dash::after { background: var(--dash); }
.blur-thumb img { display: block; width: 100%; height: 100%; object-fit: cover; animation: fade-in var(--m-in) var(--ease); }
.blur-row.large .blur-thumb { width: 112px; height: 84px; }
.blur-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.blur-line { line-height: 1.35; }
.blur-line strong { font-weight: 600; }
.level { white-space: nowrap; }
.level.douteux { color: var(--accent-hi); font-weight: 600; }
.level.verifier { color: var(--text); }
.level.sur, .level.main { color: var(--dim); }
.level.ecarte { color: var(--faint); }
</style>
