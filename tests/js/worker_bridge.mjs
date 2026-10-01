/*
 * Node bridge for the REAL desktop planning worker (tests only).
 *
 * static/js/planning_worker.js is a browser module worker: it talks through
 * `self.onmessage` / `self.postMessage` and fetches the catalog. This bridge
 * runs it inside a Node worker_thread with a `self` shim plus a file-backed
 * fetch stub, so the worker protocol can be exercised without a browser and a
 * regression in the actual worker file (not a copy) fails the harness.
 *
 * The catalog file path arrives through NS_TEST_CATALOG (inherited by worker
 * threads); the harness rewrites it between builds to simulate catalog edits.
 */
import { parentPort } from "node:worker_threads";
import { readFileSync } from "node:fs";

globalThis.self = {
  set onmessage(fn) { parentPort.on("message", (data) => fn({ data })); },
  get onmessage() { return null; },
  postMessage: (msg, opts) => parentPort.postMessage(msg, opts && opts.transfer),
};

globalThis.fetch = async () => {
  const list = JSON.parse(readFileSync(process.env.NS_TEST_CATALOG, "utf8"));
  return {
    ok: true, status: 200, statusText: "OK",
    json: async () => list,
    headers: { get: () => "application/json" },
  };
};

await import(new URL("../../static/js/planning_worker.js", import.meta.url).href);
