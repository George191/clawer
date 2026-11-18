# Native Dot

The component hosts the installed Dot engine in a same-origin frame. The native
engine owns character rendering, activity transitions, props, materials,
lighting, pointer behavior and idle behavior. No Three.js reimplementation is
used by this component.

The screenshot's native appearance is packaged as the default:
Hexagon (`rounded_cube`), Teal, Dots, Separate trapezoid lenses, and Headphones.
No custom materials, props, lights or animations are configured.

```tsx
import NativeDot, { type DotActivity, type NativeDotProps } from '@/components/NativeDot';

// Uses native idle/rest behavior. The container controls layout size.
<div style={{ width: 96, height: 96 }}><NativeDot /></div>

// Pass only the current work state when connecting a business page.
<div style={{ width: 120, height: 120 }}><NativeDot activity="Working" /></div>
```

The collection page keeps one `ScoutDot` instance above the router. It starts preparation
on the login screen, retains the iframe across route/activity changes, and forwards
page pointer coordinates to the native pointer/gaze APIs. `preload` prepares the first
frame offscreen, then suspends offscreen rendering.

The bundled engine has no separate Sleep activity; `None` is native idle, not a
guaranteed sleeping/ZZZ animation.

All props are optional: `activity` defaults to `None`, `paused` to `false`, and
`dark` to `true`. `DotActivity`, `NativeDotProps`, and `DOT_ACTIVITIES` are public
exports. The component handles startup, visibility and reduced motion internally.

Edit only `public/dot/appearance.json` to change the shape, color, eyes, eyewear
and permanent accessories through the native catalog. Activity props are not
permanent accessories and are not modified by that file.

For another page in this project, import the component; no environment variables,
extra service or per-page appearance configuration is needed. The static
`public/dot` directory is copied by Vite into the application build. A standalone
consumer must carry this directory at `/dot/` with the component.

The five core files under `public/dot/runtime` are byte-identical to the verified
local runtime; `public/dot/runtime-sha256.json` records their hashes and version.
`frame.mjs` implements lifecycle, activity commands, visibility, reduced motion
and parent-frame messaging. It does not draw or replace any native prop.

The standalone `/helio.html` preview exposes all ten original activity kinds.
`None` delegates idle and rest behavior to the engine; no separate fake sleep
animation is added.
