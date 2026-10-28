import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { createCinematicEarth } from './CinematicEarth';
import { createCinematicScan } from './CinematicScan';
import { createCinematicShield } from './CinematicShield';
import { createCinematicSatellite, disposeCinematicSatellite, updateCinematicSatellite, prewarmCinematicSatelliteTextures, solarTexture, weatheredMetalTextures, foilTexture } from './CinematicSatellite';

const smooth = THREE.MathUtils.smoothstep;

export default function CinematicSpace({ onUnavailable, onReady }: { onUnavailable: () => void; onReady?: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const veilRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const veil = veilRef.current;
    const veilContext = veil?.getContext('2d');
    if (!canvas || !veil || !veilContext) { onUnavailable(); return; }
    const veilCtx = veilContext;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
    } catch { onUnavailable(); return; }

    let disposed = false;
    let prepared = false;
    let ready = false;
    const yieldTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));
    let width = 1;
    let height = 1;
    let elapsed = 0;
    let lastTime = performance.now();
    let lastPaint = 0;
    const flybyPeriod = 72;
    let flybyStartMean = 0;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, .1, 1600);
    camera.position.set(0, 0, 18);
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.shadowMap.autoUpdate = false;

    const sunDirection = new THREE.Vector3(-.8, .5, -.32).normalize();
    const sun = new THREE.DirectionalLight(0xe0edff, 3.3);
    sun.position.copy(sunDirection).multiplyScalar(45);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    Object.assign(sun.shadow.camera, { left: -30, right: 30, top: 30, bottom: -30, near: 1, far: 130 });
    sun.shadow.bias = -.0002;
    sun.layers.enableAll();
    scene.add(sun, sun.target);
    const fill = new THREE.HemisphereLight(0x729dc1, 0x05070a, .45);
    fill.layers.enableAll();
    scene.add(fill);
    const rim = new THREE.DirectionalLight(0x4b8cc8, 1.1);
    rim.position.set(16, -5, -12);
    rim.layers.enableAll();
    scene.add(rim);

    let environment: THREE.WebGLRenderTarget | undefined;
    scene.environmentIntensity = .35;

    function sceneTime() { return motion.matches && elapsed === 0 ? flybyPeriod * .05 : elapsed; }
    let uploads = Promise.resolve();
    function uploadTexture(texture?: THREE.Texture) {
      const pending = uploads.then(async () => {
        await yieldTask();
        if (!disposed && texture) renderer.initTexture(texture);
      });
      uploads = pending.catch(() => {});
      return pending;
    }
    const textureBudget = canvas.clientWidth * Math.min(window.devicePixelRatio || 1, 2) >= 1920 ? 8192 : 4096;
    const earth = createCinematicEarth(sunDirection, uploadTexture, {
      maxTextureSize: Math.min(renderer.capabilities.maxTextureSize, textureBudget),
      maxAnisotropy: renderer.capabilities.getMaxAnisotropy(),
    });
    scene.add(earth.group);
    // The glass pass needs the same Earth depth as the main scene.
    const veilOccluder = new THREE.Mesh(new THREE.SphereGeometry(1.006, 128, 64), new THREE.MeshBasicMaterial({ colorWrite: false }));
    veilOccluder.layers.set(1);
    veilOccluder.renderOrder = -1;
    earth.group.add(veilOccluder);
    let earthRadius = 10;
    const scanSurface = { center: earth.group.position, radius: 1 };

    const starGeometry = new THREE.BufferGeometry();
    const starPositions = new Float32Array(1800 * 3);
    let seed = 7261;
    const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < 1800; i++) {
      const z = random() * 2 - 1;
      const angle = random() * Math.PI * 2;
      const radius = 350 + random() * 300;
      const span = Math.sqrt(1 - z * z);
      starPositions.set([Math.cos(angle) * span * radius, Math.sin(angle) * span * radius, z * radius], i * 3);
    }
    starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
    const starMaterial = new THREE.PointsMaterial({ color: 0xb7c9df, size: .25, transparent: true, opacity: .65, sizeAttenuation: true, depthWrite: false });
    const stars = new THREE.Points(starGeometry, starMaterial);
    scene.add(stars);

    // Distinct orbital planes and signed speeds keep the constellation dispersed.
    const orbitLanes = [
      { radiusFactor: 1.10, inclination: -.20, tilt: 1.10, phase: 5.884, angularSpeed: .0120, scale: .075 },
      { radiusFactor: 1.16, inclination: -1.30, tilt: .30, phase: 1.553, angularSpeed: -.0120, scale: .050 },
      { radiusFactor: 1.22, inclination: -1.40, tilt: .34, phase: 4.531, angularSpeed: .0120, scale: .048 },
      { radiusFactor: 1.28, inclination: 1.10, tilt: 1.16, phase: 3.095, angularSpeed: -.0105, scale: .046 },
      { radiusFactor: 1.34, inclination: .50, tilt: .72, phase: 3.038, angularSpeed: .0135, scale: .044 },
      { radiusFactor: 1.40, inclination: -.95, tilt: .62, phase: 3.876, angularSpeed: .0092, scale: .044 },
      { radiusFactor: 1.46, inclination: .05, tilt: 1.08, phase: .642, angularSpeed: -.0148, scale: .042 },
    ];
    const models: THREE.Group[] = [];
    const appearances = Array.from({ length: orbitLanes.length + 1 }, () => ({
      revealAt: Infinity, alpha: 0, wanted: false,
      materials: [] as { material: THREE.Material; opacity: number }[],
    }));
    let openingTime = 0;
    let visibilityTime = 0;

    let flybyScan: ReturnType<typeof createCinematicScan>;
    const scans: ReturnType<typeof createCinematicScan>[] = [];
    const flyby = new THREE.Group();
    const flybyNearScale = .34;
    let flybyNearDistance = 4.2;
    let fallback: THREE.Group;
    const satelliteSize = new THREE.Vector3();
    const flybySize = satelliteSize.clone();
    flyby.scale.setScalar(flybyNearScale);
    const particleShields: ReturnType<typeof createCinematicShield>[] = [];
    scene.add(flyby);
    let flybyModel: THREE.Group | undefined;
    let isNasaModel = false;
    function prepareFlybyModel() {
      flybyModel?.traverse(object => {
        object.layers.enable(1);
        if (!(object instanceof THREE.Mesh)) return;
        object.castShadow = object.receiveShadow = true;
      });
    }
    const draco = new DRACOLoader();
    draco.setDecoderPath('/space/draco/').setWorkerLimit(1);
    const loader = new GLTFLoader().setDRACOLoader(draco);
    const loadFlyby = () => new Promise<void>(resolve => loader.load('/space/iss.glb', async gltf => {
      if (disposed) { disposeCinematicSatellite(gltf.scene); draco.dispose(); resolve(); return; }
      try {
        // These seven detached modelling offcuts sit above the exported station.
        const offcuts = new THREE.Group();
        for (const name of ['bendedtru1', 'bendedtru2', 'bendedtru3', 'bendedtrus', 'pCylinder1', 'pCylinder2', 'pCylinder8']) {
          const object = gltf.scene.getObjectByName(name);
          if (object) offcuts.add(object);
        }
        disposeCinematicSatellite(offcuts);
        const cells = solarTexture();
        cells.wrapS = cells.wrapT = THREE.RepeatWrapping;
        cells.repeat.set(7, 5);
        const metal = weatheredMetalTextures();
        const foil = foilTexture();
        const meshes: THREE.Mesh[] = [];
        gltf.scene.traverse(object => { if (object instanceof THREE.Mesh) meshes.push(object); });
        for (const object of meshes) {
          await yieldTask();
          if (disposed) { disposeCinematicSatellite(gltf.scene); draco.dispose(); return; }
          const materials = Array.isArray(object.material) ? object.material : [object.material];
          const positions = object.geometry.getAttribute('position');
          const solar = materials.some(material => material.name.startsWith('lambert7SG'));
          if (!object.geometry.hasAttribute('normal')) object.geometry.computeVertexNormals();
          const normals = object.geometry.getAttribute('normal');
          const uv = new Float32Array(positions.count * 2);
          for (let i = 0; i < positions.count; i++) {
            const nx = Math.abs(normals.getX(i));
            const ny = Math.abs(normals.getY(i));
            const nz = Math.abs(normals.getZ(i));
            const axis = nx >= ny && nx >= nz ? 'x' : ny >= nz ? 'y' : 'z';
            // The NASA export has no UVs; use its local surface axes for metal detail.
            uv[i * 2] = solar ? (positions.getZ(i) + 22.87) / 45.55 : (axis === 'x' ? positions.getZ(i) : positions.getX(i)) / 3;
            uv[i * 2 + 1] = solar ? (positions.getY(i) + 4.17) / 29.94 : (axis === 'y' ? positions.getZ(i) : positions.getY(i)) / 3;
          }
          object.geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
          for (const material of materials) {
            if (!(material instanceof THREE.MeshStandardMaterial)) continue;
            const blanket = material.name.startsWith('anisotropic');
            const radiator = material.name.startsWith('lambert3SG') || material.name.startsWith('lambert4SG');
            material.map = metal.map;
            material.roughnessMap = metal.roughnessMap;
            material.bumpMap = blanket ? foil : metal.bumpMap;
            material.bumpScale = blanket ? .16 : .055;
            material.metalness = radiator ? .28 : .82;
            material.roughness = blanket ? .86 : radiator ? .94 : .8;
            material.color.set(blanket ? 0xa58b55 : radiator ? 0xc1c5c3 : 0xa5afb4);
            if (material.name.startsWith('lambert7SG')) {
              material.map = cells;
              material.bumpMap = material.roughnessMap = null;
              material.color.set(0xa6a7aa);
              material.metalness = .25;
              material.roughness = .46;
            }
            material.needsUpdate = true;
          }
        }
        const bounds = new THREE.Box3().setFromObject(gltf.scene);
        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3());
        flybySize.copy(size).multiplyScalar(8 / Math.max(size.x, size.y, size.z));
        const model = new THREE.Group();
        gltf.scene.position.sub(center);
        model.add(gltf.scene);
        model.scale.setScalar(8 / Math.max(size.x, size.y, size.z));
        flyby.remove(fallback);
        disposeCinematicSatellite(fallback);
        flybyModel = model;
        flyby.add(model);
        isNasaModel = true;
        prepareFlybyModel();
      } catch {
        disposeCinematicSatellite(gltf.scene);
      } finally { resolve(); }
    }, undefined, () => { if (disposed) draco.dispose(); resolve(); }));

    const orbitMaterials = [...orbitLanes.map(() => 0x527b91), 0x5d758d].map((color, index) => new THREE.LineBasicMaterial({ color, transparent: true, opacity: index < orbitLanes.length ? .1 : .16, depthWrite: false }));
    const orbitLines = orbitMaterials.map(material => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(193 * 3), 3));
      const line = new THREE.Line(geometry, material);
      scene.add(line);
      return line;
    });
    const laneAxes = orbitLanes.map(() => ({ u: new THREE.Vector3(), v: new THREE.Vector3(), normal: new THREE.Vector3() }));
    const laneRadii = orbitLanes.map(() => 1);
    const viewNormal = new THREE.Vector3(), viewRight = new THREE.Vector3(), viewUp = new THREE.Vector3(), laneSide = new THREE.Vector3();
    const orbitPoint = (phase: number, index: number) => new THREE.Vector3().copy(laneAxes[index].u).multiplyScalar(Math.cos(phase))
      .addScaledVector(laneAxes[index].v, Math.sin(phase)).multiplyScalar(laneRadii[index]).add(earth.group.position);

    const eccentricity = .45;
    const orbitU = new THREE.Vector3();
    const orbitV = new THREE.Vector3();
    const orbitNormal = new THREE.Vector3();
    let semiMajor = 1;
    let semiMinor = 1;
    function flybyPoint(eccentricAnomaly: number, out: THREE.Vector3) {
      return out.copy(earth.group.position)
        .addScaledVector(orbitU, semiMajor * (Math.cos(eccentricAnomaly) - eccentricity))
        .addScaledVector(orbitV, semiMinor * Math.sin(eccentricAnomaly));
    }

    const local = new THREE.Vector3();
    const target = new THREE.Vector3();
    const screenPosition = new THREE.Vector3();
    const veilBounds = new THREE.Box3();
    let panelBounds: { x: number; y: number; width: number; height: number } | undefined;
    let blockedBounds: { x: number; y: number; width: number; height: number }[] = [];
    function measurePanel() {
      const panel = canvas!.parentElement?.querySelector('.sp-login-form-wrap')?.getBoundingClientRect();
      const copy = canvas!.parentElement?.querySelector('.sp-login-copy')?.getBoundingClientRect();
      const box = canvas!.getBoundingClientRect();
      panelBounds = panel ? { x: panel.left - box.left, y: panel.top - box.top, width: panel.width, height: panel.height } : undefined;
      blockedBounds = [panelBounds, copy && { x: copy.left - box.left, y: copy.top - box.top, width: copy.width, height: copy.height }]
        .filter((rect): rect is NonNullable<typeof rect> => !!rect);
    }
    const cameraOffset = new THREE.Vector3(), markerDirection = new THREE.Vector3(), markerCorner = new THREE.Vector3();
    function marker(model: THREE.Group, wholeOcclusion = false) {
      const radius = 5.1 * 1.05 * model.scale.x;
      if (camera.position.z - model.position.z <= camera.near + radius) return;
      markerDirection.subVectors(model.position, camera.position);
      const distance = markerDirection.length();
      markerDirection.divideScalar(distance);
      const b = cameraOffset.dot(markerDirection);
      const disc = b * b - cameraOffset.lengthSq() + earthRadius ** 2;
      const hit = disc >= 0 ? -b - Math.sqrt(disc) : Infinity;
      if (wholeOcclusion) {
        const offset = Math.acos(THREE.MathUtils.clamp(-b / cameraOffset.length(), -1, 1));
        if (hit > 0 && hit < distance - radius && offset + Math.asin(Math.min(1, radius / distance)) <= Math.asin(earthRadius / cameraOffset.length())) return;
      } else {
        const paddedDisc = b * b - cameraOffset.lengthSq() + (earthRadius * 1.006 + radius) ** 2;
        if (paddedDisc >= 0 && -b - Math.sqrt(paddedDisc) > 0 && -b - Math.sqrt(paddedDisc) < distance) return;
      }
      let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
      for (let i = 0; i < 8; i++) {
        markerCorner.set(model.position.x + (i & 1 ? radius : -radius), model.position.y + (i & 2 ? radius : -radius),
          model.position.z + (i & 4 ? radius : -radius)).project(camera);
        const x = (markerCorner.x + 1) * width / 2, y = (1 - markerCorner.y) * height / 2;
        left = Math.min(left, x); right = Math.max(right, x);
        top = Math.min(top, y); bottom = Math.max(bottom, y);
      }
      if (right < 0 || left > width || bottom < 0 || top > height) return;
      return { x: (left + right) / 2, y: (top + bottom) / 2, radius: Math.max(right - left, bottom - top) / 2, foreground: hit > distance && hit < Infinity,
        clean: left >= 20 && right <= width - 20 && top >= 20 && bottom <= height - 20
          && !blockedBounds.some(rect => right > rect.x - 20 && left < rect.x + rect.width + 20 && bottom > rect.y - 20 && top < rect.y + rect.height + 20) };
    }
    function updateVisibility(time: number) {
      const objects = [...models, flyby];
      const points = objects.map(model => marker(model, true));
      const gap = Math.max(90, Math.min(170, Math.min(width, height) * .12));
      const overlaps = (a: number, b: number, padding = 0) => !!points[a] && !!points[b]
        && Math.hypot(points[a]!.x - points[b]!.x, points[a]!.y - points[b]!.y) < points[a]!.radius + points[b]!.radius + gap + padding;
      const candidates = appearances.map((_, index) => index).filter(index => time >= appearances[index].revealAt && points[index]
        && (index < models.length || width >= 1280));
      // Keep admitted satellites stable; the close flyby gets room for its passage.
      candidates.sort((a, b) => Number(b === models.length) - Number(a === models.length)
        || Number(appearances[b].wanted) - Number(appearances[a].wanted) || appearances[b].alpha - appearances[a].alpha || a - b);
      const limit = Math.min(width < 1280 ? 3 : width >= 2200 ? 5 : 4, 2 + Math.floor(Math.max(0, time - openingTime) / 18));
      const selected: number[] = [];
      for (const index of candidates) {
        if (selected.length < limit && selected.every(other => !overlaps(index, other, appearances[index].wanted ? 0 : 30))) selected.push(index);
      }
      const delta = Math.min(.1, Math.max(0, time - visibilityTime));
      visibilityTime = time;
      appearances.forEach((appearance, index) => {
        appearance.wanted = selected.includes(index);
        const waiting = appearances.some((other, otherIndex) => otherIndex !== index && !selected.includes(otherIndex)
          && other.alpha > .01 && overlaps(index, otherIndex));
        appearance.alpha = THREE.MathUtils.damp(appearance.alpha, appearance.wanted && !waiting ? 1 : 0, 3, delta);
        if (appearance.alpha < .003) appearance.alpha = 0;
        if (appearance.alpha > .997) appearance.alpha = 1;
        objects[index].visible = appearance.alpha > 0 && (index < models.length || width >= 1280);
        for (const { material, opacity } of appearance.materials) material.opacity = opacity * appearance.alpha;
      });
    }
    const projectionRotation = new THREE.Matrix4();
    function projectedSpan(size: THREE.Vector3, attitude: THREE.Quaternion) {
      const m = projectionRotation.makeRotationFromQuaternion(attitude).elements;
      return Math.max(
        Math.abs(m[0]) * size.x + Math.abs(m[4]) * size.y + Math.abs(m[8]) * size.z,
        Math.abs(m[1]) * size.x + Math.abs(m[5]) * size.y + Math.abs(m[9]) * size.z,
      );
    }
    function paint(time: number, renderFrame = true) {
      if (disposed || !prepared) return;
      earth.update(time);
      const communication = (time / 6) % 1;
      const signals = [communication < .5 ? Math.sin(communication * Math.PI * 2) ** 2 : 0, communication >= .5 ? Math.sin((communication - .5) * Math.PI * 2) ** 2 : 0];
      models.forEach((model, index) => {
        const lane = orbitLanes[index];
        model.position.copy(orbitPoint(lane.phase + time * lane.angularSpeed, index));
        model.up.copy(laneAxes[index].normal);
        model.lookAt(earth.group.position);
        model.rotateZ(Math.sin(time * .05 + index) * .1);
        updateCinematicSatellite(model, time);
      });

      // Kepler's equation keeps this closed orbit continuous and faster near Earth.
      const meanAnomaly = (flybyStartMean - time / flybyPeriod * Math.PI * 2) % (Math.PI * 2);
      let eccentricAnomaly = meanAnomaly;
      for (let i = 0; i < 6; i++) {
        eccentricAnomaly -= (eccentricAnomaly - eccentricity * Math.sin(eccentricAnomaly) - meanAnomaly) / (1 - eccentricity * Math.cos(eccentricAnomaly));
      }
      flybyPoint(eccentricAnomaly, flyby.position);
      const cameraDistance = camera.position.distanceTo(flyby.position);
      const tangent = Math.tan(THREE.MathUtils.degToRad(camera.fov * .5));
      const focal = height / (2 * tangent);
      const distance = Math.max(camera.near, camera.position.z - flyby.position.z);
      const distanceScale = THREE.MathUtils.clamp((flybyNearDistance / cameraDistance) ** 1.2, .055, 1);
      flyby.up.copy(orbitNormal);
      flyby.lookAt(earth.group.position);
      flyby.rotateZ(.28);
      if (isNasaModel) flyby.rotateY(Math.PI * .5);
      else updateCinematicSatellite(fallback, time);
      const secondDepth = Math.max(camera.near, camera.position.z - models[1].position.z);
      const referencePixels = focal * projectedSpan(satelliteSize, models[1].quaternion) * models[1].scale.x / secondDepth;
      const minimumPixels = THREE.MathUtils.clamp(referencePixels, 20, 36);
      const minimumScale = minimumPixels * distance / (focal * projectedSpan(flybySize, flyby.quaternion));
      const safeScale = (flyby.position.distanceTo(earth.group.position) - earthRadius * 1.006 - .15) / (5.1 * 1.05);
      flyby.scale.setScalar(Math.min(safeScale, Math.max(flybyNearScale * distanceScale, minimumScale)));
      if (renderFrame) updateVisibility(time);
      models.forEach((model, index) => {
        const signal = signals[index % signals.length];
        const alpha = renderFrame ? appearances[index].alpha : 1;
        target.copy(model.position).sub(earth.group.position).normalize().multiplyScalar(scanSurface.radius).add(earth.group.position);
        scans[index].update(model.position, target, time, (.42 + signal * .58) * alpha, 1.1, scanSurface);
        particleShields[index].update(time + index * 1.3, (.8 + signal * .2) * alpha);
      });
      const flybyAlpha = renderFrame ? appearances[models.length].alpha : 1;
      target.copy(flyby.position).sub(earth.group.position).normalize().multiplyScalar(scanSurface.radius).add(earth.group.position);
      flybyScan.update(flyby.position, target, time, (.52 + signals[0] * .48) * flybyAlpha, 1.55, scanSurface);
      flybyScan.group.visible = flyby.visible && camera.position.z - flyby.position.z > camera.near + .2;
      particleShields[models.length].update(time + 2.6, (.8 + Math.sqrt(signals[0]) * .2) * flybyAlpha);
      const physicalWidth = 8 * flyby.scale.x;
      stars.rotation.y = time * .0003;
      if (!renderFrame) return;

      // Render only the flyby to the glass veil, using the same camera and geometry.
      veilCtx.clearRect(0, 0, width, height);
      const shieldRadius = 5.1 * 1.05 * flyby.scale.x;
      const panel = panelBounds;
      veilBounds.min.copy(flyby.position).min(target);
      veilBounds.max.copy(flyby.position).max(target);
      veilBounds.expandByScalar(Math.max(shieldRadius, 1.55 * 1.12 * Math.SQRT2) + .15);
      let overlapsPanel = flyby.visible && !!panel && camera.position.z - veilBounds.min.z > camera.near;
      if (overlapsPanel && panel && camera.position.z - veilBounds.max.z > camera.near) {
        let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
        for (let corner = 0; corner < 8; corner++) {
          screenPosition.set(corner & 1 ? veilBounds.max.x : veilBounds.min.x,
            corner & 2 ? veilBounds.max.y : veilBounds.min.y,
            corner & 4 ? veilBounds.max.z : veilBounds.min.z).project(camera);
          const x = (screenPosition.x + 1) * width / 2, y = (1 - screenPosition.y) * height / 2;
          left = Math.min(left, x); right = Math.max(right, x);
          top = Math.min(top, y); bottom = Math.max(bottom, y);
        }
        overlapsPanel = right > panel.x && left < panel.x + panel.width && bottom > panel.y && top < panel.y + panel.height;
      }
      if (overlapsPanel && panel) {
        camera.layers.set(1);
        renderer.render(scene, camera);
        veilCtx.save();
        veilCtx.beginPath();
        veilCtx.rect(panel.x, panel.y, panel.width, panel.height);
        veilCtx.clip();
        veilCtx.filter = 'blur(2px)';
        veilCtx.globalAlpha = THREE.MathUtils.lerp(.24, .08, smooth(focal * physicalWidth / distance, 120, 600));
        veilCtx.drawImage(canvas!, 0, 0, width, height);
        veilCtx.restore();
      }
      camera.layers.set(0);
      renderer.shadowMap.needsUpdate = true;
      renderer.render(scene, camera);
    }

    function resize() {
      if (!prepared) return;
      width = canvas!.clientWidth;
      height = canvas!.clientHeight;
      if (!width || !height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2, Math.max(1, Math.sqrt(4_000_000 / (width * height))));
      renderer.setPixelRatio(dpr);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld();
      veil!.width = Math.round(width * dpr);
      veil!.height = Math.round(height * dpr);
      veilCtx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      const distance = 24;
      const tangent = Math.tan(THREE.MathUtils.degToRad(camera.fov * .5));
      earth.group.position.set(-.4 * distance * tangent * camera.aspect, -.55 * distance * tangent, 18 - distance);
      cameraOffset.copy(camera.position).sub(earth.group.position);
      const desiredRadius = Math.min(width * .38, height * .68);
      earthRadius = desiredRadius * distance * 2 * tangent / height;
      earth.group.scale.setScalar(earthRadius);
      scanSurface.radius = earthRadius * 1.0015;
      sun.target.position.copy(earth.group.position);
      viewNormal.copy(camera.position).sub(earth.group.position).normalize();
      viewRight.set(1, 0, 0).addScaledVector(viewNormal, -viewNormal.x).normalize();
      viewUp.crossVectors(viewNormal, viewRight).normalize();
      orbitLanes.forEach((lane, index) => {
        const c = Math.cos(lane.inclination), s = Math.sin(lane.inclination);
        laneAxes[index].u.copy(viewRight).multiplyScalar(c).addScaledVector(viewUp, s);
        laneSide.copy(viewRight).multiplyScalar(-s).addScaledVector(viewUp, c);
        laneAxes[index].v.copy(laneSide).multiplyScalar(Math.cos(lane.tilt)).addScaledVector(viewNormal, Math.sin(lane.tilt));
        laneAxes[index].normal.crossVectors(laneAxes[index].u, laneAxes[index].v).normalize().multiplyScalar(Math.sign(lane.angularSpeed));
        const shieldRadius = lane.scale * 5.1 * 1.05;
        const previousClearance = index ? laneRadii[index - 1] + orbitLanes[index - 1].scale * 5.1 * 1.05 + shieldRadius + .08 : 0;
        laneRadii[index] = Math.max(earthRadius * lane.radiusFactor, earthRadius * 1.006 + shieldRadius + .15, previousClearance);
      });
      // Aim periapsis just in front of the camera; the orbital plane crosses the
      // screen from lower left to upper right, while the whole model stays clear.
      flybyNearDistance = Math.max(4.2, height / (2 * tangent) * 8 * flybyNearScale / Math.min(height * .9, width * .56));
      const periapsis = new THREE.Vector3(.15 * flybyNearDistance * tangent * camera.aspect, .12 * flybyNearDistance * tangent, camera.position.z - flybyNearDistance);
      orbitU.copy(periapsis).sub(earth.group.position).normalize();
      orbitV.set(1, .6, 0).addScaledVector(orbitU, -new THREE.Vector3(1, .6, 0).dot(orbitU)).normalize();
      orbitNormal.crossVectors(orbitU, orbitV).normalize();
      const perigeeRadius = Math.max(periapsis.distanceTo(earth.group.position), earthRadius * 1.02 + 2.2);
      semiMajor = perigeeRadius / (1 - eccentricity);
      semiMinor = semiMajor * Math.sqrt(1 - eccentricity * eccentricity);
      orbitLines.forEach((line, index) => {
        const positions = line.geometry.getAttribute('position') as THREE.BufferAttribute;
        for (let i = 0; i <= 192; i++) {
          const point = index === orbitLanes.length ? flybyPoint(i / 192 * Math.PI * 2, local) : orbitPoint(i / 192 * Math.PI * 2, index);
          positions.setXYZ(i, point.x, point.y, point.z);
        }
        positions.needsUpdate = true;
        line.geometry.computeBoundingSphere();
      });
      measurePanel();
      if (ready) paint(sceneTime());
    }
    async function randomizeOpening() {
      function refreshBounds() {
        measurePanel();
        return JSON.stringify([width, height, blockedBounds]);
      }
      let boundsVersion = refreshBounds();
      openingTime = visibilityTime = sceneTime();
      const gap = Math.max(130, Math.min(width, height) * .18);
      const markers = () => models.map((model, index) => { const point = marker(model); return point && { ...point, index }; })
        .filter((point): point is NonNullable<typeof point> => !!point);
      let bestScore = Infinity;
      let bestPhases = orbitLanes.map(lane => lane.phase);
      let openingPair: number[] = [];
      const openingCandidates: (ReturnType<typeof markers>[number] & { phase: number })[] = [];
      for (let attempt = 0; attempt < 96; attempt++) {
        // More of the waiting constellation starts on the rear hemisphere.
        orbitLanes.forEach(lane => { lane.phase = Math.random() < .65 ? Math.PI * (1 + Math.random()) : Math.random() * Math.PI * 2; });
        paint(openingTime, false);
        const points = markers().filter(point => point.clean).map(point => ({ ...point, phase: orbitLanes[point.index].phase }));
        for (const a of points) {
          for (const b of openingCandidates) {
            if (a.index === b.index) continue;
            if (Math.hypot(a.x - b.x, a.y - b.y) < a.radius + b.radius + gap) continue;
            let score = a.x < width * .55 && a.foreground || b.x < width * .55 && b.foreground ? 0 : 1000;
            if (a.x > width * .5 && b.x > width * .5) score += 5000;
            if (Math.abs(a.x - b.x) < width * .26 && Math.abs(a.y - b.y) < height * .34) score += 500;
            if (a.foreground && b.foreground) score += 50;
            if (score < bestScore) {
              bestScore = score;
              bestPhases = orbitLanes.map(lane => lane.phase);
              bestPhases[a.index] = a.phase;
              bestPhases[b.index] = b.phase;
              openingPair = [a.index, b.index];
            }
          }
          openingCandidates.push(a);
        }
        if (bestScore === 0) break;
        if (attempt % 8 === 7 && attempt < 95) {
          await yieldTask(); if (disposed) return;
          const nextVersion = refreshBounds();
          if (nextVersion !== boundsVersion) { boundsVersion = nextVersion; bestScore = Infinity; openingCandidates.length = 0; }
        }
      }
      orbitLanes.forEach((lane, index) => { lane.phase = bestPhases[index]; });
      const waiting = orbitLanes.map((_, index) => index).filter(index => !openingPair.includes(index));
      for (let i = waiting.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [waiting[i], waiting[j]] = [waiting[j], waiting[i]];
      }
      openingPair.forEach(index => { appearances[index].revealAt = openingTime; appearances[index].alpha = 1; appearances[index].wanted = true; });
      waiting.forEach((index, order) => { appearances[index].revealAt = openingTime + 14 + order * 10 + Math.random() * 4; });
      appearances[models.length].revealAt = openingTime + 30 + Math.random() * 12;
      paint(openingTime, false);
      let flybyChosen = false;
      for (let attempt = 0; attempt < 32; attempt++) {
        flybyStartMean = Math.random() * Math.PI * 2;
        paint(openingTime, false);
        const point = flyby.visible ? marker(flyby, true) : undefined;
        const smallMarkers = markers();
        const depth = camera.position.z - flyby.position.z;
        const radius = 5.1 * 1.05 * flyby.scale.x;
        const focal = height / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov * .5)));
        const bodySpan = focal * projectedSpan(flybySize, flyby.quaternion) * flyby.scale.x / Math.max(camera.near, depth);
        if (camera.position.distanceTo(flyby.position) >= flybyNearDistance * 2.5
          && depth > camera.near + radius && bodySpan <= Math.min(180, height * .18)
          && (!point || point.clean && point.radius * 2 <= Math.min(240, height * .25)
            && smallMarkers.every(small => Math.hypot(point.x - small.x, point.y - small.y) >= point.radius + small.radius + gap))) {
          flybyChosen = true;
          break;
        }
        if (attempt % 8 === 7) { await yieldTask(); if (disposed) return; refreshBounds(); }
      }
      if (!flybyChosen) flybyStartMean = Math.PI + (Math.random() - .5) * .3;
    }
    function updateLoop() {
      renderer.setAnimationLoop(null);
      if (!ready || disposed || document.hidden) return;
      if (motion.matches) { paint(sceneTime()); return; }
      lastTime = performance.now();
      renderer.setAnimationLoop(now => {
        const delta = Math.min((now - lastTime) / 1000, .1);
        elapsed += delta;
        lastTime = now;
        if (now - lastPaint < 1000 / 60 - 1) return;
        lastPaint = now - (now - lastPaint) % (1000 / 60);
        paint(elapsed);
      });
    }
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    const panelObserver = new ResizeObserver(measurePanel);
    let panelElement = canvas.parentElement?.querySelector('.sp-login-form-wrap');
    if (panelElement) panelObserver.observe(panelElement);
    const formObserver = new MutationObserver(() => {
      const next = canvas.parentElement?.querySelector('.sp-login-form-wrap');
      if (next !== panelElement) {
        panelObserver.disconnect();
        panelElement = next;
        if (next) panelObserver.observe(next);
      }
      measurePanel();
    });
    const panelContainer = canvas.parentElement?.querySelector('.sp-login-panel');
    if (panelContainer) formObserver.observe(panelContainer, { childList: true, subtree: true });
    const scrollContainer = canvas.parentElement;
    scrollContainer?.addEventListener('scroll', measurePanel, { passive: true });
    motion.addEventListener('change', updateLoop);
    document.addEventListener('visibilitychange', updateLoop);
    async function initialize() {
      await yieldTask();
      if (disposed) return;
      await prewarmCinematicSatelliteTextures(yieldTask, () => disposed);
      if (disposed) return;
      const room = new RoomEnvironment();
      const pmrem = new THREE.PMREMGenerator(renderer);
      environment = pmrem.fromScene(room, .04);
      scene.environment = environment.texture;
      room.dispose();
      pmrem.dispose();
      for (let index = 0; index <= orbitLanes.length; index++) {
        await yieldTask();
        if (disposed) return;
        const scan = createCinematicScan(0x2c6fff, index === orbitLanes.length ? .3 : .6);
        if (index === orbitLanes.length) {
          flybyScan = scan;
          scan.group.traverse(object => object.layers.enable(1));
        }
        scans.push(scan);
        scene.add(scan.group);
      }
      for (const lane of orbitLanes) {
        await yieldTask();
        if (disposed) return;
        const model = createCinematicSatellite();
        model.scale.setScalar(lane.scale);
        models.push(model);
        scene.add(model);
      }
      fallback = createCinematicSatellite();
      flybyModel = fallback;
      new THREE.Box3().setFromObject(fallback).getSize(satelliteSize);
      flybySize.copy(satelliteSize);
      flyby.add(fallback);
      for (const model of [...models, flyby]) {
        await yieldTask();
        if (disposed) return;
        const shield = createCinematicShield();
        shield.group.scale.setScalar(5.1);
        model.add(shield.group);
        particleShields.push(shield);
      }
      flyby.traverse(object => object.layers.enable(1));
      prepareFlybyModel();
      await Promise.all([earth.ready, loadFlyby()]);
      if (disposed) return;
      // Compile the fade variant up front, retaining opaque depth and metal shading.
      [...models, flyby].forEach((model, index) => {
        const materials = new Set<THREE.Material>();
        model.traverse(object => {
          if (!(object instanceof THREE.Mesh)) return;
          for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            if (!(material instanceof THREE.ShaderMaterial)) materials.add(material);
          }
        });
        for (const material of materials) {
          material.alphaHash = true;
          appearances[index].materials.push({ material, opacity: material.opacity });
        }
      });
      const textures = new Set<THREE.Texture>();
      scene.traverse(object => {
        if (!(object instanceof THREE.Mesh)) return;
        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
          for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
        }
      });
      for (const texture of textures) {
        await uploadTexture(texture);
        if (disposed) return;
      }
      prepared = true;
      resize();
      await randomizeOpening();
      if (disposed) return;
      paint(sceneTime(), false);
      await renderer.compileAsync(scene, camera);
      if (disposed) return;
      paint(sceneTime());
      await yieldTask();
      if (disposed) return;
      ready = true;
      onReady?.();
      updateLoop();
    }
    void initialize().catch(() => { if (!disposed) onUnavailable(); });
    return () => {
      disposed = true;
      renderer.setAnimationLoop(null);
      observer.disconnect();
      panelObserver.disconnect();
      formObserver.disconnect();
      scrollContainer?.removeEventListener('scroll', measurePanel);
      motion.removeEventListener('change', updateLoop);
      document.removeEventListener('visibilitychange', updateLoop);
      earth.dispose();
      veilOccluder.geometry.dispose();
      veilOccluder.material.dispose();
      draco.dispose();
      particleShields.forEach(shield => shield.dispose());
      models.forEach(disposeCinematicSatellite);
      if (flybyModel) disposeCinematicSatellite(flybyModel);
      scans.forEach(scan => scan.dispose());
      environment?.dispose();
      starGeometry.dispose();
      starMaterial.dispose();
      orbitLines.forEach(line => line.geometry.dispose());
      orbitMaterials.forEach(material => material.dispose());
      sun.shadow.map?.dispose();
      renderer.dispose();
    };
  }, [onUnavailable, onReady]);

  return <><canvas ref={canvasRef} className="sp-login-space sp-cinematic-space" aria-hidden="true" /><canvas ref={veilRef} className="sp-login-space sp-login-satellite-veil" aria-hidden="true" /></>;
}
