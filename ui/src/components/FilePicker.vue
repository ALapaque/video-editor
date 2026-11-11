<script setup lang="ts">
// Sélecteur de fichiers servi par le moteur : un navigateur ne donne pas le chemin réel d'un
// fichier, or le moteur en a besoin pour lire la vidéo sans la copier.
import { onMounted, ref } from "vue";
import { call, type Browse } from "../api";

const props = defineProps<{ title: string; start?: string }>();
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
onMounted(() => go(props.start));
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div class="dialog picker">
      <h3>{{ title }}</h3>
      <form class="row" @submit.prevent="submitTyped">
        <input v-model="typed" type="text" spellcheck="false" placeholder="Chemin d'un dossier ou d'une vidéo" />
        <button type="submit">Aller</button>
      </form>
      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="listing" class="row shortcuts">
        <button :disabled="!listing.parent" @click="go(listing.parent!)">↑ Dossier parent</button>
        <button @click="go(listing.home)">Dossier personnel</button>
        <button v-for="r in listing.roots" :key="r" @click="go(r)">{{ r }}</button>
      </div>
      <ul v-if="listing" class="files">
        <li v-for="f in listing.folders" :key="'d' + f" class="folder" @click="go(join(f))">📁 {{ f }}</li>
        <li v-for="f in listing.files" :key="'f' + f.name" @click="emit('pick', join(f.name))">
          🎬 {{ f.name }} <span class="dim">{{ size(f.size) }}</span>
        </li>
        <li v-if="!listing.folders.length && !listing.files.length" class="dim">Aucune vidéo ni dossier ici.</li>
      </ul>
      <div class="row end"><button @click="emit('close')">Annuler</button></div>
    </div>
  </div>
</template>
