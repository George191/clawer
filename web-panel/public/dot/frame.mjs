import createOrbitModule from './runtime/orbit-characters.mjs';
import { Category, ActivityKind, ActivityCommandType, ActivityEntry, ActivityOutcome, Quality, ActivityTheme, PointerPhase } from './runtime/orbit-enums.mjs';

const canvas = document.querySelector('canvas');
const events = new AbortController();
let character, frame = 0, disposed = false, suspended = false, failed = false, ready = false, firstFrame = false;
let activity = ActivityKind.None, sequence = 0n, episode = 0n;
let active = true, reduced = false, paused = false, dark = true, requested = 'Thinking';
let elapsed = 0, previous, fit;
const send = (state, extra = {}) => parent.postMessage({ type: 'helio-dot-status', state, ...extra }, location.origin);
function fail(error) {
  if (disposed || failed) return;
  failed = true;
  cancelAnimationFrame(frame); frame = 0;
  character?.setActive(false);
  send('failed', { message: error instanceof Error ? error.message : String(error) });
}
function visible() { return active && !document.hidden && !disposed && !suspended && !failed; }
function schedule() { if (!frame && character && visible()) frame = requestAnimationFrame(render); }
function setActivity() {
  if (!character) return;
  const next = ActivityKind[requested];
  if (next === activity) return;
  const stopping = next === ActivityKind.None;
  const result = character.applyActivity({ command: stopping ? ActivityCommandType.Stop : ActivityCommandType.Start, sequence: ++sequence, episodeId: stopping ? episode : ++episode, episodeHighWater: 0n, activity: stopping ? activity : next, outcome: ActivityOutcome.Neutral, entry: ActivityEntry.Start });
  if (result !== 0) throw new Error(`Dot rejected activity ${requested}: ${result}`);
  activity = next;
  canvas.dataset.activity = requested;
}
function configure() {
  if (!character || disposed || failed) return;
  character.setActive(visible());
  character.setReducedMotion(reduced || paused);
  canvas.dataset.motion = reduced || paused ? 'still' : 'animated';
  character.setActivityTheme(dark ? ActivityTheme.Dark : ActivityTheme.Light);
  setActivity();
  previous = undefined;
  if (visible()) schedule(); else { cancelAnimationFrame(frame); frame = 0; }
}
function frameFit() {
  const side = Math.min(innerWidth, innerHeight);
  if (!fit || !side) return;
  const scale = 0.68 * 512 / fit.height;
  const x = (0.5 - fit.x / 512) * side * scale;
  const y = (0.5 - fit.y / 512) * side * scale;
  canvas.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(${scale})`;
}
function fitCharacter() {
  const rgba = character.captureNeutral(512, 512, 1);
  if (!rgba) throw new Error('Dot could not capture its native framing.');
  let left = 512, top = 512, right = 0, bottom = 0;
  for (let y = 0; y < 512; y++) for (let x = 0; x < 512; x++) {
    if (rgba[(y * 512 + x) * 4 + 3] < 8) continue;
    left = Math.min(left, x); right = Math.max(right, x + 1); top = Math.min(top, y); bottom = Math.max(bottom, y + 1);
  }
  if (right <= left || bottom <= top) throw new Error('Dot produced an empty native portrait.');
  fit = { x: (left + right) / 2, y: (top + bottom) / 2, height: bottom - top };
  frameFit();
}
function render(now) {
  frame = 0;
  if (!visible()) return;
  try {
    const dt = previous === undefined ? 0 : Math.max(0, Math.min((now - previous) / 1000, 0.1));
    previous = now;
    if (!reduced && !paused) elapsed += dt;
    character.render(elapsed);
    const error = character.renderError() || character.preparationError();
    if (error) throw new Error(error);
    const preparation = character.preparationStats();
    if (firstFrame && !ready && !preparation.pending) {
      fitCharacter(); ready = true; canvas.dataset.ready = 'true';
      send('ready', { activity: requested });
    }
    if (!ready || !reduced && !paused || preparation.pending) schedule();
  } catch (error) { fail(error); }
}

addEventListener('message', event => {
  if (event.source !== parent || event.origin !== location.origin || event.data?.type !== 'helio-dot-config') return;
  try {
    const data = event.data;
    if (!Object.hasOwn(ActivityKind, data.activity)) throw new Error('Unknown Dot activity.');
    requested = data.activity;
    active = data.active === true; reduced = data.reduced === true; paused = data.paused === true; dark = data.dark === true;
    configure();
  } catch (error) { fail(error); }
}, { signal: events.signal });
document.addEventListener('visibilitychange', configure, { signal: events.signal });
addEventListener('resize', frameFit, { signal: events.signal });
canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); fail(new Error('Dot WebGL context lost.')); }, { signal: events.signal });
function pointer(event, phase) {
  if (!character || !ready || !visible()) return;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  character.pointer(phase, event.pointerId, x, y, elapsed);
  if (phase === PointerPhase.Move && activity === ActivityKind.Ready) character.setReadyGaze(x, y, elapsed);
  schedule();
}
canvas.addEventListener('pointerdown', event => { canvas.setPointerCapture(event.pointerId); pointer(event, PointerPhase.Down); }, { signal: events.signal });
canvas.addEventListener('pointermove', event => pointer(event, PointerPhase.Move), { signal: events.signal });
canvas.addEventListener('pointerup', event => { pointer(event, PointerPhase.Up); if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId); }, { signal: events.signal });
canvas.addEventListener('pointercancel', event => pointer(event, PointerPhase.Cancel), { signal: events.signal });
canvas.addEventListener('pointerleave', () => { if (character && activity === ActivityKind.Ready) { character.clearReadyGaze(elapsed); schedule(); } }, { signal: events.signal });
addEventListener('pagehide', event => {
  if (event.persisted) {
    suspended = true; previous = undefined; cancelAnimationFrame(frame); frame = 0;
    character?.setActive(false);
    return;
  }
  disposed = true; events.abort(); cancelAnimationFrame(frame);
  if (character && !character.isDeleted()) character.delete();
}, { signal: events.signal });
addEventListener('pageshow', event => {
  if (event.persisted) { suspended = false; configure(); }
}, { signal: events.signal });
send('loaded');
try {
  const response = await fetch('./appearance.json');
  if (!response.ok) throw new Error(`Dot appearance failed to load: ${response.status}`);
  const appearance = await response.json();
  const engine = await createOrbitModule({ locateFile: file => new URL('./runtime/' + file, import.meta.url).href });
  if (!disposed) {
    character = new engine.Character('#character', 512, 512, { activities: true });
    character.setQuality(Quality.Compact);
    character.setDisplayScale(1);
    const restore = character.restore(engine.presetAppearance(appearance.preset));
    if (restore) throw new Error(restore);
    for (const item of engine.catalog(Category.Accessory)) if (character.isSelected(Category.Accessory, item.id) && !character.select(Category.Accessory, item.id)) throw new Error(`Dot could not clear accessory ${item.id}`);
    for (const [name, id] of Object.entries(appearance.selections)) {
      if (!Object.hasOwn(Category, name) || !character.isSelected(Category[name], id) && !character.select(Category[name], id)) throw new Error(`Dot rejected appearance ${name}/${id}`);
    }
    for (const id of appearance.accessories) if (!character.select(Category.Accessory, id)) throw new Error(`Dot rejected accessory ${id}`);
    const bound = character.applyActivity({ command: ActivityCommandType.Restore, sequence: 0n, episodeId: 0n, episodeHighWater: 0n, activity: ActivityKind.None, outcome: ActivityOutcome.Neutral, entry: ActivityEntry.Sustained });
    if (bound !== 0) throw new Error(`Dot activity binding failed: ${bound}`);
    character.onFirstFrame(() => { firstFrame = true; });
    configure();
  }
} catch (error) { fail(error); }
