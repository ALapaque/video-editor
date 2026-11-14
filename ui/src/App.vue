<script setup lang="ts">
// Racine de l'application : écran de choix du mode au premier lancement, puis l'assistant
// (mode simple) ou l'interface complète (mode avancé). Le projet ouvert vit dans le moteur et
// dans `store` : changer de mode ne le ferme pas et ne perd rien.
import { onBeforeUnmount, onMounted } from "vue";
import Advanced from "./components/Advanced.vue";
import ModeChoice from "./components/ModeChoice.vue";
import Wizard from "./components/Wizard.vue";
import { dropToBatch } from "./batch";
import { historyShortcut, installDrop, redo, undo } from "./extras";
import { loadMode, refresh, send, state } from "./store";
import { drop, droppedFile } from "./wizard";

/** Fichiers déposés sur la fenêtre (application installée) : la file d'attente les prend si son
 *  dialogue est ouvert ; sinon l'assistant décide ; en mode avancé, le premier s'ouvre simplement. */
async function onDrop(paths: string[]): Promise<void> {
  if (dropToBatch(paths)) return;
  if (drop.target) return drop.target(paths);
  const file = droppedFile(paths);
  if (file && (await send("POST", "/api/open", { path: file }))) await refresh();
}

/** Ctrl+Z / Ctrl+Y : annuler et rétablir, dans les deux modes. */
function onHistoryKey(event: KeyboardEvent): void {
  const target = event.target;
  // Une case à cocher ou un curseur garde le focus après le clic mais n'a rien à annuler lui-même
  // (ce n'est pas une saisie de texte) : le raccourci y annule bien la dernière modification.
  if (target instanceof HTMLInputElement && ["checkbox", "radio", "range"].includes(target.type)) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    const back = key === "z" && !event.shiftKey;
    if (!back && key !== "y" && key !== "z") return;
    event.preventDefault();
    void (back ? undo() : redo());
    return;
  }
  historyShortcut(event);
}

loadMode();
onMounted(() => {
  void refresh();
  installDrop((paths) => void onDrop(paths));
  window.addEventListener("keydown", onHistoryKey);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onHistoryKey));
</script>

<template>
  <ModeChoice v-if="!state.mode" />
  <Wizard v-else-if="state.mode === 'simple'" />
  <Advanced v-else />
</template>
