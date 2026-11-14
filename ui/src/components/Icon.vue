<script lang="ts">
// Pictogrammes de l'interface : tracés maison sur une grille de 16, trait de 1,5.
// Tout est en ligne : l'application ne charge rien depuis le réseau.
const paths = {
  play: '<path d="M5 3.2v9.6L12.8 8z" fill="currentColor"/>',
  pause: '<path d="M5.25 3.5v9M10.75 3.5v9" stroke-width="2.4"/>',
  stepBack: '<path d="M11.75 3.75v8.5L6 8z" fill="currentColor"/><path d="M3.75 3.5v9"/>',
  stepForward: '<path d="M4.25 3.75v8.5L10 8z" fill="currentColor"/><path d="M12.25 3.5v9"/>',
  scissors: '<circle cx="4" cy="4.25" r="1.75"/><circle cx="4" cy="11.75" r="1.75"/><path d="M5.5 5.3 13.5 12M5.5 10.7 13.5 4"/>',
  trash: '<path d="M2.75 4.25h10.5M6.25 4V2.75h3.5V4M4.25 4.25l.6 8.1a1 1 0 0 0 1 .9h4.3a1 1 0 0 0 1-.9l.6-8.1M6.75 7v3.5M9.25 7v3.5"/>',
  arrowLeft: '<path d="M13 8H3M7 4 3 8l4 4"/>',
  arrowRight: '<path d="M3 8h10M9 4l4 4-4 4"/>',
  arrowUp: '<path d="M8 13V3M4 7l4-4 4 4"/>',
  chevronLeft: '<path d="M9.75 3.5 5.25 8l4.5 4.5"/>',
  chevronRight: '<path d="M6.25 3.5 10.75 8l-4.5 4.5"/>',
  flag: '<path d="M3.5 14V2.5M3.5 3h8.25l-2 2.9 2 2.9H3.5"/>',
  target: '<circle cx="8" cy="8" r="4.25"/><path d="M8 1.75v3M8 11.25v3M1.75 8h3M11.25 8h3"/>',
  chevronDown: '<path d="M3.5 6.25 8 10.75l4.5-4.5"/>',
  volume: '<path d="M2.5 6.25h2L7.75 3.5v9L4.5 9.75h-2z"/><path d="M10.4 6a2.9 2.9 0 0 1 0 4M12.3 4.2a5.4 5.4 0 0 1 0 7.6"/>',
  volumeOff: '<path d="M2.5 6.25h2L7.75 3.5v9L4.5 9.75h-2z"/><path d="M10.5 6.25 14 9.75M14 6.25l-3.5 3.5"/>',
  folder: '<path d="M1.75 4.25a1 1 0 0 1 1-1h3l1.5 1.75h6a1 1 0 0 1 1 1v6.25a1 1 0 0 1-1 1H2.75a1 1 0 0 1-1-1z"/>',
  film: '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5"/><path d="M5 2.75v10.5M11 2.75v10.5M1.75 8h12.5M1.75 5.4H5M1.75 10.6H5M11 5.4h3.25M11 10.6h3.25"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  close: '<path d="m3.75 3.75 8.5 8.5M12.25 3.75l-8.5 8.5"/>',
  check: '<path d="m3 8.5 3.25 3.25L13 4.75"/>',
  scan: '<path d="M2.5 5.5v-2a1 1 0 0 1 1-1h2M10.5 2.5h2a1 1 0 0 1 1 1v2M13.5 10.5v2a1 1 0 0 1-1 1h-2M5.5 13.5h-2a1 1 0 0 1-1-1v-2M5 8h6"/>',
  export: '<path d="M8 10V2.5M5 5.25l3-3 3 3M2.75 9.75v2.5a1 1 0 0 0 1 1h8.5a1 1 0 0 0 1-1v-2.5"/>',
  alert: '<path d="M8 2.5 14.25 13H1.75zM8 6.75v2.75M8 11.3v.1"/>',
  info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.25v3.5M8 5.1v.1"/>',
  home: '<path d="M2.5 7.25 8 2.5l5.5 4.75M4 6.5v6.75h8V6.5"/>',
  drive: '<rect x="2" y="9" width="12" height="4.25" rx="1"/><path d="M2.6 9.2 4.4 3.5a1 1 0 0 1 1-.75h5.2a1 1 0 0 1 1 .75l1.8 5.7M11.5 11.1v.1"/>',
  gauge: '<path d="M2.5 11.75a5.5 5.5 0 1 1 11 0M8 11.5l2.5-3.75"/>',
  face: '<circle cx="8" cy="8" r="5.75"/><path d="M6 6.9v.2M10 6.9v.2M5.75 9.75c.6.8 1.4 1.2 2.25 1.2s1.65-.4 2.25-1.2"/>',
  plate: '<rect x="1.75" y="4.75" width="12.5" height="6.5" rx="1.25"/><path d="M4.5 8h1M7.5 8h1M10.5 8h1"/>',
  lock: '<rect x="3.25" y="7" width="9.5" height="6.5" rx="1.25"/><path d="M5.25 7V5.25a2.75 2.75 0 0 1 5.5 0V7"/>',
  steps: '<circle cx="3.5" cy="8" r="1.75"/><circle cx="12.5" cy="8" r="1.75"/><path d="M5.25 8h1.5M9.25 8h1.5"/><circle cx="8" cy="8" r=".6" fill="currentColor"/>',
  sliders: '<path d="M2.5 4.5h4M9.5 4.5h4M2.5 11.5h7M12.5 11.5h1"/><circle cx="8" cy="4.5" r="1.5"/><circle cx="11" cy="11.5" r="1.5"/>',
  eye: '<path d="M1.5 8S4 3.75 8 3.75 14.5 8 14.5 8 12 12.25 8 12.25 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="1.9"/>',
  clock: '<circle cx="8" cy="8" r="5.75"/><path d="M8 4.75V8l2.25 1.5"/>',
  refresh: '<path d="M13 8a5 5 0 1 1-1.6-3.65M13 2.75v2.5h-2.5"/>',
  folderOpen: '<path d="M1.75 12V4.25a1 1 0 0 1 1-1h3l1.5 1.75h5a1 1 0 0 1 1 1v1"/><path d="M1.9 12.5 3.6 8a1 1 0 0 1 .95-.7h9.2a.6.6 0 0 1 .57.8l-1.4 4.2a1 1 0 0 1-.95.7H2.75a.9.9 0 0 1-.85-.5z"/>',
  pen: '<path d="M2.75 13.25 3.4 10.4l7.35-7.35a1.2 1.2 0 0 1 1.7 0l.5.5a1.2 1.2 0 0 1 0 1.7L5.6 12.6z"/><path d="m9.6 4.2 2.2 2.2"/>',
  zoom: '<circle cx="7" cy="7" r="4.25"/><path d="M10.25 10.25 13.5 13.5"/>',
  /* === mode avancé : ajouts === */
  undo: '<path d="M5.5 3 2.5 6l3 3"/><path d="M2.75 6h6.5a4 4 0 0 1 0 8H6.5"/>',
  redo: '<path d="m10.5 3 3 3-3 3"/><path d="M13.25 6h-6.5a4 4 0 0 0 0 8H9.5"/>',
  camera: '<path d="M1.75 5.25a1 1 0 0 1 1-1h1.9l1-1.5h4.7l1 1.5h1.9a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1H2.75a1 1 0 0 1-1-1z"/><circle cx="8" cy="8.4" r="2.4"/>',
  grid: '<rect x="2.25" y="2.25" width="4.75" height="4.75" rx="1"/><rect x="9" y="2.25" width="4.75" height="4.75" rx="1"/><rect x="2.25" y="9" width="4.75" height="4.75" rx="1"/><rect x="9" y="9" width="4.75" height="4.75" rx="1"/>',
  grip: '<path d="M6 4v.1M10 4v.1M6 8v.1M10 8v.1M6 12v.1M10 12v.1" stroke-width="2"/>',
  user: '<circle cx="8" cy="5.6" r="2.6"/><path d="M2.9 13.4a5.2 5.2 0 0 1 10.2 0"/>',
  /* === fin des ajouts du mode avancé === */
  /* === nouveau flux, mode avancé === */
  ban: '<circle cx="8" cy="8" r="5.75"/><path d="m3.95 3.95 8.1 8.1"/>',
  /* === fin : nouveau flux, mode avancé === */
  /* file d'attente : plusieurs vidéos à la suite */
  layers: '<path d="M8 2.25 14 5.4 8 8.55 2 5.4z"/><path d="m2.5 8.1 5.5 2.9 5.5-2.9M2.5 10.7 8 13.6l5.5-2.9"/>',
} as const;
export type IconName = keyof typeof paths;
</script>

<script setup lang="ts">
defineProps<{ name: IconName; size?: number }>();
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html : tracés statiques définis ci-dessus -->
  <svg
    class="icon" :width="size ?? 16" :height="size ?? 16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
    stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" v-html="paths[name]"
  ></svg>
</template>
