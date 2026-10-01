/*
 * Web backup / restore integrity checks.
 *
 * Drives the REAL web/webstore.js + web/horizon.js through a minimal fake
 * IndexedDB and a localStorage stub (no browser here). Pins:
 *   - normalized panorama records are TRUTHFUL: hasImage === true only when the
 *     backup actually carried image bytes
 *   - the profile/strokes invariant: strokes win when present, the stored
 *     profile is authoritative when there are no strokes
 *   - stageBackup validation (a malformed bundle throws BEFORE any write)
 *   - collectBackup carries the theme (its own localStorage key) and a schema
 *     version
 *   - a restore failure rolls back to the pre-restore snapshot
 * Prints one marker line; tests/test_js_behaviour.py asserts it.
 */

globalThis.localStorage = {
  _m: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._m, k) ? this._m[k] : null; },
  setItem(k, v) { this._m[k] = String(v); },
  removeItem(k) { delete this._m[k]; },
};

// FileReader is not in Node; enough to turn a Blob into a data URL for the
// exportPanosWithImages path.
globalThis.FileReader = class {
  readAsDataURL(blob) {
    blob.arrayBuffer().then((buf) => {
      const b64 = Buffer.from(new Uint8Array(buf)).toString('base64');
      this.result = `data:${blob.type || 'image/jpeg'};base64,${b64}`;
      if (this.onload) this.onload();
    }).catch((e) => { if (this.onerror) this.onerror(e); });
  }
};

/* ---------------------------------------------------- minimal fake IDB --- */
const _stores = new Map();
const ensure = (n) => { if (!_stores.has(n)) _stores.set(n, new Map()); };
const req = () => ({ onsuccess: null, onerror: null, result: null, error: null });
const micro = (fn) => queueMicrotask(fn);

globalThis.indexedDB = {
  open() {
    const rq = req();
    micro(() => {
      const db = {
        objectStoreNames: { contains: (n) => _stores.has(n) },
        createObjectStore: (n) => { ensure(n); return {}; },
        transaction: (name) => ({
          objectStore: () => ({
            get(key) {
              const r = req();
              micro(() => { const s = _stores.get(name); r.result = s && s.has(key) ? s.get(key) : null; r.onsuccess && r.onsuccess(); });
              return r;
            },
            put(val, key) {
              const r = req();
              micro(() => {
                const fc = globalThis.__idbFailPutCount && globalThis.__idbFailPutCount[name];
                if (fc > 0) { globalThis.__idbFailPutCount[name] = fc - 1; r.onerror && r.onerror(); return; }
                ensure(name); _stores.get(name).set(key, val);
                r.result = key; r.onsuccess && r.onsuccess();
              });
              return r;
            },
            delete(key) {
              const r = req();
              micro(() => { const s = _stores.get(name); if (s) s.delete(key); r.onsuccess && r.onsuccess(); });
              return r;
            },
            clear() {
              const r = req();
              micro(() => { ensure(name); _stores.get(name).clear(); r.onsuccess && r.onsuccess(); });
              return r;
            },
            openCursor() {
              const r = req();
              micro(() => {
                const entries = [...(_stores.get(name) || new Map()).entries()];
                let i = 0;
                const step = () => {
                  if (i < entries.length) {
                    const [key, value] = entries[i++];
                    r.result = { key, value, continue: step };
                    r.onsuccess && r.onsuccess();
                  } else { r.result = null; r.onsuccess && r.onsuccess(); }
                };
                step();
              });
              return r;
            },
          }),
        }),
      };
      rq.result = db;
      if (rq.onupgradeneeded) rq.onupgradeneeded();
      rq.onsuccess && rq.onsuccess();
    });
    return rq;
  },
};

const WS = await import(new URL('../../web/webstore.js', import.meta.url).href);
const H = await import(new URL('../../web/horizon.js', import.meta.url).href);

let ok = true;
const fail = (m) => { ok = false; console.log('  FAIL:', m); };
const assert = (cond, m) => { if (!cond) fail(m); };

const DATAURL = 'data:image/jpeg;base64,' + Buffer.from('JPEGBYTES').toString('base64');
const blank720 = () => new Array(720).fill(-90);
const emptyPanos = { panos: [], activeId: null };
const panoRec = (over = {}) => ({
  id: 'p1', name: 'p', azOffset: 0, altBottom: 0, altTop: 60, fov: 360,
  paintMode: 'obstacle', strokes: [], profile: blank720(), profileTop: blank720(),
  hasImage: false, ...over,
});

/* 1. normalizePanos truthfulness ---------------------------------------- */
const withImg = H.normalizePanos({ panos: [panoRec({ hasImage: true, image: DATAURL })] });
assert(withImg.panos[0].hasImage === true, 'image present should set hasImage true');
assert(typeof withImg.panos[0].image === 'string', 'image should be preserved');
const noImg = H.normalizePanos({ panos: [panoRec({ hasImage: true })] });
assert(noImg.panos[0].hasImage === false, 'legacy hasImage without bytes must become false');
assert(noImg.panos[0].image === undefined, 'no image field when absent');
assert(H.normalizePanos({ panos: [panoRec({ blockAbove: true })] })
  .panos[0].blockAbove === true, 'blockAbove must round-trip through normalizePanos');
assert(H.normalizePanos({ panos: [panoRec()] }).panos[0].blockAbove === false,
  'blockAbove defaults to false (open above)');

/* 2. profile/strokes invariant ------------------------------------------ */
const bogus = blank720();
bogus[100] = 42;
const strokesWin = H.normalizePanos({ panos: [panoRec({
  profile: bogus,
  strokes: [{ m: 'b', x: 100, y: 50, r: 10 }],
})] });
assert(strokesWin.panos[0].profile[100] !== 42, 'strokes present must re-derive the profile');
const profileWins = H.normalizePanos({ panos: [panoRec({ profile: bogus })] });
assert(profileWins.panos[0].profile[100] === 42, 'no strokes must keep the stored profile');

/* 3. stageBackup validation --------------------------------------------- */
let threw = false;
try { WS.stageBackup({ app: 'other' }); } catch { threw = true; }
assert(threw, 'stageBackup must reject a foreign bundle');
const staged = WS.stageBackup({
  app: 'nightseam-web', theme: 'classic',
  sites: [{ id: 's1', name: 'Home', lat: 1, lon: 2 }],
  rigs: [], targets: [], prefs: { bortle: 5 }, panos: emptyPanos,
});
assert(staged.theme === 'workbench', 'legacy classic theme migrates to workbench');
assert(staged.sites.length === 1, 'one valid site staged');
assert(WS.stageBackup({ app: 'nightseam-web', theme: 'weird', panos: emptyPanos }).theme === 'workbench',
  'unknown theme value must normalize to workbench');

/* 4. exportPanosWithImages -> normalize round trip (image survives) ------ */
H.applyPanosSnapshot({
  panos: [panoRec({ hasImage: true, image: DATAURL })],
  activeId: 'p1',
});
const exported = await H.exportPanosWithImages();
assert(exported.panos[0].hasImage === true, 'export keeps hasImage when the photo exists');
assert(typeof exported.panos[0].image === 'string', 'export embeds the photo as a data URL');
H.applyPanosSnapshot(H.normalizePanos(exported));
assert(H.activePano().hasImage === true, 'round-tripped pano still has its image');

/* 5. collectBackup + restore round trip --------------------------------- */
await WS.writeStaged(WS.stageBackup({
  app: 'nightseam-web',
  sites: [{ id: 's1', name: 'Home', lat: 1, lon: 2 }],
  rigs: [{ id: 'r1', name: 'Rig', focal_mm: 100 }],
  targets: [], panos: emptyPanos,
}));
const backup = await WS.collectBackup({ bortle: 6 }, { panos: [], activeId: null }, 'classic');
assert(backup.app === 'nightseam-web' && backup.version === 2, 'backup carries schema version 2');
assert(backup.theme === 'workbench', 'collectBackup carries the canonical theme');
assert(backup.sites.length === 1 && backup.rigs.length === 1, 'collectBackup reads the stores');

/* 5b. View full day preference round-trips (ITEM 1) ---------------------- */
const vfd = await WS.collectBackup(
  { bortle: 6, viewFullDay: true }, { panos: [], activeId: null }, 'classic');
assert(vfd.prefs.viewFullDay === true, 'collectBackup carries viewFullDay');
assert(WS.stageBackup(vfd).prefs.viewFullDay === true, 'stageBackup preserves viewFullDay');
assert(WS.stageBackup({ app: 'nightseam-web', prefs: { viewFullDay: false }, panos: emptyPanos }).prefs.viewFullDay === false,
  'an imported bundle keeps the viewFullDay value');

/* 6. malformed restore does NOT destroy current data -------------------- */
const beforeSites = (await WS.all('sites')).length;
let bad = false;
try { await WS.restoreBackup({ app: 'nightseam-web', sites: [], panos: null }, null); } catch { bad = true; }
assert(bad && (await WS.all('sites')).length === beforeSites,
  'a bundle missing its panorama section is rejected before any write');
await WS.writeStaged(WS.stageBackup({
  app: 'nightseam-web', sites: [{ id: 's1', name: 'Home', lat: 1, lon: 2 }], panos: emptyPanos,
}));
const countBefore = (await WS.all('sites')).length;
let rejected = false;
try { await WS.restoreBackup({ app: 'nope', panos: emptyPanos }, null); } catch { rejected = true; }
assert(rejected && (await WS.all('sites')).length === countBefore,
  'invalid bundle throws and leaves storage intact');

/* 7. write failure rolls back to the pre-restore snapshot --------------- */
const safety = await WS.collectBackup({ bortle: 6 }, { panos: [], activeId: null }, 'classic');
const incoming = {
  app: 'nightseam-web',
  sites: [{ id: 's-new', name: 'New', lat: 9, lon: 9 }],
  panos: emptyPanos,
};
const safetyFailureBefore = (await WS.all('sites')).map((r) => r.val.id).sort();
globalThis.__idbFailPutCount = { meta: 1 };
let safetyFailed = false;
try { await WS.restoreBackup(incoming, safety); } catch { safetyFailed = true; }
assert(safetyFailed, 'a safety snapshot write failure must stop the restore');
const afterSafetyFailure = (await WS.all('sites')).map((r) => r.val.id).sort();
assert(JSON.stringify(afterSafetyFailure) === JSON.stringify(safetyFailureBefore),
  'a failed safety snapshot must leave data untouched');
globalThis.__idbFailPutCount = { sites: 1 };
let failed = false;
try { await WS.restoreBackup(incoming, safety); } catch { failed = true; }
assert(failed, 'a failed write must surface an error');
const afterFail = await WS.all('sites');
assert(afterFail.length === safety.sites.length,
  `rollback should restore ${safety.sites.length} site(s), got ${afterFail.length}`);
assert(afterFail.some((r) => r.val.id === 's1') && !afterFail.some((r) => r.val.id === 's-new'),
  'rollback must restore the pre-restore records');

/* 8. Phase-1 migrations: legacy FOV-relative panos and degree rigs ------- */
// A pre-0.6.16 web panorama stored 720 FOV-relative mask columns. With
// fov=180, azOffset=90 a wall across the WHOLE mask must migrate to the world
// bins of az 90..270 and block exactly those directions.
const legacyProf = new Array(720).fill(-90);
const legacyTop = new Array(720).fill(-90);
for (let i = 0; i < 720; i++) legacyProf[i] = 20;      // wall over the whole FOV
const legacyPano = panoRec({ fov: 180, azOffset: 90, profile: legacyProf,
                             profileTop: legacyTop, profileBins: undefined });
const migrated = H.normalizePanos({ panos: [legacyPano] }).panos[0];
assert(migrated.profileBins === 'world', 'legacy profile must migrate to world bins');
// independent expectation: desktop representative column per world bin
const wantWorld = (i) => {
  const rel = ((i * 0.5 - 90) % 360 + 360) % 360;
  return rel <= 180 ? legacyProf[Math.min(719, Math.floor(rel * 720 / 180))] : -90;
};
for (const i of [0, 179, 180, 359, 360, 539, 540, 719]) {
  assert(migrated.profile[i] === wantWorld(i),
    `migrated bin ${i} = ${migrated.profile[i]}, want ${wantWorld(i)}`);
}
assert(H.ceilFor(migrated, 100).ceil === 20, 'blocked direction inside the FOV');
assert(H.ceilFor(migrated, 269).ceil === 20, 'blocked direction at the FOV edge');
assert(H.ceilFor(migrated, 45).ceil === H.NO_BLOCK, 'outside the FOV stays unblocked');
const remigrated = H.normalizePanos({ panos: [migrated] }).panos[0];
assert(remigrated.profileBins === 'world', 'a current record keeps its bin marker');
for (const i of [0, 180, 360, 540, 719]) {
  assert(remigrated.profile[i] === migrated.profile[i],
    `normalizing a CURRENT record must not re-convert (bin ${i})`);
}

// A pre-0.6.16 rig stored fov_w/fov_h in DEGREES; getRigs converts once.
await WS.saveRigRec({ id: 'r-legacy', name: 'Legacy', focal_mm: 250,
                      sensor_w: 5.57, sensor_h: 3.13,
                      fov_w: WS.fovDeg(250, 5.57), fov_h: WS.fovDeg(250, 3.13) });
const legacyRig = (await WS.getRigs()).find((r) => r.id === 'r-legacy');
assert(legacyRig && legacyRig.fov_unit === 'arcmin', 'legacy rig must be marked arcmin');
assert(Math.abs(legacyRig.fov_w - WS.fovArcmin(250, 5.57)) < 1e-6,
  'legacy degree fov must convert to the same arcmin');
assert(Math.abs(legacyRig.fov_h - WS.fovArcmin(250, 3.13)) < 1e-6,
  'legacy degree fov_h must convert to the same arcmin');
await WS.saveRigRec({ id: 'r-new', name: 'New', focal_mm: 500,
                      sensor_w: 5.57, sensor_h: 3.13,
                      fov_w: WS.fovArcmin(500, 5.57), fov_h: WS.fovArcmin(500, 3.13),
                      fov_unit: 'arcmin' });
const newRig = (await WS.getRigs()).find((r) => r.id === 'r-new');
assert(newRig && Math.abs(newRig.fov_w - WS.fovArcmin(500, 5.57)) < 1e-6,
  'an arcmin rig must not be converted again');

/* 9. crop round-trip + legacy restore -> crop -> export -> re-import -------- */
// (a) a stroke record: crop, then normalize, and confirm fov/az/strokes survive
const cst = H.normalizePanos({ panos: [panoRec({ id: 'crop-strokes',
  strokes: [{ m: 'b', x: 360, y: 100, r: 30 }] })] }).panos[0];
H.cropPano(cst, { x: 180, w: 360 }, 720);          // keep az 90..270 -> fov 180, az 90
const cstAgain = H.normalizePanos({ panos: [JSON.parse(JSON.stringify(cst))] }).panos[0];
assert(cstAgain.fov === 180 && cstAgain.azOffset === 90, 'crop fov/az survive normalize');
assert(cstAgain.strokes.length === cst.strokes.length, 'cropped strokes survive normalize');

// (b) legacy profile-only record: migrate (world bins), crop, then export and
// re-import; the cropped record must come back byte-identical.
const legacyCrop = H.normalizePanos({ panos: [panoRec({
  id: 'legacy-crop', fov: 180, azOffset: 90,
  profile: new Array(720).fill(20), profileTop: blank720(),
  profileBins: undefined,
})] }).panos[0];
assert(legacyCrop.profileBins === 'world', 'legacy crop record migrated to world bins');
H.cropPano(legacyCrop, { x: 180, w: 360 }, 720);   // keep az 135..225 -> fov 90, az 135
assert(legacyCrop.fov === 90 && legacyCrop.azOffset === 135, 'legacy crop calibration');
H.applyPanosSnapshot({ panos: [JSON.parse(JSON.stringify(legacyCrop))],
                       activeId: legacyCrop.id });
const exportedCrop = H.exportPanos();
const reimported = H.normalizePanos(exportedCrop).panos.find((x) => x.id === 'legacy-crop');
assert(reimported, 'the cropped record must survive export + re-import');
assert(reimported.fov === legacyCrop.fov && reimported.azOffset === legacyCrop.azOffset,
  'crop fov/az must survive export + re-import');
assert(JSON.stringify(reimported.profile) === JSON.stringify(legacyCrop.profile),
  'crop profile must be byte-identical after export + re-import');
assert(JSON.stringify(reimported.profileTop) === JSON.stringify(legacyCrop.profileTop),
  'crop profileTop must be byte-identical after export + re-import');

/* WB7 forward compatibility: `version` is written at export and was IGNORED on
 * import, so a bundle from a LATER build was accepted and its fields read under
 * this build's rules - a silent misreading reported as a successful restore. A
 * version above BUNDLE_VERSION must now be refused, v1 (no stamp) and v2 must
 * still restore, and the stamp must never be a crash of its own. */
const good = { app: 'nightseam-web', version: 2, sites: [], rigs: [], targets: [],
               prefs: {}, panos: { panos: [], activeId: null } };
assert(WS.stageBackup(good), 'a current-format bundle must still stage');
const savedPrefs = WS.stageBackup({ ...good, theme: 'dome', prefs: {
  savedTargetIds: ['ngc224', 'u_capture', 'ngc224', 'bad/id', ' <img> '],
  skyBrightnessProvider: 'nasa', theme: 'dome', themeVariant: 'glass',
} }).prefs;
assert(JSON.stringify(savedPrefs.savedTargetIds) === JSON.stringify(['ngc224', 'u_capture']),
  'saved target IDs are deduplicated and unsafe IDs are dropped by backup validation');
assert(!('skyBrightnessProvider' in savedPrefs) && !('theme' in savedPrefs) &&
       !('themeVariant' in savedPrefs),
  'backup validation strips retired brightness and theme-switch preferences');
const future = { ...good, version: 99 };
let futureErr = null;
try { WS.stageBackup(future); } catch (e) { futureErr = e; }
assert(futureErr, 'a bundle from a NEWER format must be refused, not misread');
assert(futureErr && /newer version/i.test(futureErr.message),
  `the refusal must say the bundle is newer (got ${futureErr && futureErr.message})`);
assert(futureErr && String(futureErr.message).includes('99'),
  'the refusal must name the format it saw');
/* an older bundle (no stamp at all = v1) still restores: same field layout */
const v1 = { ...good }; delete v1.version;
assert(WS.stageBackup(v1), 'a v1 bundle (no version stamp) must still restore');
/* a garbage stamp is malformed input, not a crash */
for (const bad of ['abc', -1, 0, {}]) {
  let badErr = null;
  try { WS.stageBackup({ ...good, version: bad }); } catch (e) { badErr = e; }
  assert(badErr, `an unusable version stamp (${JSON.stringify(bad)}) must be refused`);
}
/* and the app identity rule is untouched */
let foreignErr = null;
try { WS.stageBackup({ ...good, app: 'something-else' }); } catch (e) { foreignErr = e; }
assert(foreignErr, 'a foreign bundle must still be refused');

console.log(ok ? 'web backup integrity held' : 'web backup integrity FAILED');
process.exit(ok ? 0 : 1);
