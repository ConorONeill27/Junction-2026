/**
 * Browser-side Camera-to-3D RGB + Depth Backprojector & Raycaster.
 * Converts 2D video/photo frames + wearer pose (x, y, z, yaw, pitch)
 * into dense colored 3D world points, live thermal depth previews, and
 * tap-to-3D object coordinates in WorldGit's -Z forward convention.
 */

export function computeDepthMap(imgData, w, h) {
  const luma = new Float32Array(w * h);
  const sat = new Float32Array(w * h);

  for (let i = 0; i < w * h; i++) {
    const idx = i * 4;
    const r = imgData[idx] / 255.0;
    const g = imgData[idx + 1] / 255.0;
    const b = imgData[idx + 2] / 255.0;
    luma[i] = 0.299 * r + 0.587 * g + 0.114 * b;
    const maxC = Math.max(r, g, b);
    const minC = Math.min(r, g, b);
    sat[i] = maxC > 0.02 ? (maxC - minC) / maxC : 0;
  }

  // Pass 1: Perspective floor/wall prior + high-frequency texture/saturation foreground pop
  const rawDepth = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const vNorm = y / Math.max(1, h - 1); // 0 = top, 1 = bottom
    const perspPrior = 3.9 - vNorm * 2.25; // lower in frame = closer table/floor
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      const right = x + 1 < w ? luma[i + 1] : luma[i];
      const down = y + 1 < h ? luma[i + w] : luma[i];
      const grad = Math.min(1.0, (Math.abs(luma[i] - right) + Math.abs(luma[i] - down)) * 5.0);
      // Saturated, textured objects in the foreground pull closer than flat background walls
      const fgPull = grad * 0.75 + sat[i] * 0.55;
      const d = perspPrior - fgPull + (luma[i] - 0.5) * 0.35;
      rawDepth[i] = Math.max(0.65, Math.min(4.6, d));
    }
  }

  // Pass 2: Edge-preserving 5x5 bilateral-style smoothing so surfaces stay planar & crisp
  const smooth = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const centerIdx = y * w + x;
      const centerLuma = luma[centerIdx];
      let sum = 0;
      let wSum = 0;
      for (let dy = -2; dy <= 2; dy++) {
        const ny = y + dy;
        if (ny < 0 || ny >= h) continue;
        for (let dx = -2; dx <= 2; dx++) {
          const nx = x + dx;
          if (nx < 0 || nx >= w) continue;
          const nIdx = ny * w + nx;
          const lumaDiff = Math.abs(centerLuma - luma[nIdx]);
          const weight = lumaDiff < 0.12 ? 1.0 : 0.15;
          sum += rawDepth[nIdx] * weight;
          wSum += weight;
        }
      }
      smooth[centerIdx] = sum / Math.max(0.001, wSum);
    }
  }

  return smooth;
}

export function renderDepthPreview(depth, w, h, targetCanvas) {
  if (!targetCanvas) return;
  targetCanvas.width = w;
  targetCanvas.height = h;
  const ctx = targetCanvas.getContext("2d");
  if (!ctx) return;

  const out = ctx.createImageData(w, h);
  const data = out.data;
  for (let i = 0; i < w * h; i++) {
    // Near (0.65m) -> 1.0 (hot orange/yellow), Far (4.6m) -> 0.0 (deep blue/purple)
    const norm = 1.0 - Math.max(0, Math.min(1, (depth[i] - 0.65) / 3.8));
    const idx = i * 4;
    data[idx] = Math.round(Math.min(255, norm * 320));
    data[idx + 1] = Math.round(Math.sin(norm * Math.PI) * 210);
    data[idx + 2] = Math.round((1.0 - norm) * 240);
    data[idx + 3] = 255;
  }
  ctx.putImageData(out, 0, 0);
}

export function unprojectCamPointToWorld(u, v, zCam, w, h, pose, yawDeg, pitchDeg) {
  const fx = w * 0.85;
  const fy = w * 0.85;
  const cx = w * 0.5;
  const cy = h * 0.5;

  const xCam = ((u - cx) * zCam) / fx;
  const yCam = -((v - cy) * zCam) / fy;

  const yawRad = (yawDeg * Math.PI) / 180;
  const pitchRad = (pitchDeg * Math.PI) / 180;
  const cosY = Math.cos(yawRad);
  const sinY = Math.sin(yawRad);
  const cosP = Math.cos(pitchRad);
  const sinP = Math.sin(pitchRad);

  const yPitch = yCam * cosP + zCam * sinP;
  const zPitch = -yCam * sinP + zCam * cosP;

  const xWorld = pose[0] + xCam * cosY + zPitch * sinY;
  const yWorld = Math.max(0.05, pose[1] + yPitch);
  const zWorld = pose[2] + xCam * sinY - zPitch * cosY;

  return [
    Number(xWorld.toFixed(3)),
    Number(yWorld.toFixed(3)),
    Number(zWorld.toFixed(3)),
  ];
}

export function projectCanvasTo3D(
  canvas,
  pose = [0, 1.5, 0],
  yawDeg = 0,
  pitchDeg = 0,
  depthPreviewCanvas = null
) {
  const w = canvas.width;
  const h = canvas.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx || w === 0 || h === 0) return [];

  const img = ctx.getImageData(0, 0, w, h).data;
  const depth = computeDepthMap(img, w, h);

  if (depthPreviewCanvas) {
    renderDepthPreview(depth, w, h, depthPreviewCanvas);
  }

  const step = Math.max(2, Math.floor(w / 64)); // ~3,000 points per frame at 192x144
  const points = [];

  for (let v = 0; v < h; v += step) {
    for (let u = 0; u < w; u += step) {
      const i = v * w + u;
      const [xw, yw, zw] = unprojectCamPointToWorld(
        u,
        v,
        depth[i],
        w,
        h,
        pose,
        yawDeg,
        pitchDeg
      );

      const idx = i * 4;
      const r = Number((img[idx] / 255).toFixed(2));
      const g = Number((img[idx + 1] / 255).toFixed(2));
      const b = Number((img[idx + 2] / 255).toFixed(2));

      points.push([xw, yw, zw, r, g, b]);
    }
  }

  return points;
}

export function unprojectTapTo3D(uNorm, vNorm, canvas, pose = [0, 1.5, 0], yawDeg = 0, pitchDeg = 0) {
  const w = canvas.width;
  const h = canvas.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx || w === 0 || h === 0) return { position: [pose[0], pose[1], pose[2] - 1.5], depthM: 1.5 };

  const img = ctx.getImageData(0, 0, w, h).data;
  const depth = computeDepthMap(img, w, h);
  const u = Math.max(0, Math.min(w - 1, Math.round(uNorm * (w - 1))));
  const v = Math.max(0, Math.min(h - 1, Math.round(vNorm * (h - 1))));
  const zCam = depth[v * w + u];
  const position = unprojectCamPointToWorld(u, v, zCam, w, h, pose, yawDeg, pitchDeg);
  return { position, depthM: Number(zCam.toFixed(2)) };
}

export function drawImageToCanvas(imgOrVideo, canvas, targetW = 192, targetH = 144) {
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(imgOrVideo, 0, 0, targetW, targetH);
}
