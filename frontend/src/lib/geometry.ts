import type { Coordinate, WallSegment } from "@/types/floorPlan";

/** Convert meters to canvas pixels. */
export function metersToCanvas(
  coord: Coordinate,
  scale: number,
  offset: { x: number; y: number },
): { x: number; y: number } {
  return {
    x: coord.x * scale + offset.x,
    y: coord.y * scale + offset.y,
  };
}

/** Convert canvas pixels back to meters. */
export function canvasToMeters(
  point: { x: number; y: number },
  scale: number,
  offset: { x: number; y: number },
): Coordinate {
  return {
    x: (point.x - offset.x) / scale,
    y: (point.y - offset.y) / scale,
  };
}

/** Compute centroid of a polygon (for label placement). */
export function polygonCentroid(polygon: Coordinate[]): Coordinate {
  // Use proper signed-area centroid formula
  const pts = polygon[0]?.x === polygon[polygon.length - 1]?.x &&
    polygon[0]?.y === polygon[polygon.length - 1]?.y
    ? polygon.slice(0, -1)
    : polygon;

  let cx = 0;
  let cy = 0;
  let a = 0;
  const n = pts.length;

  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const cross = pts[i].x * pts[j].y - pts[j].x * pts[i].y;
    a += cross;
    cx += (pts[i].x + pts[j].x) * cross;
    cy += (pts[i].y + pts[j].y) * cross;
  }

  a *= 0.5;
  if (Math.abs(a) < 1e-10) {
    // Degenerate: fall back to simple average
    const sx = pts.reduce((s, p) => s + p.x, 0);
    const sy = pts.reduce((s, p) => s + p.y, 0);
    return { x: sx / n, y: sy / n };
  }

  const f = 1 / (6 * a);
  return { x: cx * f, y: cy * f };
}

/** Shoelace formula for polygon area (meters^2). */
export function polygonArea(polygon: Coordinate[]): number {
  const pts = polygon[0]?.x === polygon[polygon.length - 1]?.x &&
    polygon[0]?.y === polygon[polygon.length - 1]?.y
    ? polygon.slice(0, -1)
    : polygon;

  let area = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    area += pts[i].x * pts[j].y - pts[j].x * pts[i].y;
  }
  return Math.abs(area) * 0.5;
}

/** Find point along a wall segment at a given position [0,1]. */
export function pointOnWall(
  wall: WallSegment,
  position: number,
): Coordinate {
  return {
    x: wall.start.x + (wall.end.x - wall.start.x) * position,
    y: wall.start.y + (wall.end.y - wall.start.y) * position,
  };
}

/** Wall angle in radians. */
export function wallAngle(wall: WallSegment): number {
  return Math.atan2(
    wall.end.y - wall.start.y,
    wall.end.x - wall.start.x,
  );
}

/** Wall length in meters. */
export function wallLength(wall: WallSegment): number {
  const dx = wall.end.x - wall.start.x;
  const dy = wall.end.y - wall.start.y;
  return Math.sqrt(dx * dx + dy * dy);
}

/** Compute scale and offset to fit a bounding box into a viewport with padding. */
export function fitBoundsToViewport(
  bounds: { minX: number; minY: number; maxX: number; maxY: number },
  viewportW: number,
  viewportH: number,
  padding = 60,
): { scale: number; offset: { x: number; y: number } } {
  const bw = bounds.maxX - bounds.minX;
  const bh = bounds.maxY - bounds.minY;

  if (bw < 0.01 || bh < 0.01) {
    return { scale: 30, offset: { x: viewportW / 2, y: viewportH / 2 } };
  }

  const availW = viewportW - padding * 2;
  const availH = viewportH - padding * 2;
  const scale = Math.min(availW / bw, availH / bh);

  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerY = (bounds.minY + bounds.maxY) / 2;

  return {
    scale,
    offset: {
      x: viewportW / 2 - centerX * scale,
      y: viewportH / 2 - centerY * scale,
    },
  };
}

/** Compute the bounding box of all rooms in a floor plan. */
export function planBounds(rooms: { polygon?: Coordinate[] }[]): {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
} {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const room of rooms) {
    if (!room.polygon) continue;
    for (const pt of room.polygon) {
      if (pt.x < minX) minX = pt.x;
      if (pt.y < minY) minY = pt.y;
      if (pt.x > maxX) maxX = pt.x;
      if (pt.y > maxY) maxY = pt.y;
    }
  }

  if (!isFinite(minX)) return { minX: 0, minY: 0, maxX: 20, maxY: 20 };
  return { minX, minY, maxX, maxY };
}
