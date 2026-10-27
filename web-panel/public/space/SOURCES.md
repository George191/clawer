# Space asset sources

Downloaded or copied on 2026-10-27 from the sources below. Files are unchanged from their source downloads or installed package copies except for the texture conversions and downsampling documented below; they are separate from this project's code license.

| Local asset | NASA image | Format and dimensions | Bytes | Source |
| --- | --- | --- | ---: | --- |
| `earth-day.jpg` | Blue Marble: land surface, ocean color and sea ice | JPEG, 2048 × 1024 | 266,599 | [Original JPEG](https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57730/land_ocean_ice_2048.jpg) |
| `earth-clouds.jpg` | Blue Marble: clouds | JPEG, 2048 × 1024 | 829,367 | [Original JPEG](https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57747/cloud_combined_2048.jpg) |

Credit: NASA Goddard Space Flight Center. Images by Reto Stöckli; enhancements by Robert Simmon. Blue Marble is a satellite-observation mosaic, not a current weather image. The cloud image stores cloud brightness in RGB; it has no alpha channel.

These NASA imagery assets are generally not subject to copyright in the United States and are used under NASA's [Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/). NASA's [Earth Science reuse FAQ](https://science.nasa.gov/earth/faq/) permits reuse of its original imagery, including commercial use, unless a separate copyright is indicated. Credit identifies the source; no NASA endorsement is implied.

SHA-256 of the downloaded files:

- `earth-day.jpg`: `d4dc80a6ef571939d0abe04a9bed3d3d1e6cd63e59514be1c5e43a6b069e6f1e`
- `earth-clouds.jpg`: `daddaad84d7a33bbbc86cdda3f591099f57cee8607b7bcf3b67eb7e4f7a1c793`

## High-resolution Earth day texture

- Local file: `earth-day-hd.jpg`, progressive RGB JPEG, 8192 × 4096, 7,505,487 bytes.
- 4K device fallback: `earth-day-4k.jpg`, progressive RGB JPEG, 4096 × 2048, 1,207,382 bytes.
- Same NASA Blue Marble dataset and credits as `earth-day.jpg` above.
- Source: [Original 8192 × 4096 PNG](https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57730/land_ocean_ice_8192.png), 24,147,269 bytes; successfully downloaded and decoded on 2026-10-27. The similarly named official 4096/8192 JPEG URLs returned HTTP 404.
- Processing: the HD file is encoded directly from the original 8192 × 4096 PNG as JPEG at quality 94 with 4:4:4 chroma, optimized progressive encoding, and no resizing. The 4K fallback preserves the earlier original-PNG LANCZOS downsample, encoded at JPEG quality 90. No geographic/content edits. The original 2048-pixel JPEG remains available separately.
- License and reuse: the same [NASA Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/) as the other NASA Blue Marble assets.
- Original PNG SHA-256: `aaddcd967a9f09fb2d7ef50ff452bebfcf10192c520465d5eb1ad8446c716e98`.
- HD JPEG SHA-256: `19916e14f52c80165bbf66f3996b9a3362e0c0ad9e2954b431a531879f017403`.
- 4K JPEG SHA-256: `b8e8200f0c7c3110684b432afb803dd05d5a09b96bc9e117cc892e7c9a59549a`.

## High-resolution clouds and night textures

Creator: Solar System Scope / INOVE, based on NASA imagery and geographic data. Source catalog: [Solar System Scope textures](https://www.solarsystemscope.com/textures/).

| Local file | Format and dimensions | Bytes | Original source |
| --- | --- | ---: | --- |
| `earth-clouds-4k.jpg` | RGB JPEG, 4096 × 2048 | 3,157,035 | [Original 8K clouds JPEG](https://www.solarsystemscope.com/textures/download/8k_earth_clouds.jpg) |
| `earth-night-hd.jpg` | RGB JPEG, 8192 × 4096 | 3,144,468 | [Original 8K night JPEG](https://www.solarsystemscope.com/textures/download/8k_earth_nightmap.jpg) |
| `earth-night-4k.jpg` | RGB JPEG, 4096 × 2048 | 904,764 | Same 8K night source |

The 8K night file preserves the source JPEG bytes unchanged. The 4K versions are direct Pillow LANCZOS downsampling of the original 8K files, encoded as optimized progressive JPEG at quality 92 with 4:4:4 chroma. No geographic/content edits. These are static planetary texture mosaics, not current weather or current city-light measurements. The original 2K fallback assets are retained separately.

The original 8K clouds JPEG (8192 × 4096, 11,619,184 bytes) was downloaded only as an intermediate source for `earth-clouds-4k.jpg`; it is not included in the packaged assets. Original-source SHA-256: `c792eca228989d36ebb45d3ea6ff1198be5e21a25d70d2fbcb2124ffd14ba7f5`.

License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Attribution: Earth cloud and night textures by Solar System Scope, licensed under CC BY 4.0; 4K versions downsampled for this application.

- `earth-clouds-4k.jpg` SHA-256: `13bf369801163b22806eb2a9cabf7a4cc5f80b2e149eefd63188b24da1013012`.
- `earth-night-hd.jpg` SHA-256: `9894e83a585a22c1c425e7ca4f987a9ba625bf08ecee45d3c9dcacae3c2ad5f7`.
- `earth-night-4k.jpg` SHA-256: `f43de93752bb0960897825293d98dc3a7773a832bac2f17be7d1a6397479f1cc`.

## Earth night texture

- Local file: `earth-night.jpg`, JPEG, 2048 × 1024, 255,287 bytes.
- Creator: Solar System Scope / INOVE; based on NASA imagery and geographic data.
- Source: [Solar System Scope textures](https://www.solarsystemscope.com/textures/), [original JPEG](https://www.solarsystemscope.com/textures/download/2k_earth_nightmap.jpg).
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Attribution: Earth night texture by Solar System Scope, licensed under CC BY 4.0. No changes made to the downloaded file.
- SHA-256: `c16fd1bc096ab91a5c5265c6ff9847c43f489f2e2ee790ccdbcbd03251cf3a5a`.

## International Space Station model

- Local file: `iss.glb`, glTF 2.0 binary, 476,992 bytes.
- Source and credit: NASA / Michael D. Carbajal, [International Space Station (ISS) (B)](https://science.nasa.gov/3d-resources/international-space-station-iss-b/).
- Download: [Original NASA GLB](https://assets.science.nasa.gov/content/dam/science/cds/3d/resources/model/international-space-station-%28iss%29-%28b%29/International%20Space%20Station%20%28ISS%29%20%28B%29.glb).
- License and reuse: NASA original 3D material under the [NASA Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/); NASA content is generally not subject to copyright in the United States. Credit identifies the source and does not imply endorsement.
- The original GLB contains 10 meshes, 19 materials and 174,440 triangles, with embedded buffers and no external image or buffer requests. Geometry uses `KHR_draco_mesh_compression`; use the local decoder below.
- SHA-256: `2ba4427413b0dd89a33d2acaf962dea8a96a3b1d8dceda05cc7aad547ffd3d16`.

## Draco decoder

- Local files: `draco/draco_wasm_wrapper.js` (58,456 bytes), `draco/draco_decoder.wasm` (192,420 bytes).
- Copied unchanged from `three@0.186.1`, `examples/jsm/libs/draco/gltf/`. Upstream: [Three.js Draco resources](https://github.com/mrdoob/three.js/tree/r186/examples/jsm/libs/draco/gltf), [Google Draco](https://github.com/google/draco).
- Copyright: The Draco Authors. License: [Apache License 2.0](https://github.com/google/draco/blob/main/LICENSE), with the complete upstream license and bundled notices retained in [`draco/LICENSE`](draco/LICENSE).
- WASM decoder configuration: path `/space/draco/`, type `wasm`. No decoder CDN is required.
- Wrapper SHA-256: `8bb2952d2ba7d67e1414f8df819410cb0434a666be53f671fff75f68843d76f6`.
- WASM SHA-256: `a680d927bed9cb864ddbd63521868891af2bfbe755092761b4837487618df8ac`.
