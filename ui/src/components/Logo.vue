<script setup lang="ts">
// Emblème : une mosaïque, c'est-à-dire ce que le logiciel pose sur les visages et les plaques.
defineProps<{ size?: number }>();
</script>

<template>
  <svg class="logo" :width="size ?? 20" :height="size ?? 20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
    <rect width="20" height="20" rx="5" fill="#f5a623" />
    <g fill="#1b1203">
      <rect x="4" y="4" width="3.5" height="3.5" rx=".6" opacity=".9" />
      <rect x="8.25" y="4" width="3.5" height="3.5" rx=".6" opacity=".45" />
      <rect x="12.5" y="4" width="3.5" height="3.5" rx=".6" opacity=".2" />
      <rect x="4" y="8.25" width="3.5" height="3.5" rx=".6" opacity=".45" />
      <rect x="8.25" y="8.25" width="3.5" height="3.5" rx=".6" opacity=".9" />
      <rect x="12.5" y="8.25" width="3.5" height="3.5" rx=".6" opacity=".45" />
      <rect x="4" y="12.5" width="3.5" height="3.5" rx=".6" opacity=".2" />
      <rect x="8.25" y="12.5" width="3.5" height="3.5" rx=".6" opacity=".45" />
      <rect x="12.5" y="12.5" width="3.5" height="3.5" rx=".6" opacity=".9" />
    </g>
  </svg>
</template>
