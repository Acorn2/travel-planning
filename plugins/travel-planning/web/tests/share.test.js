import assert from "node:assert/strict";
import test from "node:test";
import { publicShareUrl, shareQrCode } from "../src/share.js";

test("local files, private hosts and draft URLs cannot be shared as published trips", () => {
  for (const url of ["file:///Users/me/trip.html", "http://localhost:4312/", "https://localhost/", "https://127.0.0.1/", "https://192.168.1.5/", "https://[::1]/", "https://travel.local/", "https://travel.test/", "https://abc123--trip.netlify.app/", "https://user:password@trip.netlify.app/"]) {
    assert.equal(publicShareUrl(url), "", url);
  }
});

test("all views of a published trip share one stable URL without query strings", () => {
  assert.equal(publicShareUrl("https://trip.netlify.app/#overview-view"), "https://trip.netlify.app/");
  assert.equal(publicShareUrl("https://trips.example.org/武当山/?utm_source=wechat#route-view"), "https://trips.example.org/%E6%AD%A6%E5%BD%93%E5%B1%B1/");
});

test("different itineraries retain their own paths on one shared site", () => {
  const first = "https://travel-book.netlify.app/trips/wudangshan-2026-10-06/";
  const second = "https://travel-book.netlify.app/trips/chengdu-2026-11-01/";
  assert.equal(publicShareUrl(first + "?utm_source=wechat#overview-view"), first);
  assert.equal(publicShareUrl(second + "#route-view"), second);
  assert.notEqual(publicShareUrl(first), publicShareUrl(second));
  assert.notEqual(shareQrCode(first), shareQrCode(second));
});

test("QR is embedded instead of sending itinerary URLs to a third party", () => {
  assert.match(shareQrCode("https://trip.netlify.app/"), /^data:image\/gif;base64,/);
});
