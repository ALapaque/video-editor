<script setup lang="ts">
// Vignette d'un flou (image fabriquée par le moteur) : un fond pendant le chargement, une phrase
// si l'image n'a pas pu être faite. Le cadre garde sa taille : la grille ne saute pas.
import { ref, watch } from "vue";

const props = defineProps<{ src: string; alt: string; eager?: boolean }>();
const status = ref<"loading" | "ok" | "error">("loading");
watch(() => props.src, () => (status.value = "loading"));
</script>

<template>
  <span class="wiz-thumb" :class="status">
    <img v-if="status !== 'error'" :src="src" :alt="alt" :loading="eager ? 'eager' : 'lazy'" decoding="async" draggable="false" @load="status = 'ok'" @error="status = 'error'" />
    <span v-else class="wiz-thumb-missing">Image indisponible</span>
  </span>
</template>

<style>
.wiz-thumb { position: relative; display: block; width: 100%; aspect-ratio: 4 / 3; background: var(--raised); border-radius: 8px; overflow: hidden; }
.wiz-thumb img { display: block; width: 100%; height: 100%; object-fit: cover; opacity: 0; transition: opacity var(--m-panel) var(--ease); }
.wiz-thumb.ok img { opacity: 1; }
.wiz-thumb-missing { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 6px; color: var(--faint); font-size: 12.5px; text-align: center; }
</style>
