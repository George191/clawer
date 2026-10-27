import * as THREE from 'three';

const Y_AXIS = new THREE.Vector3(0, 1, 0);
const X_AXIS = new THREE.Vector3(1, 0, 0);
const MAX_RINGS = 256;
const POINTS_PER_RING = 64;
const coreVertex = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const coreFragment = `
  uniform vec3 color;
  uniform float time, strength, beamLength;
  varying vec2 vUv;
  void main() {
    float distance = vUv.y * beamLength;
    float band = abs(fract((distance + time * 1.2) / 0.65 + 0.5) - 0.5) * 0.65;
    float pixel = max(fwidth(distance), 0.001);
    float flow = (1.0 - smoothstep(0.055 - pixel * 0.5, 0.055 + pixel * 0.5, band))
      * (1.0 - smoothstep(0.22, 0.55, pixel));
    float fade = smoothstep(0.0, 0.045, vUv.y) * (1.0 - smoothstep(0.86, 1.0, vUv.y));
    gl_FragColor = vec4(color * 1.6, min((0.22 + flow * 0.5) * fade * strength, 0.85));
    #include <colorspace_fragment>
  }
`;
const particleVertex = `
  uniform float time, strength, beamLength, beamRadius, viewportHeight;
  attribute float aRing, aAngle, aSeed;
  varying float vAlpha;
  void main() {
    float distance = (aRing + fract(time * 0.6 / 0.65)) * 0.65;
    float u = distance / beamLength;
    vAlpha = 0.0;
    if (u <= 0.0 || u >= 1.0) {
      gl_PointSize = 1.0;
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      return;
    }
    // The pattern depends on physical distance, never the recycled ring slot.
    float angle = aAngle + distance * 0.12 + sin(time * 0.5 + aSeed * 6.2831853) * 0.025;
    float radial = beamRadius * pow(u, 1.6) * (1.0 + sin(time * 0.8 + aSeed * 6.2831853) * 0.004);
    vec4 point = modelViewMatrix * vec4(cos(angle) * radial, distance, sin(angle) * radial, 1.0);
    float fade = smoothstep(0.0, 0.045, u) * (1.0 - smoothstep(0.86, 1.0, u));
    float size = (0.02 + aSeed * 0.012) * mix(1.0, 1.9, smoothstep(0.08, 0.85, u)) * length(modelMatrix[0].xyz);
    float pixels = size * viewportHeight * projectionMatrix[1][1] / (2.0 * max(0.01, -point.z));
    gl_PointSize = max(1.0, pixels);
    float flicker = 0.7 + 0.3 * pow(sin(time * 1.8 + aSeed * 6.2831853), 2.0);
    vAlpha = min(1.0, strength * fade * flicker * min(1.0, pixels * pixels) * 1.4);
    gl_Position = projectionMatrix * point;
  }
`;
const particleFragment = `
  uniform vec3 color;
  varying float vAlpha;
  void main() {
    vec2 point = gl_PointCoord * 2.0 - 1.0;
    float radius = dot(point, point);
    float core = exp(-22.0 * radius);
    float glow = exp(-3.5 * radius) * 0.7;
    float softness = min(1.0, glow + core * 0.8) * (1.0 - smoothstep(0.7, 1.0, radius));
    gl_FragColor = vec4(color * vec3(1.0, 1.0, 1.4) + vec3(0.025, 0.12, 0.5) * core, softness * vAlpha);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;
const ringFragment = `
  uniform vec3 color;
  uniform float time, strength;
  varying vec2 vUv;
  void main() {
    vec2 position = vUv * 2.0 - 1.0;
    float r = length(position);
    float angle = atan(position.y, position.x + 0.0000001) / 6.2831853 + 0.5;
    float rings = 0.0, cores = 0.0;
    for (int i = 0; i < 4; i++) {
      float phase = fract(time * 0.32 + float(i) / 4.0);
      float envelope = smoothstep(0.0, 0.08, phase) * (1.0 - smoothstep(0.72, 1.0, phase));
      float ringRadius = 0.04 + phase * 0.92;
      float dr = r - ringRadius;
      float arc = (fract(angle * 96.0 + float(i) * 0.37) - 0.5) * 6.2831853 * ringRadius / 96.0;
      float rho = dot(vec2(dr, arc), vec2(dr, arc)) / 0.000324;
      float core = exp(-22.0 * rho);
      float softness = min(1.0, exp(-3.5 * rho) * 0.7 + core * 0.8) * (1.0 - smoothstep(0.7, 1.0, rho));
      float unresolved = smoothstep(0.2, 0.75, fwidth(angle * 96.0));
      float outline = exp(-pow(dr * 52.0, 2.0)) * mix(0.035, 0.18, unresolved);
      rings += (softness * (1.0 - unresolved) + outline) * envelope;
      cores += core * envelope;
    }
    float contact = exp(-r * r * 85.0) * (0.22 + 0.04 * sin(time * 3.0));
    float edge = 1.0 - smoothstep(0.92, 1.0, r);
    gl_FragColor = vec4(color * vec3(1.0, 1.0, 1.4) + vec3(0.025, 0.12, 0.5) * min(cores, 1.0), min((rings * 1.05 + contact) * edge * strength, 0.85));
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export function createCinematicScan(color: number): {
  group: THREE.Group;
  update(start: THREE.Vector3, end: THREE.Vector3, time: number, strength: number, radius?: number,
    surface?: { center: THREE.Vector3; radius: number }, volumeVisibility?: number): void;
  dispose(): void;
} {
  const group = new THREE.Group();
  group.visible = false;
  const tint = new THREE.Color(color);
  const direction = new THREE.Vector3();
  const normal = new THREE.Vector3(), tangent = new THREE.Vector3(), bitangent = new THREE.Vector3();
  const inverse = new THREE.Quaternion(), basis = new THREE.Matrix4();
  // Local +Y points from the satellite toward the Earth footprint.
  const coreGeometry = new THREE.CylinderGeometry(1, 0, 1, 24, 1, true);
  coreGeometry.translate(0, 0.5, 0);
  const particleGeometry = new THREE.BufferGeometry();
  const count = MAX_RINGS * POINTS_PER_RING;
  const rings = new Float32Array(count), angles = new Float32Array(count), seeds = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const point = i % POINTS_PER_RING;
    rings[i] = Math.floor(i / POINTS_PER_RING);
    angles[i] = point / POINTS_PER_RING * Math.PI * 2;
    seeds[i] = point * 0.61803398875 % 1;
  }
  particleGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  particleGeometry.setAttribute('aRing', new THREE.BufferAttribute(rings, 1));
  particleGeometry.setAttribute('aAngle', new THREE.BufferAttribute(angles, 1));
  particleGeometry.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1));
  const particleBounds = new THREE.Sphere();
  particleGeometry.boundingSphere = particleBounds;
  const ringGeometry = new THREE.PlaneGeometry(2, 2, 24, 24);
  const ringPositions = ringGeometry.getAttribute('position') as THREE.BufferAttribute;
  const ringUV = ringGeometry.getAttribute('uv');
  ringPositions.setUsage(THREE.DynamicDrawUsage);
  const ringBounds = new THREE.Sphere();
  ringGeometry.boundingSphere = ringBounds;
  const coreMaterial = new THREE.ShaderMaterial({
    uniforms: { color: { value: new THREE.Color(0x83beff).lerp(new THREE.Color(0xe7faff), 0.55) }, time: { value: 0 }, strength: { value: 0 }, beamLength: { value: 1 } },
    vertexShader: coreVertex, fragmentShader: coreFragment,
    transparent: true, depthTest: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass: true,
    blending: THREE.AdditiveBlending, toneMapped: false,
  });
  const particleMaterial = new THREE.ShaderMaterial({
    uniforms: { color: { value: tint }, time: { value: 0 }, strength: { value: 0 }, beamLength: { value: 1 }, beamRadius: { value: 0.5 }, viewportHeight: { value: 1 } },
    vertexShader: particleVertex, fragmentShader: particleFragment,
    transparent: true, depthTest: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const ringMaterial = new THREE.ShaderMaterial({
    uniforms: { color: { value: tint }, time: { value: 0 }, strength: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: ringFragment,
    transparent: true, depthTest: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass: true,
    blending: THREE.AdditiveBlending,
  });
  const particles = new THREE.Points(particleGeometry, particleMaterial);
  const drawingSize = new THREE.Vector2();
  particles.onBeforeRender = renderer => {
    renderer.getDrawingBufferSize(drawingSize);
    particleMaterial.uniforms.viewportHeight.value = drawingSize.y;
  };
  const core = new THREE.Mesh(coreGeometry, coreMaterial);
  const ring = new THREE.Mesh(ringGeometry, ringMaterial);
  ring.rotation.x = Math.PI / 2;
  group.add(particles, core, ring);
  const materials = [particleMaterial, coreMaterial, ringMaterial];
  let disposed = false;

  return {
    group,
    update(start, end, time, strength, radius = 0.5, surface, _volumeVisibility = 1) {
      if (disposed) return;
      direction.subVectors(end, start);
      const length = direction.length();
      group.visible = strength > 0 && length > 0.0001;
      if (!group.visible) return;
      group.position.copy(start);
      group.quaternion.setFromUnitVectors(Y_AXIS, direction.divideScalar(length));
      core.scale.set(radius * 0.04, length, radius * 0.04);
      particleMaterial.uniforms.beamLength.value = coreMaterial.uniforms.beamLength.value = length;
      particleMaterial.uniforms.beamRadius.value = radius;
      particleGeometry.setDrawRange(0, Math.min(MAX_RINGS, Math.ceil(length / 0.65) + 1) * POINTS_PER_RING);
      particleBounds.center.set(0, length * 0.5, 0);
      particleBounds.radius = Math.hypot(length * 0.5, radius * 1.004) + 0.04;
      const span = radius * 1.12;
      if (surface) {
        normal.subVectors(end, surface.center).normalize();
        tangent.crossVectors(Math.abs(normal.y) > 0.95 ? X_AXIS : Y_AXIS, normal).normalize();
        bitangent.crossVectors(normal, tangent);
        inverse.copy(group.quaternion).invert();
        ring.quaternion.setFromRotationMatrix(basis.makeBasis(tangent, bitangent, normal)).premultiply(inverse);
        ring.position.copy(normal).multiplyScalar(surface.radius).add(surface.center).sub(start).applyQuaternion(inverse);
      } else {
        ring.position.set(0, length - Math.min(0.025, length * 0.01), 0);
        ring.quaternion.setFromAxisAngle(X_AXIS, Math.PI / 2);
      }
      for (let i = 0; i < ringPositions.count; i++) {
        const x = (ringUV.getX(i) * 2 - 1) * span, y = (ringUV.getY(i) * 2 - 1) * span;
        const project = surface ? surface.radius / Math.hypot(x, y, surface.radius) : 1;
        ringPositions.setXYZ(i, x * project, y * project, surface ? surface.radius * (project - 1) : 0);
      }
      ringPositions.needsUpdate = true;
      ringBounds.radius = Math.SQRT2 * span;
      for (const material of materials) {
        material.uniforms.time.value = time;
        material.uniforms.strength.value = strength;
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      group.visible = false;
      group.clear();
      particleGeometry.dispose();
      coreGeometry.dispose();
      ringGeometry.dispose();
      for (const material of materials) material.dispose();
    },
  };
}
