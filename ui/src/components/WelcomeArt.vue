<template>
  <!-- Illustration de l'accueil : une image de vidéo avec ses trois zones de flou, comme dans le lecteur. -->
  <svg class="art" viewBox="0 0 360 202" role="img" aria-label="Une image de vidéo dont le visage, la plaque et le compteur sont floutés">
    <defs>
      <clipPath id="art-frame"><rect width="360" height="202" rx="12" /></clipPath>
      <pattern id="art-mosaic" width="8" height="8" patternUnits="userSpaceOnUse">
        <rect width="8" height="8" fill="#59616b" />
        <rect width="4" height="4" fill="#7b838d" />
        <rect x="4" y="4" width="4" height="4" fill="#454c55" />
      </pattern>
    </defs>
    <g clip-path="url(#art-frame)">
      <rect width="360" height="202" fill="#15181b" />
      <path d="M0 96V70h28V54h30v22h22V46h26v30h40v20zM226 96V62h34v-14h28v24h26V58h46v38z" fill="#191c20" />
      <rect y="96" width="360" height="106" fill="#1b1f23" />
      <path d="M158 96 36 202h288L202 96z" fill="#22262b" />
      <path d="M180 104v10M180 124v14M180 150v20" stroke="#4a515a" stroke-width="2" stroke-linecap="round" />
      <path d="M0 96h360" stroke="#2c3137" />
      <!-- piéton -->
      <path d="M60 160v-36a14 14 0 0 1 28 0v36z" fill="#2f353c" />
      <circle cx="74" cy="100" r="10" fill="#3a4149" />
      <!-- voiture -->
      <rect x="214" y="92" width="52" height="34" rx="7" fill="#2f353c" />
      <rect x="221" y="97" width="38" height="11" rx="3" fill="#1b1f23" />
      <rect x="229" y="114" width="22" height="7" rx="1.5" fill="#d7dbe0" />
      <!-- poste de pilotage -->
      <path d="M52 202c22-34 62-48 128-48s106 14 128 48z" fill="#0f1113" />
      <rect x="150" y="166" width="60" height="26" rx="5" fill="#2a3037" />
      <path d="M160 184a8 8 0 0 1 16 0M168 184l4-5M186 174h16M186 180h10" stroke="#8a929c" stroke-width="1.5" stroke-linecap="round" fill="none" />
    </g>
    <g class="z z1">
      <ellipse cx="74" cy="100" rx="15" ry="17" fill="url(#art-mosaic)" />
      <ellipse cx="74" cy="100" rx="15" ry="17" fill="none" stroke="#4ade80" stroke-width="1.5" />
      <rect x="59" y="70" width="46" height="11" fill="#000" opacity=".65" /><text x="62" y="78.5" fill="#4ade80">visage 0.93</text>
    </g>
    <g class="z z2">
      <rect x="225" y="110" width="30" height="15" rx="4" fill="url(#art-mosaic)" />
      <rect x="225" y="110" width="30" height="15" rx="4" fill="none" stroke="#38bdf8" stroke-width="1.5" />
      <rect x="225" y="130" width="48" height="11" fill="#000" opacity=".65" /><text x="228" y="138.5" fill="#38bdf8">plaque 0.88</text>
    </g>
    <g class="z z3">
      <rect x="146" y="162" width="68" height="34" rx="3" fill="url(#art-mosaic)" />
      <rect x="146" y="162" width="68" height="34" rx="3" fill="none" stroke="#f472b6" stroke-width="1.5" />
      <rect x="146" y="148" width="41" height="11" fill="#000" opacity=".65" /><text x="149" y="156.5" fill="#f472b6">compteur</text>
    </g>
    <rect x=".5" y=".5" width="359" height="201" rx="11.5" fill="none" stroke="#2f343b" />
  </svg>
</template>
