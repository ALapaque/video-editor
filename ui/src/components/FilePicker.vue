<script setup lang="ts">
// Sélecteur de fichiers servi par le moteur : un navigateur ne donne pas le chemin réel d'un
// fichier, or le moteur en a besoin pour lire la vidéo sans la copier.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { call, type Browse } from "../api";
import Icon from "./Icon.vue";

// `simple` : présentation du mode simple, vidéos seulement, sans saisie de chemin.
const props = defineProps<{ title: string; start?: string; simple?: boolean }>();
const emit = defineEmits<{ pick: [path: string]; close: [] }>();
const listing = ref<Browse | null>(null);
const error = ref("");
const typed = ref("");

async function go(dir?: string): Promise<void> {
  try {
    listing.value = await call<Browse>("GET", `/api/browse${dir ? `?dir=${encodeURIComponent(dir)}` : ""}`);
    typed.value = listing.value.dir;
    error.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
}
/** Mode simple : seulement les vidéos (un fichier projet s'ouvre en choisissant sa vidéo). */
const shownFiles = computed(() => (listing.value?.files ?? []).filter((f) => !props.simple || !f.name.endsWith(".json")));
function join(name: string): string {
  const l = listing.value!;
  return l.dir.endsWith(l.sep) ? l.dir + name : l.dir + l.sep + name;
}
function size(bytes: number): string {
  return bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} Go` : `${Math.max(1, Math.round(bytes / 1e6))} Mo`;
}
function submitTyped(): void {
  const value = typed.value.trim();
  if (/\.(mp4|mov|mkv|m4v|avi|mts|webm|json)$/i.test(value)) emit("pick", value);
  else void go(value);
}
function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}
onMounted(() => {
  window.addEventListener("keydown", onKey);
  void go(props.start);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div class="dialog picker" role="dialog" aria-modal="true" aria-labelledby="picker-title">
      <h3 id="picker-title"><Icon name="folder" :size="18" />{{ title }}</h3>
      <p v-if="simple && listing" class="picker-where"><span class="dim">Dossier :</span> <span class="mono">{{ listing.dir }}</span></p>
      <form v-else-if="!simple" class="row" @submit.prevent="submitTyped">
        <input v-model="typed" type="text" spellcheck="false" placeholder="Chemin d'un dossier ou d'une vidéo" aria-label="Chemin d'un dossier ou d'une vidéo" />
        <button type="submit">Aller</button>
      </form>
      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="listing" class="row shortcuts">
        <button :disabled="!listing.parent" @click="go(listing.parent!)"><Icon name="arrowUp" />Dossier parent</button>
        <button @click="go(listing.home)"><Icon name="home" />Dossier personnel</button>
        <button v-for="r in listing.roots" :key="r" @click="go(r)"><Icon name="drive" />{{ r }}</button>
      </div>
      <ul v-if="listing" :key="listing.dir" class="files">
        <li v-for="f in listing.folders" :key="'d' + f" class="folder" @click="go(join(f))"><Icon name="folder" /><span class="fname">{{ f }}</span><Icon class="go" name="chevronRight" :size="14" /></li>
        <li v-for="f in shownFiles" :key="'f' + f.name" class="file" @click="emit('pick', join(f.name))">
          <Icon name="film" /><span class="fname">{{ f.name }}</span> <span class="dim mono">{{ size(f.size) }}</span>
        </li>
        <li v-if="!listing.folders.length && !shownFiles.length" class="dim none">Aucune vidéo ni dossier ici.</li>
      </ul>
      <div v-else-if="!error" class="files loading"><span class="spinner"></span>Lecture du dossier…</div>
      <div class="row end"><button @click="emit('close')">Annuler</button></div>
    </div>
  </div>
</template>
