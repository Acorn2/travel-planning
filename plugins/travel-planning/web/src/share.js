import qrcode from "qrcode-generator";

export function publicShareUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    if (url.protocol !== "https:" || url.username || url.password || !host.includes(".") ||
        host.includes(":") || /^[\d.]+$/.test(host) ||
        /(^|\.)(localhost|local|test|internal|invalid)$/.test(host) ||
        /^[a-z0-9]+--.*\.netlify\.app$/.test(host)) return "";
    url.hash = "";
    url.search = "";
    return url.href;
  } catch (_error) {
    return "";
  }
}

export function shareQrCode(url) {
  const code = qrcode(0, "M");
  code.addData(url, "Byte");
  code.make();
  return code.createDataURL(6, 24);
}
