import {
  PLANNER_CHANGE_EVENT,
  nextPlannerRevision,
  plannerChange,
  panoramaRevision,
} from '../../core/plannerstate.js';

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

assert(PLANNER_CHANGE_EVENT === 'planner:changed', 'event name is stable');
assert(nextPlannerRevision(0) === 1, 'revision starts at one');
assert(nextPlannerRevision(12) === 13, 'revision increments');
assert(nextPlannerRevision(-1) === 1, 'negative revisions reset');
assert(nextPlannerRevision(Number.MAX_SAFE_INTEGER) === 1,
  'overflowing revisions reset');

const event = plannerChange('panorama', 4, { cause: 'undo', panoId: 'p1' });
assert(event.kind === 'panorama' && event.revision === 4 && event.cause === 'undo',
  'change payload keeps the shared fields');

const base = {
  id: 'p1', paint_mode: 'obstacle', block_above: false,
  az_offset: 0, alt_bottom: 0, alt_top: 45, fov: 360,
  profile: [-90, 10], profile_top: [],
  strokes: [{ m: 'b', x: 0.2, y: 0.4, r: 0.1 }],
};
const changed = { ...base, strokes: [{ ...base.strokes[0], x: 0.3 }] };
assert(panoramaRevision(base) !== panoramaRevision(changed),
  'a stroke edit changes the content revision');
const strokeAlias = { ...base, strokes: base.strokes.map((s) => ({ ...s })) };
assert(panoramaRevision(base) === panoramaRevision(strokeAlias),
  'equal panorama content has a stable revision');
assert(panoramaRevision(null) === '-', 'missing panorama has a sentinel revision');

console.log('planner state contract held');
