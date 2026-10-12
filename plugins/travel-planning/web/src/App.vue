<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { enhancePage } from "./pageEnhancements.js";
import TripTools from "./TripTools.vue";

defineProps({
  panels: {
    type: Object,
    required: true,
  },
  tailHtml: {
    type: String,
    default: "",
  },
});

const views = [
  { id: "focus", label: "玩什么·吃什么", panel: "focus-view" },
  { id: "overview", label: "全程概览", panel: "overview-view" },
  { id: "detail", label: "每日行程", panel: "detail-view" },
  { id: "prepare", label: "出发准备", panel: "prepare-view" },
];
const activeView = ref("focus");
const tabElements = ref([]);
let pageEnhancements = null;

function applyView() {
  document.documentElement.dataset.itineraryView = activeView.value;
}

function selectView(view, focus = false) {
  activeView.value = view.id;
  if (window.history && window.history.replaceState) {
    const day = view.id === "detail" ? document.querySelector("details.day[open]") : null;
    window.history.replaceState(null, "", `#${day ? day.id : view.panel}`);
  }
  if (focus) {
    nextTick(() => {
      const index = views.findIndex((item) => item.id === view.id);
      const element = tabElements.value[index];
      if (element) element.focus();
    });
  }
}

function onKeydown(event, index) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  let next = index;
  if (event.key === "Home") next = 0;
  else if (event.key === "End") next = views.length - 1;
  else if (event.key === "ArrowRight") next = (index + 1) % views.length;
  else next = (index - 1 + views.length) % views.length;
  selectView(views[next], true);
}

function openDay(id, scroll = true) {
  selectView(views.find((view) => view.id === "detail"));
  nextTick(() => {
    pageEnhancements.showDay(id, scroll);
    window.history.replaceState(null, "", `#${id}`);
  });
}

function restoreHash() {
  const hash = window.location.hash;
  if (/^#day-\d+$/.test(hash)) {
    openDay(hash.slice(1));
    return;
  }
  const hashView = views.find((view) => `#${view.panel}` === hash);
  activeView.value = hash === "#route-view" ? "overview" : (hashView ? hashView.id : "focus");
  if (hash === "#route-view") {
    nextTick(() => {
      const route = document.getElementById("route-view");
      if (route) {
        route.closest("details").open = true;
        route.scrollIntoView();
      }
    });
  }
}

onMounted(() => {
  pageEnhancements = enhancePage({
    onSelectDay: openDay,
    onSelectView: (id) => selectView(views.find((view) => view.id === id)),
  });
  restoreHash();
  window.addEventListener("hashchange", restoreHash);
  document.documentElement.dataset.itineraryEnhanced = "true";
  applyView();
});

watch(activeView, applyView, { flush: "post" });

onBeforeUnmount(() => {
  window.removeEventListener("hashchange", restoreHash);
  if (pageEnhancements) pageEnhancements.destroy();
  delete document.documentElement.dataset.itineraryEnhanced;
  delete document.documentElement.dataset.itineraryView;
});
</script>

<template>
  <Teleport to="#itinerary-tools">
    <TripTools />
  </Teleport>
  <Teleport to="#itinerary-view-controls">
    <nav class="view-switcher" role="tablist" aria-label="行程展示方式">
      <a
        v-for="(view, index) in views"
        :key="view.id"
        :ref="(element) => { if (element) tabElements[index] = element; }"
        :href="`#${view.panel}`"
        role="tab"
        :aria-selected="String(activeView === view.id)"
        :aria-controls="view.panel"
        :tabindex="activeView === view.id ? 0 : -1"
        @click.prevent="selectView(view)"
        @keydown="onKeydown($event, index)"
      >
        {{ view.label }}
      </a>
    </nav>
  </Teleport>
  <section
    v-show="activeView === 'focus'"
    :hidden="activeView !== 'focus'"
    id="focus-view"
    data-itinerary-view="focus"
    aria-labelledby="focus-heading"
    v-html="panels['focus-view']"
  />
  <section
    v-show="activeView === 'detail'"
    :hidden="activeView !== 'detail'"
    id="detail-view"
    data-itinerary-view="detail"
    v-html="panels['detail-view']"
  />
  <section
    v-show="activeView === 'overview'"
    :hidden="activeView !== 'overview'"
    id="overview-view"
    class="itinerary-overview"
    data-itinerary-view="overview"
    aria-labelledby="handbook-heading"
    v-html="panels['overview-view']"
  />
  <section
    v-show="activeView === 'prepare'"
    :hidden="activeView !== 'prepare'"
    id="prepare-view"
    class="itinerary-prepare"
    data-itinerary-view="prepare"
    aria-labelledby="prepare-heading"
    v-html="panels['prepare-view']"
  />
  <div class="itinerary-tail" v-html="tailHtml" />
</template>
