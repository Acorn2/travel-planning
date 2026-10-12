<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { copyText } from "./pageEnhancements.js";
import { publicShareUrl } from "./share.js";
import { printItinerary } from "./print.js";
import ShareDialog from "./ShareDialog.vue";

const tools = ref(null);
const pdfButton = ref(null);
const moreButton = ref(null);
const shareDialog = ref(null);
const activePanel = ref("");
const status = ref("");
const url = publicShareUrl(window.location.href);
let copying = false;

async function toggle(panel) {
  activePanel.value = activePanel.value === panel ? "" : panel;
  status.value = "";
  if (activePanel.value) {
    await nextTick();
    tools.value.querySelector(".tool-popover button").focus();
  }
}

function close(restoreFocus = false) {
  const trigger = activePanel.value === "pdf" ? pdfButton.value : moreButton.value;
  activePanel.value = "";
  if (restoreFocus) trigger?.focus();
}

function outside(event) {
  if (!tools.value.contains(event.target)) close();
}

function keydown(event) {
  if (event.key === "Escape" && activePanel.value) {
    event.preventDefault();
    close(true);
  }
}

function focusout(event) {
  if (!copying && event.relatedTarget && !tools.value.contains(event.relatedTarget)) close();
}

async function save(edition) {
  close(true);
  await nextTick();
  printItinerary(edition);
}

async function copyLink() {
  if (!url) {
    showShare();
    return;
  }
  let copied = false;
  copying = true;
  try {
    copied = await copyText(url);
  } finally {
    copying = false;
  }
  status.value = copied ? "链接已复制，可发给同行人。" : "复制未成功，请打开二维码窗口手动复制链接。";
}

async function showShare() {
  close(true);
  await nextTick();
  shareDialog.value.show();
}

onMounted(() => document.addEventListener("pointerdown", outside));
onBeforeUnmount(() => document.removeEventListener("pointerdown", outside));
</script>

<template>
  <div ref="tools" class="trip-tools" @keydown="keydown" @focusout="focusout">
    <button ref="pdfButton" type="button" class="save-pdf" :aria-expanded="activePanel === 'pdf'" aria-controls="pdf-options" @click="toggle('pdf')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5Z"/><path d="M14 3v5h5M8 13h8M8 17h5"/></svg>
      保存 PDF
    </button>
    <button ref="moreButton" type="button" class="more-tools" aria-label="更多行程操作" :aria-expanded="activePanel === 'more'" aria-controls="more-options" @click="toggle('more')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>
    </button>
    <section v-if="activePanel === 'pdf'" id="pdf-options" class="tool-popover" aria-label="选择 PDF 版本">
      <p class="tool-popover-title">把行程带在身边</p>
      <button type="button" class="pdf-option" @click="save('brief')"><strong>随身简版 <span>推荐</span></strong><small>每日安排、主推餐厅、地址与预约提醒</small></button>
      <button type="button" class="pdf-option" @click="save('full')"><strong>完整资料版</strong><small>包含全部备选、详细路线与核验依据</small></button>
      <p class="tool-help">随后打开系统打印窗口，请选择“存储为 PDF”或“另存为 PDF”。</p>
    </section>
    <section v-if="activePanel === 'more'" id="more-options" class="tool-popover more-popover" aria-label="更多行程操作">
      <button type="button" @click="copyLink">复制行程链接</button>
      <button type="button" @click="showShare">查看分享二维码</button>
      <button type="button" @click="save('brief')">打印随身简版</button>
      <p v-if="status" class="tool-status" role="status">{{ status }}</p>
    </section>
  </div>
  <ShareDialog ref="shareDialog" />
</template>
