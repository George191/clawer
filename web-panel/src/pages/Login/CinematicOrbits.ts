import * as THREE from 'three';

// Equal radii and speeds; distinct planes include a counter-travelling pass.
export const SMALL_ORBIT_LANES = [
  { radiusFactor: 1.10, inclination: -.20, tilt: 1.10, phase: 5.884, angularSpeed: .0120, scale: .075 },
  { radiusFactor: 1.10, inclination: -.60, tilt: .25, phase: 1.553, angularSpeed: .0120, scale: .050 },
  { radiusFactor: 1.10, inclination: 2.25, tilt: 2.85, phase: 4.531, angularSpeed: .0120, scale: .048 },
];

/** Tile each orbit's visible arc over one common period, with overlap at handoffs. */
export function planSmallOrbitPhases(
  orbits: { u: THREE.Vector3; v: THREE.Vector3; radius: number }[],
  camera: THREE.PerspectiveCamera,
  earthCenter: THREE.Vector3,
  earthRadius: number,
  width: number,
  height: number,
  openingPhase: number,
) {
  const tau = Math.PI * 2;
  const samples = 1440;
  const step = tau / samples;
  const offset = camera.position.clone().sub(earthCenter);
  const point = new THREE.Vector3();
  const direction = new THREE.Vector3();
  const screen = new THREE.Vector3();
  const windows = orbits.map(orbit => {
    const visible = Array.from({ length: samples }, (_, index) => {
      const phase = index * step;
      point.copy(earthCenter).addScaledVector(orbit.u, orbit.radius * Math.cos(phase))
        .addScaledVector(orbit.v, orbit.radius * Math.sin(phase));
      direction.subVectors(point, camera.position);
      const distance = direction.length();
      direction.divideScalar(distance);
      const b = offset.dot(direction);
      const disc = b * b - offset.lengthSq() + (earthRadius * 1.006) ** 2;
      if (disc >= 0 && -b - Math.sqrt(disc) > 0 && -b - Math.sqrt(disc) < distance) return false;
      screen.copy(point).project(camera);
      const x = (screen.x + 1) * width / 2, y = (1 - screen.y) * height / 2;
      return screen.z > -1 && screen.z < 1 && x >= 20 && x <= width - 20 && y >= 20 && y <= height - 20;
    });
    let run = 0, length = 0, start = 0;
    for (let index = 0; index < samples * 2; index++) {
      run = visible[index % samples] ? Math.min(run + 1, samples) : 0;
      if (run > length) { length = run; start = index - run + 1; }
    }
    // Keep the sampled boundaries inside the continuous visible arc.
    return { start: (start % samples + 2) * step, length: (length - 4) * step };
  });
  const overlap = (windows.reduce((sum, window) => sum + window.length, 0) - tau) / windows.length;
  const first = windows[0];
  const opening = first.start + THREE.MathUtils.clamp(
    (openingPhase - first.start + tau) % tau, 0, first.length,
  );
  let arrival = first.start - opening;
  return windows.map(window => {
    const phase = window.start - arrival;
    arrival += window.length - overlap;
    return phase;
  });
}
