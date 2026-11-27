import { MODE_DRAWS, resolvePreset } from 'thinking-orbs/engine';

// Paint the same orb before the application and its lazy routes have mounted.
const stage = document.querySelector<HTMLElement>('.sp-auth-boot')!;
const canvas = stage.querySelector('canvas')!;
const context = canvas.getContext('2d')!;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const preset = resolvePreset('working', 64);
const ratio = Math.min(devicePixelRatio || 1, 2);
canvas.width = canvas.height = 64 * ratio;
context.scale(ratio, ratio);
let frame = 0;

function draw(time: number) {
  const style = getComputedStyle(stage);
  if (document.hidden || style.visibility === 'hidden' || style.display === 'none') { frame = 0; return; }
  context.clearRect(0, 0, 64, 64);
  MODE_DRAWS[preset.mode](context, 64, reduced.matches ? 1 : time / 1000 * preset.speed, true, preset.opts);
  frame = reduced.matches ? 0 : requestAnimationFrame(draw);
}

function sync() {
  cancelAnimationFrame(frame);
  frame = 0;
  draw(performance.now());
}

new MutationObserver(sync).observe(document.getElementById('root')!, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
document.addEventListener('visibilitychange', sync);
reduced.addEventListener('change', sync);
sync();
