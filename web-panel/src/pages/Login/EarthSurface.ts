const SIZE = 384;
const TAU = Math.PI * 2;

export function createEarthSurface(url: string, onLoad: () => void, cloudUrl?: string) {
  const surface = document.createElement('canvas');
  surface.width = surface.height = SIZE;
  const context = surface.getContext('2d');
  let output = context?.createImageData(SIZE, SIZE) ?? null;
  let texture: Uint8ClampedArray | null = null;
  let clouds: Uint8ClampedArray | null = null;
  let image: HTMLImageElement | null = new Image();
  let cloudImage: HTMLImageElement | null = cloudUrl ? new Image() : null;
  let cloudsLoaded = false;
  let textureWidth = 0;
  let textureHeight = 0;
  let disposed = false;
  let count = 0;
  let columns = new Float32Array(SIZE * SIZE);
  let rows = new Uint32Array(SIZE * SIZE);
  let offsets = new Uint32Array(SIZE * SIZE);
  let lighting = new Float32Array(SIZE * SIZE);
  let atmosphere = new Float32Array(SIZE * SIZE);

  function prepareClouds() {
    if (disposed || !cloudImage || !cloudsLoaded || !texture) return false;
    const source = document.createElement('canvas');
    source.width = textureWidth;
    source.height = textureHeight;
    const sourceContext = source.getContext('2d', { willReadFrequently: true });
    if (!sourceContext) return false;
    sourceContext.drawImage(cloudImage, 0, 0, source.width, source.height);
    clouds = sourceContext.getImageData(0, 0, source.width, source.height).data;
    cloudImage.onload = cloudImage.onerror = null;
    cloudImage = null;
    source.width = source.height = 0;
    return true;
  }

  image.onload = () => {
    if (disposed || !image || !output || !context) return;
    const source = document.createElement('canvas');
    textureWidth = Math.min(image.naturalWidth, 2048);
    source.width = textureWidth;
    source.height = textureHeight = Math.round(image.naturalHeight * textureWidth / image.naturalWidth);
    const sourceContext = source.getContext('2d', { willReadFrequently: true });
    if (!sourceContext) return;
    sourceContext.drawImage(image, 0, 0, source.width, source.height);
    texture = sourceContext.getImageData(0, 0, source.width, source.height).data;

    // Invert the globe's camera tilt once; longitude alone changes during rotation.
    const tilt = Math.hypot(.98, .2);
    const cosTilt = .98 / tilt;
    const sinTilt = .2 / tilt;
    for (let y = 0; y < SIZE; y++) {
      const up = 1 - (y + .5) * 2 / SIZE;
      for (let x = 0; x < SIZE; x++) {
        const right = (x + .5) * 2 / SIZE - 1;
        const distance = right * right + up * up;
        if (distance >= 1) continue;
        const front = Math.sqrt(1 - distance);
        const worldY = up * cosTilt + front * sinTilt;
        const worldZ = front * cosTilt - up * sinTilt;
        columns[count] = (Math.atan2(right, worldZ) / TAU + .5) * textureWidth;
        const row = Math.min(source.height - 1, Math.floor((.5 - Math.asin(worldY) / Math.PI) * source.height));
        rows[count] = row * textureWidth * 4;
        offsets[count] = (y * SIZE + x) * 4;
        const sunlight = Math.max(0, -.73 * right + .42 * up + .54 * front);
        lighting[count] = .025 + .975 * Math.pow(sunlight, .65);
        atmosphere[count] = Math.pow(1 - front, 3) * (.15 + .85 * Math.sqrt(sunlight));
        output.data[offsets[count] + 3] = 255 * Math.min(1, (1 - Math.sqrt(distance)) * SIZE / 2);
        count++;
      }
    }

    image.onload = image.onerror = null;
    image = null;
    source.width = source.height = 0;
    prepareClouds();
    onLoad();
  };
  image.onerror = () => {
    if (image) image.onload = image.onerror = null;
    image = null;
  };
  image.src = url;
  if (cloudImage && cloudUrl) {
    cloudImage.onload = () => {
      cloudsLoaded = true;
      if (prepareClouds()) onLoad();
    };
    cloudImage.onerror = () => {
      if (cloudImage) cloudImage.onload = cloudImage.onerror = null;
      cloudImage = null;
    };
    cloudImage.src = cloudUrl;
  }

  return {
    draw(ctx: CanvasRenderingContext2D, cx: number, cy: number, radius: number, rotation: number): boolean {
      if (disposed || !texture || !output || !context) return false;
      const shift = ((rotation / TAU * textureWidth) % textureWidth + textureWidth) % textureWidth;
      const cloudShift = ((rotation * 1.03 / TAU * textureWidth) % textureWidth + textureWidth) % textureWidth;
      const pixels = output.data;
      for (let i = 0; i < count; i++) {
        let column = columns[i] - shift;
        if (column < 0) column += textureWidth;
        const sample = rows[i] + Math.floor(column) * 4;
        const target = offsets[i];
        const light = lighting[i];
        const haze = atmosphere[i];
        let coverage = 0;
        if (clouds) {
          let cloudColumn = columns[i] - cloudShift;
          if (cloudColumn < 0) cloudColumn += textureWidth;
          coverage = clouds[rows[i] + Math.floor(cloudColumn) * 4] / 255 * .9;
        }
        const ground = (1 - coverage) * (1 - coverage * .13);
        pixels[target] = (texture[sample] * ground + coverage * 235) * light + haze * 13;
        pixels[target + 1] = (texture[sample + 1] * ground + coverage * 242) * light + haze * 50;
        pixels[target + 2] = (texture[sample + 2] * ground + coverage * 250) * light + haze * 90;
      }
      context.putImageData(output, 0, 0);
      ctx.drawImage(surface, cx - radius, cy - radius, radius * 2, radius * 2);
      return true;
    },
    dispose() {
      disposed = true;
      if (image) {
        image.onload = image.onerror = null;
        image.removeAttribute('src');
        image = null;
      }
      if (cloudImage) {
        cloudImage.onload = cloudImage.onerror = null;
        cloudImage.removeAttribute('src');
        cloudImage = null;
      }
      texture = clouds = output = null;
      columns = new Float32Array(0);
      rows = offsets = new Uint32Array(0);
      lighting = atmosphere = new Float32Array(0);
      surface.width = surface.height = 0;
    },
  };
}
