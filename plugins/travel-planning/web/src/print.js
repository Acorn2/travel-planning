import { shareQrCode } from "./share.js";

export function preparePrintQrs() {
  document.querySelectorAll("[data-print-map]").forEach((link) => {
    if (link.querySelector("img")) return;
    try {
      const url = new URL(link.getAttribute("href"));
      if (url.protocol !== "https:" || url.username || url.password) return;
      const image = document.createElement("img");
      image.src = shareQrCode(url.href);
      image.alt = `扫码查看${link.dataset.printMap}的位置`;
      image.width = 80;
      image.height = 80;
      link.prepend(image);
    } catch (_error) {
      // The address and clickable map link remain usable without a QR code.
    }
  });
}

export function printItinerary(edition = "brief") {
  document.documentElement.dataset.printEdition = edition === "full" ? "full" : "brief";
  preparePrintQrs();
  window.print();
}
