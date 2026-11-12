<script setup lang="ts">
// Premier lancement : mode simple (assistant pas à pas) ou mode avancé (tous les outils).
import { onBeforeUnmount, onMounted } from "vue";
import { setMode } from "../store";
import Icon from "./Icon.vue";
import Logo from "./Logo.vue";

function onKey(event: KeyboardEvent): void {
  const tag = (event.target as HTMLElement).tagName;
  if (event.key === "Enter" && tag !== "BUTTON") setMode("simple"); // Entrée : le choix recommandé
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <main class="mode-choice">
    <span class="brand"><Logo :size="26" /><strong>moto-blur</strong></span>
    <h1>Comment voulez-vous travailler ?</h1>
    <p class="wiz-lead">Vous pourrez changer d'avis à tout moment, sans rien perdre.</p>
    <div class="mode-cards">
      <button class="mode-card suggested" data-primary @click="setMode('simple')">
        <span class="mode-icon"><Icon name="steps" :size="30" /></span>
        <span class="mode-name">Mode simple</span>
        <span class="mode-text">Le logiciel vous guide pas à pas, une question à la fois. Recommandé pour commencer.</span>
      </button>
      <button class="mode-card" @click="setMode('advanced')">
        <span class="mode-icon"><Icon name="sliders" :size="30" /></span>
        <span class="mode-name">Mode avancé</span>
        <span class="mode-text">Tous les outils : timeline, coupes au milieu, plusieurs vidéos, réglages.</span>
      </button>
    </div>
  </main>
</template>
