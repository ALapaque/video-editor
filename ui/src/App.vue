<script setup lang="ts">
// Coquille de l'application : choix du mode au premier lancement, puis l'assistant pas à pas
// (mode simple) ou l'interface complète (mode avancé). Le projet ouvert vit dans le moteur et
// dans `store` : changer de mode ne le ferme pas et ne perd rien.
import { onMounted } from "vue";
import Advanced from "./components/Advanced.vue";
import ModeChoice from "./components/ModeChoice.vue";
import Wizard from "./components/Wizard.vue";
import { loadMode, refresh, state } from "./store";

loadMode();
onMounted(() => void refresh());
</script>

<template>
  <ModeChoice v-if="!state.mode" />
  <Wizard v-else-if="state.mode === 'simple'" />
  <Advanced v-else />
</template>
