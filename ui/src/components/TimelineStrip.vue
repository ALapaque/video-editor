<script lang="ts">
// Bande d'images et niveau sonore d'un passage de la timeline.
// Le moteur fournit, par clip, UNE image qui contient toutes les vignettes côte à côte et un
// tableau de niveaux sonores : les deux sont demandés une seule fois par clip, puis chaque
// passage en montre la portion qui le concerne. Rien n'est recalculé pendant la lecture : le
// contenu ne dépend que du passage, du zoom et de la partie visible de la timeline.
import { filmstripUrl, waveform } from "../extras";
import { project } from "../store";

const STRIP_HEIGHT = 34; // hauteur affichée des vignettes, en pixels
const WAVE_HEIGHT = 18;
const strips = new Map<string, Promise<string>>();

/** Adresse locale de la bande d'images d'un clip ("" si elle ne peut pas être fabriquée). */
function loadStrip(key: string, clip: string, count: number): Promise<string> {
  let strip = strips.get(key);
  if (!strip) {
    strip = fetch(filmstripUrl(clip, count))
      .then((response) => (response.ok ? response.blob() : Promise.reject(new Error(String(response.status)))))
      .then((blob) => URL.createObjectURL(blob))
      .catch(() => {
        strips.delete(key); // nouvel essai au prochain affichage
        return "";
      });
    strips.set(key, strip);
  }
  return strip;
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import type { Clip } from "../api";

const props = defineProps<{
  clip: Clip;
  start: number; // début et fin du passage, en secondes de temps source
  end: number;
  pps: number; // pixels par seconde
  viewFrom: number; // partie visible du passage, en pixels depuis son bord gauche
  viewTo: number;
}>();

const width = computed(() => Math.max(0, (props.end - props.start) * props.pps));
const tile = computed(() => Math.max(8, Math.round((STRIP_HEIGHT * props.clip.width) / Math.max(1, props.clip.height))));
/** Nombre de vignettes demandées pour le clip : une toutes les demi-secondes, 120 au plus. */
const count = computed(() => Math.min(120, Math.max(16, Math.round(props.clip.duration * 2))));
const ready = computed(() => props.clip.proxy && !props.clip.missing); // l'aperçu du clip existe : lecture rapide

// --- Bande d'images ---
const strip = ref("");
const stripKey = computed(() => `${project.value?.path}|${props.clip.id}|${props.clip.path}|${count.value}`);
watch([stripKey, ready], async ([key, ok]) => {
  strip.value = "";
  if (!ok) return;
  const url = await loadStrip(key, props.clip.id, count.value);
  if (key === stripKey.value) strip.value = url;
}, { immediate: true });

/** Vignettes visibles : chacune montre l'image du clip la plus proche de l'instant qu'elle couvre. */
const tiles = computed(() => {
  const out: { x: number; shift: number }[] = [];
  if (!strip.value || props.pps <= 0) return out;
  const w = tile.value;
  const first = Math.max(0, Math.floor(props.viewFrom / w));
  const last = Math.min(Math.ceil(width.value / w), Math.ceil(props.viewTo / w));
  const total = Math.max(props.clip.duration, 1e-6);
  for (let j = first; j < last; j++) {
    const t = Math.min(props.start + ((j + 0.5) * w) / props.pps, props.end);
    const index = Math.min(count.value - 1, Math.max(0, Math.floor((t / total) * count.value)));
    out.push({ x: j * w, shift: -index * w });
  }
  return out;
});

// --- Niveau sonore ---
const peaks = ref<number[] | null>(null); // null : pas encore chargé
watch([stripKey, ready], async ([key, ok]) => {
  peaks.value = null;
  if (!props.clip.has_audio) peaks.value = [];
  else if (ok) {
    const values = await waveform(props.clip.id);
    if (key === stripKey.value) peaks.value = values;
  }
}, { immediate: true });

const canvas = ref<HTMLCanvasElement>();
const span = computed(() => {
  const from = Math.max(0, Math.floor(props.viewFrom));
  return { from, width: Math.max(0, Math.ceil(Math.min(width.value, props.viewTo)) - from) };
});

function drawWave(): void {
  const cv = canvas.value;
  const values = peaks.value;
  if (!cv) return;
  const ratio = window.devicePixelRatio || 1;
  const w = span.value.width;
  cv.width = Math.max(1, Math.round(w * ratio));
  cv.height = Math.round(WAVE_HEIGHT * ratio);
  const ctx = cv.getContext("2d");
  if (!ctx || !values?.length || w <= 0 || props.pps <= 0) return;
  ctx.fillStyle = "rgba(255, 255, 255, .62)";
  const total = Math.max(props.clip.duration, 1e-6);
  const scale = values.length / total;
  const step = 1 / props.pps; // durée couverte par un pixel
  for (let x = 0; x < w; x++) {
    const t = props.start + (span.value.from + x) * step;
    const a = Math.min(values.length - 1, Math.max(0, Math.floor(t * scale)));
    const b = Math.min(values.length, Math.max(a + 1, Math.ceil((t + step) * scale)));
    let peak = 0;
    for (let i = a; i < b; i++) if (values[i] > peak) peak = values[i];
    // au carré : un son fort en continu (moteur, vent) garde du relief au lieu d'un aplat
    const h = Math.max(1, Math.round(peak * peak * (WAVE_HEIGHT - 4)));
    ctx.fillRect(x * ratio, (WAVE_HEIGHT - 1 - h) * ratio, ratio, h * ratio);
  }
}
watch(() => [peaks.value, props.start, props.end, props.pps, span.value.from, span.value.width], drawWave, { flush: "post" });
onMounted(drawWave);
</script>

<template>
  <span class="strip" :style="{ '--strip': strip ? `url(${strip})` : 'none', '--strip-size': `${count * tile}px`, '--tile': `${tile}px` }">
    <span class="strip-images" :class="{ loaded: !!strip }">
      <span v-for="t in tiles" :key="t.x" class="strip-tile" :style="{ transform: `translateX(${t.x}px)`, backgroundPositionX: `${t.shift}px` }"></span>
    </span>
    <span class="strip-wave" :class="{ silent: peaks !== null && !peaks.length }" :title="peaks !== null && !peaks.length ? 'Ce clip n\'a pas de son' : undefined">
      <canvas ref="canvas" :style="{ transform: `translateX(${span.from}px)`, width: `${span.width}px` }"></canvas>
    </span>
  </span>
</template>
