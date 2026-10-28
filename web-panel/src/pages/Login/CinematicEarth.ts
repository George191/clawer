import * as THREE from 'three';

const vertexShader = `
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    vUv = uv;
    vWorldNormal = normalize(mat3(modelMatrix) * normal);
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldPosition = worldPosition.xyz;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const surfaceShader = `
  uniform sampler2D uDay;
  uniform sampler2D uNight;
  uniform sampler2D uClouds;
  uniform vec3 uSunDirection;
  uniform float uCloudOffset;
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    vec3 normal = normalize(vWorldNormal);
    vec3 sun = normalize(uSunDirection);
    vec3 view = normalize(cameraPosition - vWorldPosition);
    float solarAngle = dot(normal, sun);
    float daylight = smoothstep(-0.06, 0.06, solarAngle);
    float diffuse = 0.014 + 0.986 * pow(max(solarAngle, 0.0), 0.85);
    vec3 day = texture2D(uDay, vUv).rgb;
    float luminance = dot(day, vec3(0.2126, 0.7152, 0.0722));
    vec3 terrain = mix(vec3(luminance), day, 0.85) * vec3(0.78, 0.9, 1.05);
    float cloudShadow = texture2D(uClouds, vec2(vUv.x + uCloudOffset + 0.0015, vUv.y - 0.001)).r;
    vec3 color = terrain * diffuse * (1.0 - cloudShadow * daylight * 0.2);
    float ocean = smoothstep(0.005, 0.075, day.b - day.r) * (1.0 - smoothstep(0.08, 0.35, luminance));
    vec3 halfway = normalize(sun + view);
    float specular = pow(max(dot(normal, halfway), 0.0), 220.0) * ocean * daylight;
    color += vec3(0.08, 0.12, 0.16) * specular;
    vec3 night = max(texture2D(uNight, vUv).rgb - vec3(0.003), vec3(0.0));
    float nightSide = 1.0 - smoothstep(-0.15, 0.08, solarAngle);
    color += night * vec3(2.7, 2.25, 1.65) * nightSide;
    float rim = pow(1.0 - max(dot(normal, view), 0.0), 5.0);
    color += vec3(0.008, 0.038, 0.075) * rim * smoothstep(-0.25, 0.4, solarAngle);
    gl_FragColor = vec4(color, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

const cloudShader = `
  uniform sampler2D uClouds;
  uniform vec3 uSunDirection;
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    float coverage = pow(texture2D(uClouds, vUv).r, 1.2);
    if (coverage < 0.015) discard;
    float sunlight = max(dot(normalize(vWorldNormal), normalize(uSunDirection)), 0.0);
    float illumination = 0.016 + 0.984 * pow(sunlight, 0.8);
    gl_FragColor = vec4(vec3(0.78, 0.86, 0.96) * illumination, coverage * 0.78);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

const atmosphereShader = `
  uniform vec3 uSunDirection;
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    vec3 normal = normalize(vWorldNormal);
    vec3 view = normalize(cameraPosition - vWorldPosition);
    float fresnel = pow(1.0 - max(dot(normal, view), 0.0), 3.6);
    float sunlight = smoothstep(-0.3, 0.7, dot(normal, normalize(uSunDirection)));
    float alpha = fresnel * (0.1 + sunlight * 0.52);
    vec3 color = mix(vec3(0.025, 0.13, 0.29), vec3(0.075, 0.36, 0.7), sunlight);
    gl_FragColor = vec4(color, alpha);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export function createCinematicEarth(
  sunDirection: THREE.Vector3,
  onTextureLoaded: (texture?: THREE.Texture) => void | Promise<void>,
  capabilities?: { maxTextureSize: number; maxAnisotropy: number },
) {
  const group = new THREE.Group();
  group.name = 'Cinematic Earth';
  const geometry = new THREE.SphereGeometry(1, 256, 128);
  const maxTextureSize = capabilities?.maxTextureSize ?? 4096;
  const textureSuffix = maxTextureSize >= 8192 ? '-hd' : maxTextureSize >= 4096 ? '-4k' : '';
  const cloudSuffix = maxTextureSize >= 4096 ? '-4k' : '';
  const anisotropy = Math.max(1, Math.min(capabilities?.maxAnisotropy ?? 1, 16));
  const textures = new Set<THREE.Texture>();
  let disposed = false;
  const black = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
  black.needsUpdate = true;
  const blue = new THREE.DataTexture(new Uint8Array([12, 37, 63, 255]), 1, 1);
  blue.colorSpace = THREE.SRGBColorSpace;
  blue.needsUpdate = true;
  textures.add(black);
  textures.add(blue);
  const sun = { value: sunDirection.clone().normalize() };
  const day = { value: blue as THREE.Texture };
  const night = { value: black as THREE.Texture };
  const clouds = { value: black as THREE.Texture };
  const cloudOffset = { value: 0 };
  const surfaceMaterial = new THREE.ShaderMaterial({
    precision: 'highp',
    vertexShader,
    fragmentShader: surfaceShader,
    uniforms: { uDay: day, uNight: night, uClouds: clouds, uSunDirection: sun, uCloudOffset: cloudOffset },
  });
  const cloudMaterial = new THREE.ShaderMaterial({
    precision: 'highp',
    vertexShader,
    fragmentShader: cloudShader,
    uniforms: { uClouds: clouds, uSunDirection: sun },
    transparent: true,
    depthWrite: false,
  });
  const atmosphereMaterial = new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader: atmosphereShader,
    uniforms: { uSunDirection: sun },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const surface = new THREE.Mesh(geometry, surfaceMaterial);
  const cloudLayer = new THREE.Mesh(geometry, cloudMaterial);
  cloudLayer.scale.setScalar(1.006);
  cloudLayer.renderOrder = 1;
  const atmosphere = new THREE.Mesh(geometry, atmosphereMaterial);
  atmosphere.scale.setScalar(1.02);
  atmosphere.renderOrder = 2;
  group.add(surface, cloudLayer, atmosphere);
  surface.rotation.y = cloudLayer.rotation.y = -.55;

  const loader = new THREE.TextureLoader();
  async function load(url: string, target: { value: THREE.Texture }, color: boolean) {
    let texture: THREE.Texture;
    try {
      texture = await loader.loadAsync(url);
    } catch {
      if (!disposed) await onTextureLoaded();
      return;
    }
    if (disposed) {
      texture.dispose();
      return;
    }
    texture.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    texture.wrapS = THREE.RepeatWrapping;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.magFilter = THREE.LinearFilter;
    texture.generateMipmaps = true;
    texture.anisotropy = anisotropy;
    textures.add(texture);
    try {
      await onTextureLoaded(texture);
      if (!disposed) target.value = texture;
    } catch {
      if (textures.delete(texture)) texture.dispose();
    }
  }
  const ready = Promise.allSettled([
    load(`/space/earth-day${textureSuffix}.jpg`, day, true),
    load(`/space/earth-night${textureSuffix}.jpg`, night, true),
    load(`/space/earth-clouds${cloudSuffix}.jpg`, clouds, false),
  ]).then(() => undefined);

  return {
    group,
    ready,
    update(time: number) {
      if (disposed) return;
      surface.rotation.y = -.55 + time * .008;
      cloudLayer.rotation.y = -.55 + time * .00864;
      cloudOffset.value = -time * .00064 / (Math.PI * 2);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      geometry.dispose();
      surfaceMaterial.dispose();
      cloudMaterial.dispose();
      atmosphereMaterial.dispose();
      textures.forEach(texture => texture.dispose());
      textures.clear();
      group.clear();
    },
  };
}
