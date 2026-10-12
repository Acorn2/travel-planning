import { preparePrintQrs } from "./print.js";

function fallbackCopy(text) {
  const previousFocus = document.activeElement;
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  try {
    return document.execCommand("copy");
  } finally {
    field.remove();
    if (previousFocus) previousFocus.focus();
  }
}

export async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_error) {
    // Local file previews can deny Clipboard API access.
  }
  try {
    return fallbackCopy(text);
  } catch (_error) {
    return false;
  }
}

export function enhancePage({ onSelectDay, onSelectView } = {}) {
  const cleanups = [];
  const days = [...document.querySelectorAll("details.day")];
  let printing = false;
  let printState = null;

  function showDay(id, scroll = false) {
    const selected = days.find((day) => day.id === id);
    if (!selected) return;
    days.forEach((day) => { day.open = day === selected; });
    document.querySelectorAll("[data-day-target]").forEach((link) => {
      if (link.dataset.dayTarget === id) link.setAttribute("aria-current", "date");
      else link.removeAttribute("aria-current");
    });
    const dateLink = document.querySelector(`.day-picker [data-day-target="${id}"]`);
    const picker = dateLink?.parentElement;
    if (picker?.clientWidth) {
      const dateBounds = dateLink.getBoundingClientRect();
      const pickerBounds = picker.getBoundingClientRect();
      if (dateBounds.right > pickerBounds.right) picker.scrollLeft += dateBounds.right - pickerBounds.right;
      else if (dateBounds.left < pickerBounds.left) picker.scrollLeft -= pickerBounds.left - dateBounds.left;
    }
    if (scroll) {
      selected.querySelector("summary").focus({ preventScroll: true });
      selected.scrollIntoView({ block: "start" });
    }
  }

  days.forEach((day) => {
    const onToggle = () => {
      if (!printing && day.open) showDay(day.id);
    };
    day.addEventListener("toggle", onToggle);
    cleanups.push(() => day.removeEventListener("toggle", onToggle));
    const summary = day.querySelector("summary");
    const onActivate = () => {
      window.setTimeout(() => {
        if (!printing && day.open && document.documentElement.dataset.itineraryView === "detail") {
          window.history.replaceState(null, "", `#${day.id}`);
        }
      }, 0);
    };
    summary.addEventListener("click", onActivate);
    cleanups.push(() => summary.removeEventListener("click", onActivate));
  });
  if (days.length) showDay(days[0].id);

  document.querySelectorAll("[data-day-target], [data-open-view]").forEach((link) => {
    const onClick = (event) => {
      if (link.dataset.dayTarget && onSelectDay) {
        event.preventDefault();
        onSelectDay(link.dataset.dayTarget);
      } else if (link.dataset.openView && onSelectView) {
        event.preventDefault();
        onSelectView(link.dataset.openView);
        document.querySelector("main").scrollIntoView({ block: "start" });
      }
    };
    link.addEventListener("click", onClick);
    cleanups.push(() => link.removeEventListener("click", onClick));
  });

  const beforePrint = () => {
    if (printState) return;
    printing = true;
    preparePrintQrs();
    printState = [...document.querySelectorAll("details")].map((item) => [item, item.open]);
    printState.forEach(([item]) => { item.open = true; });
  };
  const afterPrint = () => {
    if (!printState) return;
    printState.forEach(([item, open]) => { item.open = open; });
    printState = null;
    delete document.documentElement.dataset.printEdition;
    window.setTimeout(() => { printing = false; }, 0);
  };
  window.addEventListener("beforeprint", beforePrint);
  window.addEventListener("afterprint", afterPrint);
  cleanups.push(() => window.removeEventListener("beforeprint", beforePrint));
  cleanups.push(() => window.removeEventListener("afterprint", afterPrint));

  document.querySelectorAll("[data-copy-destination]").forEach((button) => {
    const onClick = async () => {
      const destination = button.dataset.copyDestination || "";
      if (!destination) return;
      const copied = await copyText(destination);
      const status = button.closest(".route-handoff")?.querySelector(".route-copy-status");
      if (status) {
        status.textContent = copied
          ? `已复制“${destination}”，请在导航设备中粘贴并核对地点。`
          : `请在导航设备中搜索“${destination}”并核对地点。`;
      }
    };
    button.addEventListener("click", onClick);
    cleanups.push(() => button.removeEventListener("click", onClick));
  });

  document.querySelectorAll("[data-wechat-account]").forEach((button) => {
    const onClick = async () => {
      const account = button.dataset.wechatAccount || "";
      const menu = button.dataset.wechatMenu || "";
      const actions = button.closest(".actions");
      const status = actions?.querySelector(".wechat-account-status");
      const copied = await copyText(account);
      if (status) {
        status.textContent = copied
          ? `已复制“${account}”，请打开微信搜索${menu ? `，再进入“${menu}”` : ""}。`
          : `请打开微信搜索“${account}”${menu ? `，再进入“${menu}”` : ""}。`;
      }
      if (copied) {
        const original = button.textContent;
        button.textContent = "已复制，打开微信搜索";
        window.setTimeout(() => { button.textContent = original; }, 2400);
      }
    };
    button.addEventListener("click", onClick);
    cleanups.push(() => button.removeEventListener("click", onClick));
  });

  return {
    showDay,
    destroy() {
      cleanups.reverse().forEach((cleanup) => cleanup());
    },
  };
}
