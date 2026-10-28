import * as THREE from 'three';

export function createCinematicShield() {
  const group = new THREE.Group();
  group.name = 'Particle reception shield';
  group.visible = false;
  let disposed = false;
  const time = { value: 0 };
  const strength = { value: 0 };
  const viewportHeight = { value: 1 };
  const geometry = new THREE.BufferGeometry();
  const positions = new Float32Array(1400 * 3);
  const phases = new Float32Array(1400);
  const kinds = new Float32Array(1400);
  for (let i = 0; i < 1400; i++) {
    const y = 1 - 2 * (i + .5) / 1400;
    const angle = i * Math.PI * (3 - Math.sqrt(5));
    const radius = Math.sqrt(1 - y * y);
    positions.set([Math.cos(angle) * radius, y, Math.sin(angle) * radius], i * 3);
    phases[i] = (i * .61803398875) % 1;
    kinds[i] = i % 4 !== 0 ? 0 : i % 20 === 0 ? 2 : 1;
  }
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('aPhase', new THREE.BufferAttribute(phases, 1));
  geometry.setAttribute('aKind', new THREE.BufferAttribute(kinds, 1));
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1.05);
  const material = new THREE.ShaderMaterial({
    uniforms: { uTime: time, uStrength: strength, uViewportHeight: viewportHeight },
    vertexShader: `
      uniform float uTime;
      uniform float uStrength;
      uniform float uViewportHeight;
      attribute float aPhase;
      attribute float aKind;
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        float life = fract(uTime * 0.26 + aPhase);
        float radial = 0.995 + sin(uTime * 0.8 + aPhase * 6.283185) * 0.008;
        float visibility = 0.65 + 0.35 * pow(sin(uTime * 1.8 + aPhase * 6.283185), 2.0);
        vColor = vec3(0.025, 0.16, 1.4);
        if (aKind > 1.5) {
          radial = mix(1.02, 0.3, life);
          visibility = pow(sin(life * 3.141593), 2.0);
          vColor = vec3(0.035, 0.25, 1.5);
        } else if (aKind > 0.5) {
          radial = mix(0.2, 1.04, 1.0 - pow(1.0 - life, 3.0));
          visibility = pow(sin(life * 3.141593), 2.0);
        }
        vec4 point = modelViewMatrix * vec4(position * radial, 1.0);
        float scale = length(modelMatrix[0].xyz);
        float size = (0.013 + aPhase * 0.009) * scale;
        float pixels = size * uViewportHeight * projectionMatrix[1][1] / (2.0 * max(0.01, -point.z));
        gl_PointSize = max(1.0, pixels);
        vAlpha = uStrength * visibility * min(1.0, pixels * pixels) * 0.95;
        gl_Position = projectionMatrix * point;
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        vec2 point = gl_PointCoord * 2.0 - 1.0;
        float radius = dot(point, point);
        float core = exp(-22.0 * radius);
        float softness = min(1.0, exp(-3.5 * radius) * 0.7 + core * 0.8) * (1.0 - smoothstep(0.7, 1.0, radius));
        gl_FragColor = vec4(vColor + vec3(0.025, 0.12, 0.5) * core, softness * vAlpha);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
    transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
  });
  const particles = new THREE.Points(geometry, material);
  const drawingSize = new THREE.Vector2();
  particles.onBeforeRender = renderer => {
    renderer.getDrawingBufferSize(drawingSize);
    viewportHeight.value = drawingSize.y;
  };
  const shellGeometry = new THREE.SphereGeometry(1, 48, 32);
  shellGeometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1.05);
  const shellMaterial = new THREE.ShaderMaterial({
    uniforms: { uTime: time, uStrength: strength },
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vView;
      varying vec2 vUv;
      void main() {
        vec4 point = modelViewMatrix * vec4(position, 1.0);
        vNormal = normalize(normalMatrix * normal);
        vView = normalize(-point.xyz);
        vUv = uv;
        gl_Position = projectionMatrix * point;
      }
    `,
    fragmentShader: `
      uniform float uStrength;
      uniform float uTime;
      varying vec3 vNormal;
      varying vec3 vView;
      varying vec2 vUv;
      void main() {
        float rim = pow(max(0.0, 1.0 - abs(dot(normalize(vNormal), normalize(vView)))), 2.25);
        vec2 grid = vUv * vec2(32.0, 16.0);
        vec2 edge = abs(fract(grid - 0.5) - 0.5) / max(fwidth(grid), vec2(0.0001));
        float lattice = 1.0 - min(1.0, min(edge.x, edge.y));
        float arc = pow(max(0.0, sin(vUv.x * 18.0 + vUv.y * 11.0 - uTime * 1.6)), 18.0);
        float wave = smoothstep(0.75, 1.0, sin(vUv.y * 18.0 + uTime * 1.3));
        float alpha = uStrength * (rim * (0.34 + wave * 0.1) + lattice * (0.018 + arc * 0.085));
        gl_FragColor = vec4(0.025, 0.17, 1.35, alpha);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
    transparent: true, depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
  });
  group.add(particles, new THREE.Mesh(shellGeometry, shellMaterial));
  return {
    group,
    update(seconds: number, reception: number) {
      if (disposed) return;
      strength.value = THREE.MathUtils.clamp(reception, 0, 1);
      time.value = seconds;
      group.visible = strength.value > 0;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      group.visible = false;
      geometry.dispose();
      material.dispose();
      shellGeometry.dispose();
      shellMaterial.dispose();
      group.clear();
    },
  };
}
