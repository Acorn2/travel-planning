<script setup>
import { nextTick, onBeforeUnmount, ref } from "vue";
import { copyText } from "./pageEnhancements.js";
import { publicShareUrl, shareQrCode } from "./share.js";

const open = ref(false);
const panel = ref(null);
const closeButton = ref(null);
const url = publicShareUrl(window.location.href);
const status = ref("");
let qr = "";
try { if (url) qr = shareQrCode(url); } catch (_error) { /* Link sharing remains available. */ }
const release = document.querySelector('meta[name="itinerary-release-at"]');
const releaseDate = release ? new Date(release.content) : null;
const releasedAt = releaseDate && !Number.isNaN(releaseDate.getTime())
  ? releaseDate.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
  : "";
let previousFocus;
let previousOverflow;

async function show() {
  previousFocus = document.activeElement;
  previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  status.value = "";
  open.value = true;
  await nextTick();
  closeButton.value.focus();
}

function close() {
  if (!open.value) return;
  open.value = false;
  document.body.style.overflow = previousOverflow;
  if (previousFocus) previousFocus.focus();
}

function keydown(event) {
  if (event.key === "Escape") { event.preventDefault(); close(); }
  if (event.key !== "Tab") return;
  const controls = Array.from(panel.value.querySelectorAll("button, input, a[href]"));
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

async function copy() {
  status.value = await copyText(url) ? "链接已复制，可粘贴到微信或发给同行人。" : "复制未成功，请长按上方链接手动复制。";
}

onBeforeUnmount(close);
defineExpose({ show });
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="share-overlay" @click.self="close" @keydown="keydown">
      <section ref="panel" class="share-dialog" role="dialog" aria-modal="true" aria-labelledby="share-title" aria-describedby="share-description">
        <button ref="closeButton" class="share-close" type="button" aria-label="关闭分享窗口" @click="close">×</button>
        <span class="kicker">同行 · 一起出发</span>
        <h2 id="share-title">把行程带给同行人</h2>
        <template v-if="url">
          <p id="share-description">无需下载文件，手机打开链接就能查看。行程更新后，刷新同一链接即可。</p>
          <figure v-if="qr" class="share-qr">
            <img :src="qr" width="216" height="216" alt="扫码打开这份行程" />
            <figcaption>扫一扫，在手机上查看</figcaption>
          </figure>
          <label class="share-link-label" for="share-link">行程链接</label>
          <input id="share-link" class="share-link" :value="url" readonly @focus="$event.target.select()" />
          <div class="share-actions">
            <button type="button" @click="copy">复制链接</button>
            <a v-if="qr" :href="qr" download="itinerary-qr.gif">保存二维码</a>
          </div>
          <p class="share-status" role="status" aria-live="polite">{{ status }}</p>
          <p v-if="releasedAt" class="share-release">本版生成：{{ releasedAt }}</p>
          <p class="share-footnote">持有链接的人都能查看，请只转发给需要的同行人。</p>
        </template>
        <template v-else>
          <p id="share-description">这份行程还在本地或预览环境，暂时没有可转发的公网链接。</p>
          <div class="share-local-note">发布到线上后，再打开正式页面，即可复制链接、生成和保存二维码。</div>
          <p class="share-footnote">现在可以使用“保存 PDF”留一份备份。</p>
        </template>
      </section>
    </div>
  </Teleport>
</template>
