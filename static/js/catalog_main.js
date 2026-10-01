/* Catalog-only desktop boot. The legacy planner shell keeps its own entry
   point in main.js; this shell binds only the FITS archive and its map. */

import { $, toast, initFocusTrap } from "./util.js";
import { S } from "./state.js";
import { bindArchive, applyArchiveUI, loadLibrary, exportLibrary } from "./archive.js";
import { bindSkyMap, renderSkyMap } from "./skymap.js";

const CHILD_KEY = "nightseam_catalog_child";
const CHILDREN = ["library", "import", "skymap"];
let libraryLoaded = false;
let libraryLoadPromise = null;

function bindCatalogHost() {
  const setCapabilities = (capabilities) => {
    const nativeChrome = capabilities?.window_chrome === "native";
    S.hostCapabilities = {
      native_folder_picker: capabilities?.native_folder_picker === true,
    };
    document.body.dataset.windowChrome = nativeChrome ? "native" : "browser";
  };
  setCapabilities(null);
  const sync = async () => {
    const bridge = window.pywebview?.api;
    if (!bridge || typeof bridge.host_capabilities !== "function") return;
    try {
      const capabilities = await bridge.host_capabilities();
      setCapabilities(capabilities);
    } catch {
      setCapabilities(null);
    }
  };
  void sync();
  window.addEventListener("pywebviewready", sync);
}

function updateVersionTag() {
  const version = S.cfg?.version;
  $("#versionTag").textContent = version
    ? `NightSeam v${version} · Image Catalog` : "Image Catalog";
}

function ensureLibraryLoaded() {
  if (libraryLoaded && S.library) return Promise.resolve(true);
  if (libraryLoadPromise) return libraryLoadPromise;
  libraryLoadPromise = loadLibrary().then((loaded) => {
    libraryLoaded = Boolean(loaded);
    if (libraryLoaded) updateVersionTag();
    return libraryLoaded;
  }).finally(() => { libraryLoadPromise = null; });
  return libraryLoadPromise;
}

function selectChild(button, { remember = true } = {}) {
  if (!button) return;
  const child = button.dataset.tab;
  if (!CHILDREN.includes(child)) return;
  const tabs = [...document.querySelectorAll("#catalogTabs [role='tab']")];
  for (const tab of tabs) {
    const active = tab === button;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  for (const pane of document.querySelectorAll("#tab-image-catalog > .catpane")) {
    const active = pane.id === `tab-${child}`;
    pane.classList.toggle("active", active);
    pane.hidden = !active;
  }
  if (remember) {
    try { sessionStorage.setItem(CHILD_KEY, child); } catch {}
  }
  if (child === "library") void ensureLibraryLoaded();
  if (child === "skymap") renderSkyMap();
}

function bindCatalogTabs() {
  const nav = $("#catalogTabs");
  const tabs = [...nav.querySelectorAll("[role='tab']")];
  for (const tab of tabs) {
    tab.addEventListener("click", () => selectChild(tab));
  }
  nav.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const current = Math.max(0, tabs.indexOf(event.target.closest("[role='tab']")));
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
      (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next].focus();
    selectChild(tabs[next]);
  });

  let saved = "library";
  try {
    const candidate = sessionStorage.getItem(CHILD_KEY);
    if (CHILDREN.includes(candidate)) saved = candidate;
  } catch {}
  selectChild(tabs.find((tab) => tab.dataset.tab === saved) || tabs[0],
    { remember: false });
}

function bindTopActions() {
  $("#btnTopIngest").addEventListener("click", () => {
    selectChild($("#catalog-tab-import"));
    $("#btnIngest").click();
  });
  $("#btnTopReindex").addEventListener("click", () => $("#btnArcReindex").click());
  $("#btnTopExport").addEventListener("click", () => exportLibrary());
}

async function initCatalog() {
  document.body.dataset.primaryView = "image-catalog";
  document.body.classList.add("image-catalog-view", "catalog-only");
  bindCatalogHost();
  if (new URLSearchParams(window.location.search).has("planner-unavailable")) {
    const notice = document.createElement("div");
    notice.className = "catalog-launch-notice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.textContent = "The full planner could not open in the running NightSeam window. Close that window, then relaunch with --planner.";
    document.querySelector("#topbar").after(notice);
  }
  updateVersionTag();
  bindCatalogTabs();
  bindTopActions();
  bindArchive();
  bindSkyMap();
  initFocusTrap();
  const objectsToggle = $("#libraryObjectsToggle");
  const discloseObjects = (expanded) => {
    objectsToggle.setAttribute("aria-expanded", String(expanded));
    objectsToggle.textContent = expanded ? "Hide objects" : "Show objects";
    objectsToggle.closest(".library-object-rail").classList.toggle("objects-expanded", expanded);
  };
  objectsToggle.addEventListener("click", () => {
    discloseObjects(objectsToggle.getAttribute("aria-expanded") !== "true");
  });
  $("#libSearch").addEventListener("input", () => {
    if (window.matchMedia("(max-width: 760px)").matches) discloseObjects(true);
  });

  await ensureLibraryLoaded();
  applyArchiveUI();
  updateVersionTag();
}

initCatalog().catch((error) => {
  console.error(error);
  toast(`Image Catalog failed to start: ${error.message}`);
});
