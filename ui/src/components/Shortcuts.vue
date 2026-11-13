<script setup lang="ts">
// Liste des raccourcis clavier du mode avancé, ouverte par le bouton « ? » ou la touche ?.
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import Icon from "./Icon.vue";

const emit = defineEmits<{ close: [] }>();
const box = ref<HTMLDivElement>();
let opener: Element | null = null;
const mac = /Mac|iPhone|iPad/.test(navigator.platform);
const ctrl = mac ? "Cmd" : "Ctrl";

const groups: { title: string; keys: { keys: string[]; text: string }[] }[] = [
  {
    title: "Lecture",
    keys: [
      { keys: ["Espace"], text: "Lecture / pause" },
      { keys: ["←", "→"], text: "Image précédente / suivante" },
      { keys: ["Maj + ←", "Maj + →"], text: "Une seconde en arrière / en avant" },
      { keys: ["Début", "Fin"], text: "Aller au début / à la fin du montage" },
      { keys: ["V"], text: "Voir le flou / voir les contours" },
    ],
  },
  {
    title: "Zoom sur la vidéo",
    keys: [
      { keys: ["Molette"], text: "Zoomer là où se trouve la souris (ou pincer sur le pavé tactile)" },
      { keys: ["+", "−"], text: "Zoomer / dézoomer au centre" },
      { keys: ["Glisser"], text: "Se déplacer dans l'image zoomée (bouton du milieu pendant un tracé)" },
      { keys: ["0"], text: "Revenir à l'image entière" },
    ],
  },
  {
    title: "Montage",
    keys: [
      { keys: ["C"], text: "Couper ici" },
      { keys: ["Suppr"], text: "Supprimer le passage sélectionné (ou Retour arrière)" },
      { keys: ["M"], text: "Couper le son : début, puis fin" },
      { keys: ["←", "→"], text: "Sur la poignée d'un passage : le déplacer à gauche / à droite" },
      { keys: [`${ctrl} + Z`], text: "Annuler la dernière modification" },
      { keys: [mac ? "Maj + Cmd + Z" : "Ctrl + Y"], text: "Rétablir" },
    ],
  },
  {
    title: "Vérification",
    keys: [
      { keys: ["Clic sur un flou"], text: "Pendant la relecture : retirer ce flou, sans arrêter la lecture" },
      { keys: ["S"], text: "Pendant la relecture : signaler que quelque chose reste lisible" },
      { keys: ["Entrée"], text: "« Oui, c'est bon » : passer à l'alerte ou au signalement suivant" },
      { keys: ["Maj + clic"], text: "Dans la planche des flous : cocher toute une série" },
      { keys: ["Échap"], text: "Annuler un tracé ou une correction, fermer une fenêtre" },
      { keys: ["?"], text: "Afficher cette liste" },
    ],
  },
];

function onKey(event: KeyboardEvent): void {
  if (event.key === "Escape" || event.key === "?") {
    event.preventDefault();
    event.stopPropagation();
    emit("close");
  }
}
onMounted(() => {
  opener = document.activeElement;
  window.addEventListener("keydown", onKey, true);
  void nextTick(() => box.value?.focus());
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey, true);
  if (opener instanceof HTMLElement && document.contains(opener)) opener.focus({ preventScroll: true });
});
</script>

<template>
  <div class="modal" @pointerdown.self="emit('close')">
    <div ref="box" class="dialog shortcuts-dialog" role="dialog" aria-modal="true" aria-labelledby="keys-title" tabindex="-1">
      <div class="sheet-head">
        <h3 id="keys-title"><Icon name="info" :size="18" />Raccourcis clavier</h3>
        <button class="icon-btn bare" aria-label="Fermer la liste des raccourcis" title="Fermer (Échap)" @click="emit('close')"><Icon name="close" /></button>
      </div>
      <div class="keys-grid">
        <section v-for="g in groups" :key="g.title">
          <h4>{{ g.title }}</h4>
          <dl>
            <template v-for="k in g.keys" :key="k.text">
              <dt><kbd v-for="key in k.keys" :key="key">{{ key }}</kbd></dt>
              <dd>{{ k.text }}</dd>
            </template>
          </dl>
        </section>
      </div>
      <div class="row end"><button @click="emit('close')">Fermer</button></div>
    </div>
  </div>
</template>

<style>
.dialog.shortcuts-dialog { width: min(760px, 94vw); overflow-y: auto; }
.shortcuts-dialog:focus { outline: none; }
.keys-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 28px; }
.keys-grid h4 { margin-bottom: 6px; font-size: 12px; font-weight: 650; color: var(--faint); letter-spacing: .04em; text-transform: uppercase; }
.keys-grid dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 0; align-items: baseline; }
.keys-grid dt { display: flex; gap: 4px; white-space: nowrap; }
.keys-grid dt kbd { margin: 0; opacity: 1; color: var(--text); border-color: var(--line-strong); background: var(--raised); }
.keys-grid dd { margin: 0; color: var(--dim); font-size: 12.5px; line-height: 1.4; }
@media (max-width: 720px) { .keys-grid { grid-template-columns: 1fr; } }
</style>
