/* On-demand grayscale previews for the selected capture session. */
import { $ } from "./util.js";

let images = [], selectedId = null, controller = null, url = null;
let generation = 0, requested = false, viewerInert = [], viewerOpener = null;

function closeViewer(restoreFocus = true) {
  $("#fitsPreviewModal").classList.add("hidden");
  $("#fitsPreviewLarge").removeAttribute("src");
  for (const [element, wasInert] of viewerInert) element.inert = wasInert;
  viewerInert = [];
  if (restoreFocus) viewerOpener?.focus();
}

function filename(image) {
  return image.orig_name || String(image.file || "").split(/[\\/]/).pop() || `Exposure ${image.id}`;
}

function selectedImage() {
  return images.find(image => image.id === selectedId);
}

function openViewer() {
  if (!url || $("#libraryPreviewImage").hidden || document.querySelector('[role="dialog"]:not(.hidden)')) return;
  viewerOpener = document.activeElement;
  viewerInert = [...document.querySelectorAll("body > header, body > main")].map(element => [element, element.inert]);
  viewerInert.forEach(([element]) => { element.inert = true; });
  $("#fitsPreviewFilename").textContent = filename(selectedImage());
  $("#fitsPreviewLarge").src = url;
  $("#fitsPreviewLarge").alt = $("#libraryPreviewImage").alt;
  $("#fitsPreviewCaption").textContent = $("#libraryPreviewCaption").textContent;
  $("#fitsPreviewModal").classList.remove("hidden");
  $("#fitsPreviewClose").focus();
}

function resetRequest() {
  closeViewer(false);
  generation += 1;
  controller?.abort();
  controller = null;
  const image = $("#libraryPreviewImage");
  image.hidden = true;
  image.removeAttribute("src");
  if (url) URL.revokeObjectURL(url);
  url = null;
  $("#libraryPreviewCaption").textContent = "";
  $("#libraryPreviewShow").disabled = false;
  $("#libraryPreviewExpand").hidden = true;
}

export function clearExposurePreview() {
  resetRequest();
  requested = false;
  $("#libraryExposurePreview").hidden = true;
}

function describeSelection() {
  const index = images.findIndex(image => image.id === selectedId);
  const image = images[index];
  if (!image) return;
  const details = [image.imagetyp, image.filter,
    typeof image.exposure === "number" && Number.isFinite(image.exposure) ? `${image.exposure}s` : null].filter(Boolean);
  $("#libraryPreviewInfo").textContent = `${index + 1} of ${images.length}${details.length ? ` · ${details.join(" · ")}` : ""}`;
}

export function setPreviewImages(sessionImages) {
  clearExposurePreview();
  images = sessionImages.filter(image => Number.isSafeInteger(image.id) && image.id > 0);
  if (!images.length) return;
  if (!images.some(image => image.id === selectedId)) selectedId = images[0].id;
  const selector = $("#libraryPreviewExposure");
  selector.replaceChildren();
  for (const image of images) {
    const option = document.createElement("option");
    option.value = String(image.id);
    option.textContent = filename(image);
    selector.appendChild(option);
  }
  selector.value = String(selectedId);
  describeSelection();
  $("#libraryPreviewShow").textContent = "Show preview";
  $("#libraryPreviewStatus").textContent = "Load a grayscale preview of this exposure.";
  $("#libraryExposurePreview").hidden = false;
}

async function loadPreview() {
  const exposure = selectedImage();
  if (!exposure) return;
  resetRequest();
  requested = true;
  const requestGeneration = generation, requestController = new AbortController();
  controller = requestController;
  const timeout = setTimeout(() => requestController.abort(), 30000);
  const button = $("#libraryPreviewShow");
  button.disabled = true;
  $("#libraryPreviewStatus").textContent = "Loading grayscale FITS preview...";
  try {
    const response = await fetch(`/api/images/${exposure.id}/preview`, { signal: requestController.signal });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Preview unavailable (HTTP ${response.status}).`);
    }
    if (!response.headers.get("Content-Type")?.startsWith("image/png")) throw new Error("The server did not return a preview image.");
    const blob = await response.blob();
    if (requestGeneration !== generation) return;
    const image = $("#libraryPreviewImage");
    url = URL.createObjectURL(blob);
    image.src = url;
    image.alt = `Grayscale FITS preview of ${filename(exposure)}`;
    await image.decode();
    if (requestGeneration !== generation) return;
    image.hidden = false;
    $("#libraryPreviewExpand").hidden = false;
    const size = response.headers.get("X-FITS-Size"), hdu = response.headers.get("X-FITS-HDU");
    const plane = response.headers.get("X-FITS-Plane") === "1" ? " · first plane" : "";
    $("#libraryPreviewCaption").textContent = `${size ? `${size.replace("x", " × ")} pixels · ` : ""}${hdu !== null ? `HDU ${hdu}${plane} · ` : ""}Grayscale · automatic stretch · no debayering`;
    $("#libraryPreviewStatus").textContent = "Preview ready.";
    button.textContent = "Refresh preview";
  } catch (error) {
    if (requestGeneration !== generation) return;
    resetRequest();
    $("#libraryPreviewStatus").textContent = error.name === "AbortError"
      ? "Preview took too long. Try again." : `Preview unavailable: ${error.message}`;
    button.textContent = "Retry preview";
  } finally {
    clearTimeout(timeout);
    if (requestGeneration === generation) {
      controller = null;
      button.disabled = false;
    }
  }
}

export function bindExposurePreview() {
  $("#libraryPreviewShow").addEventListener("click", loadPreview);
  $("#libraryPreviewExposure").addEventListener("change", () => {
    selectedId = Number($("#libraryPreviewExposure").value);
    describeSelection();
    if (requested) void loadPreview();
  });
  window.addEventListener("pagehide", clearExposurePreview);
  $("#libraryPreviewExpand").addEventListener("click", openViewer);
  $("#fitsPreviewClose").addEventListener("click", () => closeViewer());
  $("#fitsPreviewModal").addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeViewer(); }
  });
}
