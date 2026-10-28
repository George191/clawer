import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { createCinematicEarth } from './CinematicEarth';
import { createCinematicScan } from './CinematicScan';
import { createCinematicShield } from './CinematicShield';
import { createCinematicSatellite, disposeCinematicSatellite, updateCinematicSatellite, solarTexture, weatheredMetalTextures, foilTexture } from './CinematicSatellite';

const smooth = THREE.MathUtils.smoothstep;

export default function CinematicSpace({ onUnavailable }: { onUnavailable: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const veilRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const veil = veilRef.current;
    const veilContext = veil?.getContext('2d');
    if (!canvas || !veil || !veilContext) return;
    const veilCtx = veilContext;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
    } catch { onUnavailable(); return; }

    let disposed = false;
    let width = 1;
    let height = 1;
    let elapsed = 0;
    let lastTime = performance.now();
    let lastPaint = 0;
    const flybyPeriod = 72;
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

    const room = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(room, .04);
    scene.environment = environment.texture;
    scene.environmentIntensity = .35;
    room.dispose();
    pmrem.dispose();

    function sceneTime() { return motion.matches && elapsed === 0 ? flybyPeriod * .05 : elapsed; }
    function repaint() { if (!disposed && motion.matches) paint(sceneTime()); }
    const textureBudget = canvas.clientWidth * Math.min(window.devicePixelRatio || 1, 2) >= 1920 ? 8192 : 4096;
    const earth = createCinematicEarth(sunDirection, repaint, {
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

    const orbitLanes = [
      { radiusFactor: 1.10, inclination: -1.35, tilt: .30, phase: .47, angularSpeed: .0120, scale: .075 },
      { radiusFactor: 1.16, inclination: -1.30, tilt: .34, phase: 1.41, angularSpeed: .0122, scale: .050 },
      { radiusFactor: 1.22, inclination: -1.25, tilt: .38, phase: 2.74, angularSpeed: .0119, scale: .048 },
      { radiusFactor: 1.28, inclination: -1.40, tilt: .32, phase: 4.24, angularSpeed: .0121, scale: .046 },
      { radiusFactor: 1.34, inclination: -1.15, tilt: .30, phase: 5.30, angularSpeed: .0118, scale: .044 },
    ];
    const models = orbitLanes.map(lane => {
      const model = createCinematicSatellite();
      model.scale.setScalar(lane.scale);
      return model;
    });
    scene.add(...models);

    const flybyScan = createCinematicScan(0x2c6fff, .3);
    const scans = [...models.map(() => createCinematicScan(0x2c6fff)), flybyScan];
    scans.forEach((scan, index) => {
      if (index === models.length) scan.group.traverse(object => object.layers.enable(1));
      scene.add(scan.group);
    });
    const flyby = new THREE.Group();
    const flybyNearScale = .34;
    let flybyNearDistance = 4.2;
    const fallback = createCinematicSatellite();
    const satelliteSize = new THREE.Box3().setFromObject(fallback).getSize(new THREE.Vector3());
    const flybySize = satelliteSize.clone();
    flyby.add(fallback);
    flyby.scale.setScalar(flybyNearScale);
    const particleShields = [...models, flyby].map(model => {
      const shield = createCinematicShield();
      shield.group.scale.setScalar(5.1);
      model.add(shield.group);
      return shield;
    });
    const flybyShield = particleShields[models.length];
    flyby.traverse(object => object.layers.enable(1));
    scene.add(flyby);
    let flybyModel: THREE.Group = fallback;
    let isNasaModel = false;
    function prepareFlybyModel() {
      flybyModel.traverse(object => {
        object.layers.enable(1);
        if (!(object instanceof THREE.Mesh)) return;
        object.castShadow = object.receiveShadow = true;
      });
    }
    prepareFlybyModel();
    const draco = new DRACOLoader();
    draco.setDecoderPath('/space/draco/').setWorkerLimit(1);
    const loader = new GLTFLoader().setDRACOLoader(draco);
    loader.load('/space/iss.glb', gltf => {
      if (disposed) { disposeCinematicSatellite(gltf.scene); draco.dispose(); return; }
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
      gltf.scene.traverse(object => {
        if (!(object instanceof THREE.Mesh)) return;
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
      });
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
      repaint();
    }, undefined, () => { if (disposed) draco.dispose(); });

    const orbitMaterials = [...orbitLanes.map(() => 0x527b91), 0x5d758d].map((color, index) => new THREE.LineBasicMaterial({ color, transparent: true, opacity: index < models.length ? .1 : .16, depthWrite: false }));
    const orbitLines = orbitMaterials.map(material => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(193 * 3), 3));
      const line = new THREE.Line(geometry, material);
      scene.add(line);
      return line;
    });
    const laneAxes = orbitLanes.map(() => ({ u: new THREE.Vector3(), v: new THREE.Vector3() }));
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
    const projectionRotation = new THREE.Matrix4();
    function projectedSpan(size: THREE.Vector3, attitude: THREE.Quaternion) {
      const m = projectionRotation.makeRotationFromQuaternion(attitude).elements;
      return Math.max(
        Math.abs(m[0]) * size.x + Math.abs(m[4]) * size.y + Math.abs(m[8]) * size.z,
        Math.abs(m[1]) * size.x + Math.abs(m[5]) * size.y + Math.abs(m[9]) * size.z,
      );
    }
    function paint(time: number) {
      if (disposed) return;
      earth.update(time);
      const communication = (time / 6) % 1;
      const signals = [communication < .5 ? Math.sin(communication * Math.PI * 2) ** 2 : 0, communication >= .5 ? Math.sin((communication - .5) * Math.PI * 2) ** 2 : 0];
      models.forEach((model, index) => {
        const lane = orbitLanes[index];
        const signal = signals[index % signals.length];
        model.position.copy(orbitPoint(lane.phase + time * lane.angularSpeed, index));
        model.lookAt(earth.group.position);
        model.rotateZ(Math.sin(time * .05 + index) * .1);
        updateCinematicSatellite(model, time);
        target.copy(model.position).sub(earth.group.position).normalize().multiplyScalar(scanSurface.radius).add(earth.group.position);
        scans[index].update(model.position, target, time, .42 + signal * .58, 1.1, scanSurface);
        particleShields[index].update(time + index * 1.3, .8 + signal * .2);
      });

      // Kepler's equation keeps this closed orbit continuous and faster near Earth.
      const meanAnomaly = (.32 - time / flybyPeriod * Math.PI * 2) % (Math.PI * 2);
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
      flyby.visible = width >= 1280;
      target.copy(flyby.position).sub(earth.group.position).normalize().multiplyScalar(scanSurface.radius).add(earth.group.position);
      flybyScan.update(flyby.position, target, time, .52 + signals[0] * .48, 1.55, scanSurface);
      flybyScan.group.visible = flyby.visible && camera.position.z - flyby.position.z > camera.near + .2;
      flybyShield.update(time + 2.6, .8 + Math.sqrt(signals[0]) * .2);
      const physicalWidth = 8 * flyby.scale.x;
      stars.rotation.y = time * .0003;

      // Render only the flyby to the glass veil, using the same camera and geometry.
      veilCtx.clearRect(0, 0, width, height);
      const panel = canvas!.parentElement?.querySelector('.sp-login-form-wrap')?.getBoundingClientRect();
      const canvasBox = canvas!.getBoundingClientRect();
      if (flyby.visible && panel) {
        camera.layers.set(1);
        renderer.render(scene, camera);
        veilCtx.save();
        veilCtx.beginPath();
        veilCtx.rect(panel.left - canvasBox.left, panel.top - canvasBox.top, panel.width, panel.height);
        veilCtx.clip();
        veilCtx.filter = 'blur(2px)';
        veilCtx.globalAlpha = THREE.MathUtils.lerp(.24, .08, smooth(focal * physicalWidth / distance, 120, 600));
        veilCtx.drawImage(canvas!, 0, 0, width, height);
        veilCtx.restore();
      }
      camera.layers.set(0);
      renderer.render(scene, camera);
    }

    function resize() {
      width = canvas!.clientWidth;
      height = canvas!.clientHeight;
      if (!width || !height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      renderer.setPixelRatio(dpr);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      veil!.width = Math.round(width * dpr);
      veil!.height = Math.round(height * dpr);
      veilCtx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      const distance = 24;
      const tangent = Math.tan(THREE.MathUtils.degToRad(camera.fov * .5));
      earth.group.position.set(-.4 * distance * tangent * camera.aspect, -.55 * distance * tangent, 18 - distance);
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
          const point = index === models.length ? flybyPoint(i / 192 * Math.PI * 2, local) : orbitPoint(i / 192 * Math.PI * 2, index);
          positions.setXYZ(i, point.x, point.y, point.z);
        }
        positions.needsUpdate = true;
        line.geometry.computeBoundingSphere();
      });
      paint(sceneTime());
    }
    function updateLoop() {
      renderer.setAnimationLoop(null);
      if (document.hidden) return;
      if (motion.matches) { paint(sceneTime()); return; }
      lastTime = performance.now();
      renderer.setAnimationLoop(now => {
        const delta = Math.min((now - lastTime) / 1000, .1);
        elapsed += delta;
        lastTime = now;
        if (now - lastPaint < 1000 / 30) return;
        lastPaint = now;
        paint(elapsed);
      });
    }
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    motion.addEventListener('change', updateLoop);
    document.addEventListener('visibilitychange', updateLoop);
    resize();
    updateLoop();
    return () => {
      disposed = true;
      renderer.setAnimationLoop(null);
      observer.disconnect();
      motion.removeEventListener('change', updateLoop);
      document.removeEventListener('visibilitychange', updateLoop);
      earth.dispose();
      veilOccluder.geometry.dispose();
      veilOccluder.material.dispose();
      draco.dispose();
      particleShields.forEach(shield => shield.dispose());
      models.forEach(disposeCinematicSatellite);
      disposeCinematicSatellite(flybyModel);
      scans.forEach(scan => scan.dispose());
      environment.dispose();
      starGeometry.dispose();
      starMaterial.dispose();
      orbitLines.forEach(line => line.geometry.dispose());
      orbitMaterials.forEach(material => material.dispose());
      sun.shadow.map?.dispose();
      renderer.dispose();
    };
  }, [onUnavailable]);

  return <><canvas ref={canvasRef} className="sp-login-space sp-cinematic-space" aria-hidden="true" /><canvas ref={veilRef} className="sp-login-space sp-login-satellite-veil" aria-hidden="true" /></>;
}
