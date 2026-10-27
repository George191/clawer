import * as THREE from 'three';

type Position = [number, number, number];

function metalNoise(x: number, y: number, cellsX: number, cellsY: number, seed: number) {
  const u = x / 512 * cellsX;
  const v = y / 512 * cellsY;
  const ix = Math.floor(u);
  const iy = Math.floor(v);
  const tx = (u - ix) ** 2 * (3 - 2 * (u - ix));
  const ty = (v - iy) ** 2 * (3 - 2 * (v - iy));
  const hash = (a: number, b: number) => {
    let value = Math.imul(a % cellsX, 374761393) ^ Math.imul(b % cellsY, 668265263) ^ seed;
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
  };
  const top = THREE.MathUtils.lerp(hash(ix, iy), hash(ix + 1, iy), tx);
  const bottom = THREE.MathUtils.lerp(hash(ix, iy + 1), hash(ix + 1, iy + 1), tx);
  return THREE.MathUtils.lerp(top, bottom, ty) - .5;
}

function wrappedScratch(ctx: CanvasRenderingContext2D, x: number, y: number, dx: number, dy: number) {
  ctx.beginPath();
  for (const ox of [-512, 0, 512]) for (const oy of [-512, 0, 512]) {
    ctx.moveTo(x + ox, y + oy);
    ctx.lineTo(x + dx + ox, y + dy + oy);
  }
  ctx.stroke();
}

export function weatheredMetalTextures(): { map: THREE.CanvasTexture; roughnessMap: THREE.CanvasTexture; bumpMap: THREE.CanvasTexture } {
  const canvases = Array.from({ length: 3 }, () => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 512;
    return canvas;
  });
  const contexts = canvases.map(canvas => canvas.getContext('2d')!);
  const pixels = contexts.map(ctx => ctx.createImageData(512, 512));
  let seed = 41873;
  const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  for (let y = 0; y < 512; y++) {
    for (let x = 0; x < 512; x++) {
      const coarse = metalNoise(x, y, 5, 5, 37);
      const patina = metalNoise(x, y, 23, 19, 81);
      const brushed = metalNoise(x, y, 4, 173, 53);
      const grain = random() - .5;
      const color = 224 + coarse * 38 + patina * 20 + brushed * 4 + grain * 5;
      const roughness = 200 + coarse * 78 + patina * 54 + brushed * 18 + grain * 5;
      const height = 128 + patina * 5 + brushed * 17 + grain * 6;
      const index = (y * 512 + x) * 4;
      pixels[0].data.set([color + coarse * 3, color, color - coarse * 3, 255], index);
      pixels[1].data.set([roughness, roughness, roughness, 255], index);
      pixels[2].data.set([height, height, height, 255], index);
    }
  }
  contexts.forEach((ctx, index) => { ctx.putImageData(pixels[index], 0, 0); ctx.lineCap = 'round'; });
  for (let i = 0; i < 1100; i++) {
    const x = random() * 512;
    const y = random() * 512;
    const length = 3 + random() ** 2 * 85;
    const dy = (random() - .5) * (i % 13 === 0 ? length : 2);
    contexts[0].strokeStyle = `rgba(255,255,255,${.035 + random() * .11})`;
    contexts[1].strokeStyle = `rgba(85,85,85,${.08 + random() * .2})`;
    contexts[2].strokeStyle = `rgba(85,85,85,${.08 + random() * .2})`;
    for (const ctx of contexts) {
      ctx.lineWidth = .35 + random() * .45;
      wrappedScratch(ctx, x, y, length, dy);
    }
  }
  // A few offset joints suggest manufactured panels without a repeating brick grid.
  for (const [x, y, dx, dy] of [[117, -18, 0, 341], [117, 323, 146, 0], [364, 127, 167, 0]]) {
    for (let index = 0; index < contexts.length; index++) {
      const ctx = contexts[index];
      ctx.lineWidth = index === 1 ? 1.5 : .8;
      ctx.strokeStyle = index === 1 ? 'rgba(245,245,245,.6)' : 'rgba(65,65,65,.42)';
      wrappedScratch(ctx, x, y, dx, dy);
      ctx.strokeStyle = 'rgba(250,250,250,.25)';
      wrappedScratch(ctx, x + 1.1, y + 1.1, dx, dy);
    }
  }
  const [map, roughnessMap, bumpMap] = canvases.map(canvas => {
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.anisotropy = 8;
    return texture;
  });
  map.colorSpace = THREE.SRGBColorSpace;
  return { map, roughnessMap, bumpMap };
}

export function solarTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 768;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#788794';
  ctx.fillRect(0, 0, 512, 768);
  for (let row = 0; row < 12; row++) {
    for (let col = 0; col < 8; col++) {
      const x = col * 64 + 3;
      const y = row * 64 + 3;
      const sheen = ctx.createLinearGradient(x, y, x + 58, y + 58);
      sheen.addColorStop(0, '#173e6b');
      sheen.addColorStop(.45, (row + col) % 3 === 0 ? '#142c53' : '#0b2448');
      sheen.addColorStop(1, '#07172e');
      ctx.beginPath();
      ctx.moveTo(x + 7, y);
      ctx.lineTo(x + 51, y);
      ctx.lineTo(x + 58, y + 7);
      ctx.lineTo(x + 58, y + 51);
      ctx.lineTo(x + 51, y + 58);
      ctx.lineTo(x + 7, y + 58);
      ctx.lineTo(x, y + 51);
      ctx.lineTo(x, y + 7);
      ctx.closePath();
      ctx.fillStyle = sheen;
      ctx.fill();
      ctx.strokeStyle = 'rgba(150,186,213,.27)';
      ctx.lineWidth = .6;
      for (let line = 5; line < 58; line += 4) {
        ctx.beginPath();
        ctx.moveTo(x + 3, y + line);
        ctx.lineTo(x + 55, y + line);
        ctx.stroke();
      }
      ctx.strokeStyle = '#94a6b4';
      ctx.lineWidth = 1.2;
      for (const line of [19, 39]) {
        ctx.beginPath();
        ctx.moveTo(x + line, y);
        ctx.lineTo(x + line, y + 58);
        ctx.stroke();
      }
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

export function foilTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 512;
  const ctx = canvas.getContext('2d')!;
  let seed = 9173;
  const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  const pixels = ctx.createImageData(512, 512);
  const tau = Math.PI * 2;
  for (let y = 0; y < 512; y++) {
    for (let x = 0; x < 512; x++) {
      const warp = metalNoise(x, y, 7, 7, 31);
      const fine = metalNoise(x, y, 29, 31, 83);
      const u = x / 512 + warp * .025;
      const v = y / 512 + metalNoise(x, y, 5, 5, 47) * .025;
      const foldA = Math.sin(tau * (u * 17 + v * 11));
      const foldB = Math.sin(tau * (u * 23 - v * 19));
      const crease = (1 - Math.abs(foldA)) ** 12 * 16 - (1 - Math.abs(foldB)) ** 10 * 12;
      const height = 128 + foldA * 9 + foldB * 6 + crease + fine * 9 + (random() - .5) * 4;
      pixels.data.set([height, height, height, 255], (y * 512 + x) * 4);
    }
  }
  ctx.putImageData(pixels, 0, 0);
  ctx.lineWidth = .6;
  for (let i = 0; i < 420; i++) {
    const x = random() * 512;
    const y = random() * 512;
    const dx = (random() - .5) * 32;
    const dy = (random() - .5) * 24;
    ctx.strokeStyle = 'rgba(60,60,60,.2)';
    wrappedScratch(ctx, x, y, dx, dy);
    ctx.strokeStyle = 'rgba(220,220,220,.23)';
    wrappedScratch(ctx, x + .7, y + .7, dx, dy);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = 8;
  return texture;
}

export function createCinematicSatellite(): THREE.Group {
  const model = new THREE.Group();
  model.name = 'Cinematic communications satellite';
  const finish = weatheredMetalTextures();
  const metal = new THREE.MeshStandardMaterial({ ...finish, color: 0xc0c8ce, metalness: .86, roughness: .76, bumpScale: .025 });
  const frame = new THREE.MeshStandardMaterial({ ...finish, color: 0x64737e, metalness: .82, roughness: .85, bumpScale: .03 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x111a22, metalness: .65, roughness: .48 });
  const gold = new THREE.MeshStandardMaterial({ color: 0xb58b37, metalness: .88, roughness: .7, roughnessMap: finish.roughnessMap, bumpMap: foilTexture(), bumpScale: .065 });
  const cells = new THREE.MeshStandardMaterial({ map: solarTexture(), metalness: .62, roughness: .27 });
  const reflector = new THREE.MeshStandardMaterial({ ...finish, color: 0xd3d7d9, metalness: .9, roughness: .58, bumpScale: .012, side: THREE.DoubleSide });
  const radiator = new THREE.MeshStandardMaterial({ ...finish, color: 0xd9dedc, metalness: .38, roughness: .92, bumpScale: .018 });
  const box = new THREE.BoxGeometry(1, 1, 1);
  const cylinder = new THREE.CylinderGeometry(1, 1, 1, 10);

  function matrix(position: Position, scale: Position, rotation: Position = [0, 0, 0]) {
    return new THREE.Matrix4().compose(new THREE.Vector3(...position), new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation)), new THREE.Vector3(...scale));
  }

  function batch(geometry: THREE.BufferGeometry, material: THREE.Material, matrices: THREE.Matrix4[], parent: THREE.Object3D = model) {
    const mesh = new THREE.InstancedMesh(geometry, material, matrices.length);
    matrices.forEach((transform, index) => mesh.setMatrixAt(index, transform));
    mesh.castShadow = mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  function block(position: Position, size: Position, material: THREE.Material, parent: THREE.Object3D = model) {
    const mesh = new THREE.Mesh(box, material);
    mesh.position.set(...position);
    mesh.scale.set(...size);
    mesh.castShadow = mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  function struts(segments: [Position, Position][], radius: number, material: THREE.Material, parent: THREE.Object3D = model) {
    const up = new THREE.Vector3(0, 1, 0);
    batch(cylinder, material, segments.map(([a, b]) => {
      const start = new THREE.Vector3(...a);
      const end = new THREE.Vector3(...b);
      const direction = end.clone().sub(start);
      return new THREE.Matrix4().compose(start.add(end).multiplyScalar(.5), new THREE.Quaternion().setFromUnitVectors(up, direction.clone().normalize()), new THREE.Vector3(radius, direction.length(), radius));
    }), parent);
  }

  block([0, 0, 0], [1.62, 1.38, 1.82], gold);
  block([0, -.69, 0], [1.76, .12, 1.96], frame);
  block([0, .69, 0], [1.76, .1, 1.96], metal);
  block([0, -.23, -.99], [1.36, .65, .18], gold);
  block([.45, .87, -.43], [.7, .28, .72], gold);
  block([-.5, .83, -.4], [.49, .18, .62], dark);

  const rails: THREE.Matrix4[] = [];
  for (const x of [-.83, .83]) {
    for (const z of [-.94, .94]) rails.push(matrix([x, 0, z], [.045, 1.38, .045]));
    for (const y of [-.66, .66]) rails.push(matrix([x, y, 0], [.045, .045, 1.88]));
  }
  for (const y of [-.66, .66]) {
    for (const z of [-.94, .94]) rails.push(matrix([0, y, z], [1.7, .045, .045]));
  }
  batch(box, frame, rails);

  const equipment = [
    matrix([-.33, .17, .965], [.55, .43, .11]),
    matrix([.41, -.32, .965], [.48, .31, .13]),
    matrix([-.85, .13, .25], [.12, .62, .64]),
    matrix([.85, .28, .3], [.12, .51, .77]),
  ];
  batch(box, metal, equipment);
  batch(box, dark, [matrix([-.34, .17, 1.027], [.43, .28, .014]), matrix([.41, -.32, 1.038], [.33, .17, .014])]);
  const bolts: THREE.Matrix4[] = [];
  for (const x of [-.75, -.5, -.25, 0, .25, .5, .75]) {
    for (const y of [-.61, .61]) bolts.push(matrix([x, y, .97], [.022, .024, .022], [Math.PI / 2, 0, 0]));
  }
  for (const x of [-.78, .78]) {
    for (const y of [-.4, -.2, 0, .2, .4]) bolts.push(matrix([x, y, .97], [.022, .024, .022], [Math.PI / 2, 0, 0]));
  }

  const wings: THREE.Group[] = [];
  const solarGeometry = new THREE.PlaneGeometry(1.38, 2.02).rotateX(-Math.PI / 2);
  for (const side of [-1, 1]) {
    const wing = new THREE.Group();
    wing.position.x = side * 1.05;
    wing.rotation.x = .04;
    model.add(wing);
    wings.push(wing);
    const solarFrame: THREE.Matrix4[] = [];
    for (let section = 0; section < 2; section++) {
      const x = side * (.85 + section * 1.5);
      block([x, 0, 0], [1.48, .065, 2.14], dark, wing);
      const panel = new THREE.Mesh(solarGeometry, cells);
      panel.position.set(x, .036, 0);
      panel.castShadow = panel.receiveShadow = true;
      wing.add(panel);
      for (const z of [-1.07, 1.07]) solarFrame.push(matrix([x, .045, z], [1.5, .035, .035]));
      for (const dx of [-.74, .74]) solarFrame.push(matrix([x + dx, .045, 0], [.035, .035, 2.14]));
    }
    batch(box, metal, solarFrame, wing);
    struts([[[0, -.035, -.7], [side * 2.95, -.09, -.7]], [[0, -.035, .7], [side * 2.95, -.09, .7]], [[0, -.18, 0], [side * 1.5, -.08, -.9]], [[0, -.18, 0], [side * 1.5, -.08, .9]]], .027, frame, wing);
  }
  batch(cylinder, metal, [-1, 1].map(side => matrix([side * .99, .02, 0], [.105, .38, .105], [0, 0, Math.PI / 2])));

  const dish = new THREE.Group();
  dish.position.set(0, .34, 1.14);
  dish.rotation.set(-.18, .12, 0);
  model.add(dish);
  const profile = Array.from({ length: 25 }, (_, i) => {
    const r = i / 24 * .78;
    return new THREE.Vector2(r, .35 * (r / .78) ** 2 - .35);
  });
  const bowl = new THREE.Mesh(new THREE.LatheGeometry(profile, 64).rotateX(Math.PI / 2), reflector);
  bowl.castShadow = bowl.receiveShadow = true;
  dish.add(bowl);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(.78, .014, 8, 64), metal);
  dish.add(rim);
  const antennaStruts: [Position, Position][] = [];
  for (let i = 0; i < 3; i++) {
    const angle = i * Math.PI * 2 / 3 + .3;
    antennaStruts.push([[Math.cos(angle) * .71, Math.sin(angle) * .71, -.015], [0, 0, .58]]);
  }
  struts(antennaStruts, .012, frame, dish);
  const feed = new THREE.Mesh(new THREE.CylinderGeometry(.075, .11, .24, 20).rotateX(Math.PI / 2), gold);
  feed.position.z = .58;
  feed.castShadow = true;
  dish.add(feed);
  const feedCap = new THREE.Mesh(new THREE.CircleGeometry(.077, 20), dark);
  feedCap.position.z = .706;
  dish.add(feedCap);
  struts([[[-.55, .48, .78], [-.24, .25, 1.06]], [[.55, .48, .78], [.24, .25, 1.06]], [[0, -.4, .8], [0, .17, .95]]], .04, metal);

  const auxiliary = new THREE.Mesh(new THREE.LatheGeometry(profile, 32).rotateX(Math.PI / 2), reflector);
  auxiliary.scale.setScalar(.34);
  auxiliary.position.set(-.58, .93, .34);
  auxiliary.rotation.set(-.6, -.55, 0);
  auxiliary.castShadow = auxiliary.receiveShadow = true;
  model.add(auxiliary);
  struts([[[-.58, .64, .2], [-.58, .92, .25]]], .035, frame);

  const tank = new THREE.CapsuleGeometry(.16, .58, 5, 16);
  batch(tank, metal, [matrix([-.99, -.04, -.5], [1, 1, 1]), matrix([.99, -.04, -.5], [1, 1, 1])]);
  const clamps: THREE.Matrix4[] = [];
  for (const x of [-.99, .99]) for (const y of [-.27, .2]) clamps.push(matrix([x, y, -.5], [1, 1, 1], [Math.PI / 2, 0, 0]));
  batch(new THREE.TorusGeometry(.166, .015, 6, 20), dark, clamps);
  block([0, .755, .02], [1.08, .045, .95], radiator);
  const radiatorFins = Array.from({ length: 13 }, (_, i) => matrix([-.48 + i * .08, .792, .02], [.021, .03, .89]));
  batch(box, frame, radiatorFins);

  const engines: THREE.Matrix4[] = [];
  const enginePlates: THREE.Matrix4[] = [];
  for (const x of [-.52, .52]) {
    for (const y of [-.41, .3]) {
      engines.push(matrix([x, y, -1.2], [1, 1, 1], [Math.PI / 2, 0, 0]));
      enginePlates.push(matrix([x, y, -1.02], [.29, .29, .065]));
    }
  }
  batch(box, metal, enginePlates);
  batch(new THREE.CylinderGeometry(.105, .19, .32, 20, 1, true), dark, engines);
  const rcs: THREE.Matrix4[] = [];
  for (const x of [-.8, .8]) for (const z of [-.84, .84]) rcs.push(matrix([x, -.83, z], [1, 1, 1]));
  batch(new THREE.CylinderGeometry(.055, .075, .13, 12, 1, true), frame, rcs);
  batch(cylinder, metal, bolts);

  for (const [position, color] of [[[-.87, .68, -.85], 0xd84435], [[.87, .68, -.85], 0x48be8f], [[0, -.72, .97], 0x8fb9d2]] as [Position, number][]) {
    const lamp = new THREE.Mesh(new THREE.SphereGeometry(.026, 8, 6), new THREE.MeshBasicMaterial({ color }));
    lamp.position.set(...position);
    model.add(lamp);
  }
  for (const side of [-1, 1]) {
    const cable = new THREE.CatmullRomCurve3([
      new THREE.Vector3(side * .76, .49, .62),
      new THREE.Vector3(side * .94, .32, .66),
      new THREE.Vector3(side * .96, -.42, .58),
      new THREE.Vector3(side * .67, -.6, .72),
    ]);
    const harness = new THREE.Mesh(new THREE.TubeGeometry(cable, 20, .019, 6, false), dark);
    model.add(harness);
  }
  model.userData.wings = wings;
  model.userData.dish = dish;
  return model;
}

export function updateCinematicSatellite(group: THREE.Group, time: number) {
  const wings = group.userData.wings as THREE.Group[] | undefined;
  wings?.forEach(wing => { wing.rotation.x = .04 + Math.sin(time * .12) * .018; });
  const dish = group.userData.dish as THREE.Group | undefined;
  if (dish) dish.rotation.y = .12 + Math.sin(time * .08) * .025;
}

export function disposeCinematicSatellite(group: THREE.Group) {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  group.traverse(object => {
    if (!(object instanceof THREE.Mesh)) return;
    if (object instanceof THREE.InstancedMesh) object.dispose();
    geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
    }
  });
  geometries.forEach(geometry => geometry.dispose());
  materials.forEach(material => material.dispose());
  textures.forEach(texture => texture.dispose());
  group.clear();
  group.userData = {};
}
