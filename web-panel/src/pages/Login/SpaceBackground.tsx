import { useEffect, useRef } from 'react';
import { landPolygons } from './earthLand';
import { createEarthSurface } from './EarthSurface';

type Point = { x: number; y: number; z: number };
type Relay = { position: Point; target: Point; receiving: boolean; scale: number; angle: number; signal: number; opacity: number };

const pulseEnvelope = (phase: number) => Math.sin(Math.PI * phase) ** 2;

const continents = landPolygons.map((polygon) => ({
  polygon,
  west: Math.min(...polygon.map(([lon]) => lon)),
  east: Math.max(...polygon.map(([lon]) => lon)),
  south: Math.min(...polygon.map(([, lat]) => lat)),
  north: Math.max(...polygon.map(([, lat]) => lat)),
}));

function isLand(lon: number, lat: number) {
  return continents.some(({ polygon, west, east, south, north }) => {
    if (lon < west || lon > east || lat < south || lat > north) return false;
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const [xi, yi] = polygon[i];
      const [xj, yj] = polygon[j];
      if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  });
}

function spherePoint(lat: number, lon: number): Point {
  return { x: Math.cos(lat) * Math.sin(lon), y: Math.sin(lat), z: Math.cos(lat) * Math.cos(lon) };
}

const globePoints = Array.from({ length: 9000 }, (_, i) => {
  const lat = Math.asin(1 - 2 * (i + .5) / 9000);
  const lon = (i * Math.PI * (3 - Math.sqrt(5))) % (2 * Math.PI) - Math.PI;
  return { ...spherePoint(lat, lon), land: isLand(lon * 180 / Math.PI, lat * 180 / Math.PI), index: i };
}).filter((point) => point.land || point.index % 5 === 0);

const coastPoints = landPolygons.flatMap((polygon) => polygon.map(([lon, lat]) => spherePoint(lat * Math.PI / 180, lon * Math.PI / 180)));

const stars = Array.from({ length: 850 }, (_, i) => ({
  x: ((i * 127.13 + 19) % 997) / 997,
  y: ((i * 73.79 + 41) % 991) / 991,
  radius: i % 9 === 0 ? 1.15 : .45 + (i % 3) * .16,
}));

const dust = Array.from({ length: 340 }, (_, i) => {
  const angle = i * Math.PI * (3 - Math.sqrt(5));
  const distance = Math.sqrt(((i * 137.31 + 29) % 997) / 997);
  return { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance, depth: 1 - distance };
});

export default function SpaceBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const satelliteVeilRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    const veilCanvas = satelliteVeilRef.current;
    const veilContext = veilCanvas?.getContext('2d');
    if (!canvas || !context || !veilCanvas || !veilContext) return;
    const ctx = context;
    const veilCtx = veilContext;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const compact = window.matchMedia('(max-width: 960px)');
    let width = 0;
    let height = 0;
    let frame = 0;
    let elapsed = 0;
    let lastTime = 0;
    let headingAnchor: { x: number; y: number } | undefined;
    let anchorTime = 0;
    let earthSurface: ReturnType<typeof createEarthSurface> | undefined;

    function render(time: number) {
      ctx.clearRect(0, 0, width, height);
      veilCtx.clearRect(0, 0, width, height);
      if (compact.matches || !width || !height) return;
      const radius = Math.min(width * .15, height * .21, 230);
      const cx = width * .28 + Math.max(0, Math.min(20, (1280 - width) * .08));
      const cy = height * .36;
      const rotation = -.55 + time * .035;
      const cos = Math.cos(rotation);
      const sin = Math.sin(rotation);

      function project(point: Point) {
        const x = point.x * cos + point.z * sin;
        const z = point.z * cos - point.x * sin;
        return { x: cx + x * radius, y: cy - (point.y * .98 - z * .2) * radius, z: point.y * .2 + z * .98 };
      }

      function circle(x: number, y: number, r: number, color: string | CanvasGradient, target = ctx) {
        target.beginPath();
        target.arc(x, y, r, 0, Math.PI * 2);
        target.fillStyle = color;
        target.fill();
      }

      function nebula(x: number, y: number, rx: number, ry: number, color: string, opacity = .085) {
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(-.32);
        ctx.scale(rx, ry);
        const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
        glow.addColorStop(0, `rgba(${color},${opacity})`);
        glow.addColorStop(.45, `rgba(${color},${opacity * .47})`);
        glow.addColorStop(1, `rgba(${color},0)`);
        circle(0, 0, 1, glow);
        ctx.restore();
      }

      nebula(cx + radius * .8, cy - radius * .35, radius * 2.6, radius * 1.2, '46,126,168');
      nebula(cx - radius * .75, cy + radius * .65, radius * 1.8, radius * 1.1, '40,118,143');
      nebula(width * .57, height * .35, width * .32, height * .44, '39,81,126');
      nebula(width * .84, height * .42, width * .38, height * .7, '42,112,153', .13);

      const starCount = Math.min(stars.length, Math.max(320, Math.round(width * height / 4000)));
      for (let i = 0; i < starCount; i++) {
        const star = stars[i];
        const x = (star.x * width + Math.sin(time * .025 + i) * 4) % width;
        const y = star.y * height;
        const quietArea = x > width * .6 || y > height * .6;
        const alpha = (.28 + .15 * Math.sin(time * .5 + i)) * (quietArea ? .5 : 1);
        circle(x, y, star.radius, `rgba(171,211,239,${alpha})`);
        if (i % 29 === 0 && !quietArea) {
          const glow = ctx.createRadialGradient(x, y, 0, x, y, 7);
          glow.addColorStop(0, `rgba(146,222,250,${alpha * .3})`);
          glow.addColorStop(1, 'rgba(146,222,250,0)');
          circle(x, y, 7, glow);
        }
      }

      for (let i = 0; i < dust.length; i++) {
        const particle = dust[i];
        const x = cx + radius * (.55 + particle.x * 2.55) + Math.sin(time * .04 + i) * 5;
        const y = cy + radius * (particle.y * 1.4 - .22) + Math.cos(time * .035 + i) * 3;
        const quietArea = x > width * .56 || y > height * .58;
        const alpha = (.07 + particle.depth * .2) * (quietArea ? .25 : 1);
        circle(x, y, i % 13 === 0 ? 1.25 : .55, `rgba(104,195,222,${alpha})`);
        const farX = width * (.65 + particle.x * .42) + Math.sin(time * .03 + i) * 4;
        const farY = height * (.43 + particle.y * .53);
        circle(farX, farY, i % 17 === 0 ? 1.2 : .65, `rgba(123,202,231,${.05 + particle.depth * .16})`);
      }

      if (width >= 1100) {
        const clusterX = width * .53;
        const clusterY = height * .23;
        const spread = Math.min(width * .065, 130);
        nebula(clusterX, clusterY, spread * 1.6, spread, '76,114,163');
        for (let i = 0; i < dust.length; i++) {
          const particle = dust[i];
          const x = clusterX + particle.x * spread * 1.25 + Math.sin(time * .025 + i) * 2;
          const y = clusterY + particle.y * spread * .7;
          const alpha = .05 + particle.depth * .3;
          circle(x, y, i % 23 === 0 ? 1.15 : .55, `rgba(150,195,232,${alpha})`);
        }
      }

      const atmosphere = ctx.createRadialGradient(cx, cy, radius * .85, cx, cy, radius * 2.2);
      atmosphere.addColorStop(0, 'rgba(22,116,166,.02)');
      atmosphere.addColorStop(.35, 'rgba(35,151,202,.075)');
      atmosphere.addColorStop(1, 'rgba(13,76,113,0)');
      circle(cx, cy, radius * 2.2, atmosphere);

      function perspective(point: Point) {
        return 5.2 / (5.2 - point.z);
      }

      function orbitPoint(angle: number, surface = false, tilt = -.85): Point {
        const x = Math.cos(angle) * (surface ? .5 : 1.68);
        const y = Math.sin(angle) * (surface ? .15 : .55);
        const z = Math.sin(angle) * (surface ? 1.8 : 1.58);
        const length = surface ? Math.hypot(x, y, z) : 1;
        const depth = z / length;
        const view = surface ? 1 : perspective({ x: 0, y: 0, z: depth });
        return {
          x: cx + (x * Math.cos(tilt) - y * Math.sin(tilt)) / length * radius * view,
          y: cy + (x * Math.sin(tilt) + y * Math.cos(tilt)) / length * radius * view,
          z: depth,
        };
      }

      function orbitalRelay(angle: number, tilt: number, receiving: boolean, signal: number): Relay {
        const position = orbitPoint(angle, false, tilt);
        const ahead = orbitPoint(angle + .002, false, tilt);
        const behind = orbitPoint(angle - .002, false, tilt);
        return {
          position,
          target: orbitPoint(angle, true, tilt),
          receiving,
          scale: (receiving ? .65 : .9) * perspective(position),
          angle: Math.atan2(ahead.y - behind.y, ahead.x - behind.x) + Math.PI * .5 + Math.sin(angle * 2) * .06,
          signal,
          opacity: receiving ? .9 : 1,
        };
      }

      const orbit = .7 + time * Math.PI * 2 / 72;
      const communication = (time / 6) % 1;
      const sendSignal = communication < .5 ? pulseEnvelope(communication * 2) : 0;
      const receiveSignal = communication >= .5 ? pulseEnvelope((communication - .5) * 2) : 0;
      const relays: Relay[] = [
        orbitalRelay(orbit, -.85, false, sendSignal),
        orbitalRelay(orbit + Math.PI, .48, true, receiveSignal),
      ];
      const orbitPaths: Array<(phase: number) => Point> = [
        phase => orbitPoint(phase),
        phase => orbitPoint(phase, false, .48),
      ];

      if (width >= 1280 && height >= 600) {
        const heading = canvas?.parentElement?.querySelector('.sp-login-form-wrap h2');
        const headingBox = heading?.getBoundingClientRect();
        const canvasBox = canvas?.getBoundingClientRect();
        const desired = headingBox && canvasBox
          ? { x: headingBox.left - canvasBox.left + headingBox.width * .3, y: headingBox.top - canvasBox.top + headingBox.height * .5 }
          : { x: width * .73, y: height * .28 };
        if (!headingAnchor || motion.matches) headingAnchor = desired;
        else {
          const follow = 1 - Math.exp(-Math.max(0, Math.min(.1, time - anchorTime)) * 4);
          headingAnchor.x += (desired.x - headingAnchor.x) * follow;
          headingAnchor.y += (desired.y - headingAnchor.y) * follow;
        }
        anchorTime = time;
        const end = headingAnchor;
        const start = { x: width * .25, y: height * .75 };
        const center = { x: (start.x + end.x) * .5, y: (start.y + end.y) * .5 };
        const halfY = (end.y - start.y) * .5;
        const verticalRoom = Math.max(0, Math.min(center.y - 52, height - center.y - 52));
        const bow = Math.min(height * .44, Math.sqrt(Math.max(0, verticalRoom ** 2 - halfY ** 2)));
        const wideOrbit = (phase: number): Point => ({
          x: center.x - (end.x - start.x) * .5 * Math.cos(phase) - width * .18 * Math.sin(phase),
          y: center.y - (end.y - start.y) * .5 * Math.cos(phase) - bow * Math.sin(phase),
          z: Math.sin(phase),
        });
        orbitPaths.push(wideOrbit);
        const phase = time * Math.PI * 2 / 48;
        const position = wideOrbit(phase);
        const vx = (end.x - start.x) * .5 * Math.sin(phase) - width * .18 * Math.cos(phase);
        const vy = halfY * Math.sin(phase) - bow * Math.cos(phase);
        relays.push({ position, target: orbitPoint(phase, true), receiving: false, scale: .58 * perspective(position), angle: Math.atan2(vy, vx) + Math.PI * .5 + Math.sin(phase * 2) * .06, signal: .3 + sendSignal * .4, opacity: .45 });
      }

      function drawOrbits(front: boolean) {
        ctx.save();
        ctx.lineWidth = .65;
        for (let index = 0; index < orbitPaths.length; index++) {
          ctx.beginPath();
          let penDown = false;
          for (let step = 0; step <= 192; step++) {
            const point = orbitPaths[index](step / 192 * Math.PI * 2);
            if (Math.abs(point.z) < 1e-6 || (point.z >= 0) === front) {
              if (penDown) ctx.lineTo(point.x, point.y); else ctx.moveTo(point.x, point.y);
              penDown = true;
            } else penDown = false;
          }
          ctx.strokeStyle = `rgba(133,184,210,${(front ? .2 : .085) * (index === 2 ? .4 : 1)})`;
          ctx.stroke();
        }
        ctx.restore();
      }

      drawOrbits(false);
      for (const relay of relays) if (relay.position.z < 0) drawRelay(relay);

      const surface = ctx.createRadialGradient(cx - radius * .4, cy - radius * .45, 0, cx, cy, radius);
      surface.addColorStop(0, '#102c40');
      surface.addColorStop(.65, '#091b2b');
      surface.addColorStop(1, '#06121e');
      circle(cx, cy, radius, surface);

      const textured = earthSurface?.draw(ctx, cx, cy, radius, rotation) ?? false;
      if (!textured) {
        // Only the visible hemisphere is drawn, including the latitude/longitude grid.
        ctx.strokeStyle = 'rgba(86,169,203,.115)';
        ctx.lineWidth = .65;
        for (let axis = 0; axis < 2; axis++) {
          const count = axis === 0 ? 7 : 12;
          for (let line = 0; line < count; line++) {
            ctx.beginPath();
            let visible = false;
            for (let step = 0; step <= 96; step++) {
              const lat = axis === 0 ? (line - 3) * Math.PI / 12 : -Math.PI / 2 + step / 96 * Math.PI;
              const lon = axis === 0 ? step / 96 * Math.PI * 2 : line / count * Math.PI * 2;
              const p = project(spherePoint(lat, lon));
              if (p.z > 0) {
                if (visible) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y);
                visible = true;
              } else visible = false;
            }
            ctx.stroke();
          }
        }

        for (const point of globePoints) {
          const p = project(point);
          if (p.z <= 0) continue;
          const alpha = (point.land ? .45 : .1) + p.z * (point.land ? .47 : .12);
          circle(p.x, p.y, (point.land ? 1.05 : .6) * Math.max(.7, radius / 190), `rgba(${point.land ? '122,211,232' : '68,144,184'},${alpha})`);
        }
        for (const point of coastPoints) {
          const p = project(point);
          if (p.z <= 0) continue;
          circle(p.x, p.y, .65 * Math.max(.7, radius / 190), `rgba(160,229,240,${.25 + p.z * .55})`);
        }
      }
      ctx.save();
      const rim = ctx.createLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius);
      rim.addColorStop(0, 'rgba(179,219,255,.8)');
      rim.addColorStop(.5, 'rgba(69,143,218,.32)');
      rim.addColorStop(1, 'rgba(48,106,164,.035)');
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.003, 0, Math.PI * 2);
      ctx.strokeStyle = rim;
      ctx.lineWidth = 1.4;
      ctx.shadowColor = 'rgba(79,160,239,.55)';
      ctx.shadowBlur = radius * .045;
      ctx.stroke();
      ctx.restore();

      function drawRelay({ position, target, receiving, scale, angle, signal, opacity }: Relay) {
        ctx.save();
        const relayAlpha = opacity * (.65 + .35 * Math.max(0, Math.min(1, (position.z + 1.58) / 3.16)));
        const horizonFade = pulseEnvelope(Math.min(1, Math.abs(target.z) * 5) * .5);
        ctx.globalAlpha = relayAlpha * horizonFade;
        const color = receiving ? '123,246,204' : '117,205,247';
        const distance = Math.hypot(target.x - position.x, target.y - position.y) || 1;
        const sideX = -(target.y - position.y) / distance * radius * .2;
        const sideY = (target.x - position.x) / distance * radius * .2;
        const beam = ctx.createLinearGradient(position.x, position.y, target.x, target.y);
        beam.addColorStop(0, `rgba(${color},${.13 + signal * .12})`);
        beam.addColorStop(1, `rgba(${color},.015)`);
        ctx.beginPath();
        ctx.moveTo(position.x, position.y);
        ctx.lineTo(target.x + sideX, target.y + sideY);
        ctx.quadraticCurveTo(target.x, target.y, target.x - sideX, target.y - sideY);
        ctx.closePath();
        ctx.fillStyle = beam;
        ctx.fill();

        for (let i = 0; i < 3; i++) {
          const phase = (time * .38 + i / 3) % 1;
          ctx.beginPath();
          ctx.ellipse(target.x, target.y, radius * (.04 + phase * .23), radius * (.02 + phase * .075), -.35, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${color},${pulseEnvelope(phase) * .4})`;
          ctx.lineWidth = .7;
          ctx.stroke();
        }
        circle(target.x, target.y, 1.7, `rgba(${color},.85)`);

        const start = receiving ? target : position;
        const end = receiving ? position : target;
        for (let i = 0; i < 6; i++) {
          const progress = (time * .44 + i / 6) % 1;
          const envelope = pulseEnvelope(progress) * signal;
          for (let trail = 0; trail < 3; trail++) {
            const step = progress - trail * .025;
            if (step < 0) continue;
            const x = start.x + (end.x - start.x) * step;
            const y = start.y + (end.y - start.y) * step;
            const alpha = envelope * pulseEnvelope(step) * (.95 - trail * .25);
            circle(x, y, trail === 0 ? 1.8 : 1, `rgba(${color},${alpha})`);
          }
        }
        ctx.globalAlpha = relayAlpha;
        drawSatellite(ctx, position.x, position.y, scale, angle, receiving, signal, position.z);
        ctx.restore();
      }

      function drawSatellite(ctx: CanvasRenderingContext2D, x: number, y: number, scale: number, angle: number, receiving: boolean, signal: number, depth: number) {
        ctx.save();
        ctx.translate(x, y);
        ctx.scale(scale, scale);
        if (receiving && signal > 0) {
          for (let echo = 0; echo < 2; echo++) {
            const pulse = (time / 1.8 + echo * .5) % 1;
            const growth = .04 + (1 - (1 - pulse) ** 2) * 1.46;
            const alpha = pulseEnvelope(pulse) * signal;
            ctx.save();
            ctx.scale(growth, growth);
            const shield = ctx.createRadialGradient(0, 0, 5, 0, 0, 39);
            shield.addColorStop(0, 'rgba(93,226,200,0)');
            shield.addColorStop(1, `rgba(93,226,200,${alpha * .1})`);
            ctx.beginPath();
            ctx.moveTo(0, -39);
            ctx.bezierCurveTo(11, -31, 24, -31, 33, -29);
            ctx.lineTo(31, 4);
            ctx.bezierCurveTo(29, 20, 13, 30, 0, 38);
            ctx.bezierCurveTo(-13, 30, -29, 20, -31, 4);
            ctx.lineTo(-33, -29);
            ctx.bezierCurveTo(-24, -31, -11, -31, 0, -39);
            ctx.fillStyle = shield;
            ctx.fill();
            ctx.strokeStyle = `rgba(116,255,213,${alpha * .7})`;
            ctx.lineWidth = .9;
            ctx.stroke();
            ctx.restore();
          }
        }
        ctx.save();
        const signalAlpha = ctx.globalAlpha * signal;
        const search = (time / 1.5) % 1;
        const radioColor = receiving ? '#9bffda' : '#9ddfff';
        ctx.globalAlpha = signalAlpha * (.45 + pulseEnvelope(search) * .55);
        circle(28, -23, 1.8, radioColor, ctx);
        ctx.strokeStyle = radioColor;
        ctx.lineCap = 'round';
        ctx.shadowColor = radioColor;
        for (let i = 0; i < 3; i++) {
          const phase = search * 1.8 - i * .3;
          const wave = phase > 0 && phase < 1 ? pulseEnvelope(phase) : 0;
          ctx.globalAlpha = signalAlpha * (.1 + wave * .9);
          ctx.lineWidth = 1.35 + wave * .6;
          ctx.shadowBlur = wave * 4;
          ctx.beginPath();
          ctx.arc(28, -23, 6 + i * 5, -Math.PI * .8, -Math.PI * .2);
          ctx.stroke();
        }
        ctx.restore();
        ctx.rotate(angle);
        const span = 16 * (.6 + .4 * Math.min(1, Math.abs(depth) / 1.58));
        const panels = ctx.createLinearGradient(-26, -8, 26, 8);
        panels.addColorStop(0, '#071827');
        panels.addColorStop(.45, '#153d58');
        panels.addColorStop(1, '#081d30');
        ctx.fillStyle = panels;
        ctx.strokeStyle = '#608598';
        ctx.lineWidth = .7;
        for (const side of [-1, 1]) {
          const start = side < 0 ? -10 - span : 10;
          ctx.fillRect(start, -8, span, 16);
          ctx.strokeRect(start, -8, span, 16);
          ctx.beginPath();
          for (let cell = 1; cell < 4; cell++) {
            ctx.moveTo(start + span * cell / 4, -8); ctx.lineTo(start + span * cell / 4, 8);
          }
          ctx.moveTo(start, 0); ctx.lineTo(start + span, 0);
          ctx.moveTo(side * 6, 0); ctx.lineTo(side * 10, 0);
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.moveTo(6, -10); ctx.lineTo(10, -6); ctx.lineTo(10, 14); ctx.lineTo(6, 10); ctx.closePath();
        ctx.fillStyle = '#536e7c';
        ctx.fill();
        const metal = ctx.createLinearGradient(-6, -10, 6, 10);
        metal.addColorStop(0, '#e0d5ab');
        metal.addColorStop(.45, '#acb4af');
        metal.addColorStop(1, '#627985');
        ctx.fillStyle = metal;
        ctx.fillRect(-6, -10, 12, 20);
        ctx.strokeStyle = '#d5dedb';
        ctx.strokeRect(-6, -10, 12, 20);
        ctx.beginPath();
        ctx.moveTo(0, -10); ctx.lineTo(0, -17);
        ctx.moveTo(-4, 15); ctx.quadraticCurveTo(0, 20, 4, 15);
        ctx.stroke();
        circle(0, -18, 1.4, '#f4d497', ctx);
        ctx.restore();
      }

      drawOrbits(true);
      for (const relay of relays) if (relay.position.z >= 0) drawRelay(relay);

      const panelBox = canvas?.parentElement?.querySelector('.sp-login-form-wrap')?.getBoundingClientRect();
      const canvasBox = canvas?.getBoundingClientRect();
      if (panelBox && canvasBox) {
        veilCtx.save();
        veilCtx.beginPath();
        veilCtx.rect(panelBox.left - canvasBox.left, panelBox.top - canvasBox.top, panelBox.width, panelBox.height);
        veilCtx.clip();
        veilCtx.filter = 'blur(2px)';
        for (const relay of relays) {
          veilCtx.globalAlpha = Math.min(.34, relay.opacity * .8);
          drawSatellite(veilCtx, relay.position.x, relay.position.y, relay.scale, relay.angle, relay.receiving, relay.signal, relay.position.z);
        }
        veilCtx.restore();
      }
    }

    function animate(now: number) {
      if (now - lastTime >= 1000 / 30) {
        elapsed += Math.min((now - lastTime) / 1000, .1);
        lastTime = now;
        render(elapsed);
      }
      frame = requestAnimationFrame(animate);
    }

    function resume() {
      cancelAnimationFrame(frame);
      if (!compact.matches && !earthSurface) {
        earthSurface = createEarthSurface('/space/earth-day.jpg', resume, '/space/earth-clouds.jpg');
      }
      render(motion.matches ? 0 : elapsed);
      if (!compact.matches && !motion.matches && !document.hidden) {
        lastTime = performance.now();
        frame = requestAnimationFrame(animate);
      }
    }

    function resize() {
      if (!canvas || !veilCanvas) return;
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      headingAnchor = undefined;
      anchorTime = 0;
      const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      veilCanvas.width = canvas.width;
      veilCanvas.height = canvas.height;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      veilCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      resume();
    }

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    motion.addEventListener('change', resume);
    compact.addEventListener('change', resize);
    document.addEventListener('visibilitychange', resume);
    resize();
    return () => {
      cancelAnimationFrame(frame);
      earthSurface?.dispose();
      observer.disconnect();
      motion.removeEventListener('change', resume);
      compact.removeEventListener('change', resize);
      document.removeEventListener('visibilitychange', resume);
    };
  }, []);

  return <><canvas ref={canvasRef} className="sp-login-space" aria-hidden="true" /><canvas ref={satelliteVeilRef} className="sp-login-space sp-login-satellite-veil" aria-hidden="true" /></>;
}
