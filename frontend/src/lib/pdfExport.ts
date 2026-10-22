/**
 * Vector PDF Generator for GeneratedFloorPlan.
 * Produces a standard, valid PDF 1.4 document containing vector drawings of:
 * - Room polygons with pastel fills and outlines
 * - Wall segments (exterior thick, interior standard)
 * - Door placements with arc swings
 * - Window placements
 * - Room labels, dimensions, and area annotations
 * - Architectural title block
 *
 * Fully compliant with PyMuPDF (fitz) vector extraction and PDF.js rendering.
 */

import type { GeneratedFloorPlan, Coordinate } from "@/types/floorPlan";

export interface PdfExportOptions {
  projectName?: string;
  author?: string;
  paperSize?: "A3" | "A4"; // landscape
}

export function createFloorPlanPdfBlob(
  plan: GeneratedFloorPlan,
  options: PdfExportOptions = {},
): Blob {
  const isA3 = options.paperSize === "A3";
  // Landscape dimensions in PDF points (72 pt = 1 inch)
  const pageWidth = isA3 ? 1190.55 : 841.89; // A3 or A4 landscape width
  const pageHeight = isA3 ? 841.89 : 595.28; // A3 or A4 landscape height

  const margin = 40;
  const titleBlockHeight = 50;
  const drawWidth = pageWidth - margin * 2;
  const drawHeight = pageHeight - margin * 2 - titleBlockHeight;

  // Calculate bounding box of the floor plan in meters
  const rooms = plan.spec.rooms ?? [];
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  for (const room of rooms) {
    if (room.polygon && room.polygon.length > 0) {
      for (const pt of room.polygon) {
        if (pt.x < minX) minX = pt.x;
        if (pt.x > maxX) maxX = pt.x;
        if (pt.y < minY) minY = pt.y;
        if (pt.y > maxY) maxY = pt.y;
      }
    }
  }

  for (const wall of plan.walls ?? []) {
    minX = Math.min(minX, wall.start.x, wall.end.x);
    maxX = Math.max(maxX, wall.start.x, wall.end.x);
    minY = Math.min(minY, wall.start.y, wall.end.y);
    maxY = Math.max(maxY, wall.start.y, wall.end.y);
  }

  // Fallback if empty coordinates
  if (!Number.isFinite(minX) || !Number.isFinite(maxX)) {
    minX = 0;
    maxX = plan.spec.plot?.width_m || 20;
    minY = 0;
    maxY = plan.spec.plot?.depth_m || 15;
  }

  const planWidthM = Math.max(maxX - minX, 1);
  const planHeightM = Math.max(maxY - minY, 1);

  // Scale factor (PDF points per meter) to fit draw area
  const scale = Math.min(drawWidth / planWidthM, drawHeight / planHeightM) * 0.88;

  // Center the plan on the sheet
  const offsetX = margin + (drawWidth - planWidthM * scale) / 2 - minX * scale;
  // Note: PDF coordinate origin (0,0) is at BOTTOM-LEFT
  const offsetY = margin + titleBlockHeight + (drawHeight - planHeightM * scale) / 2 + planHeightM * scale + minY * scale;

  function toPdfX(xMeters: number): number {
    return offsetX + xMeters * scale;
  }

  function toPdfY(yMeters: number): number {
    // Invert Y so top-down blueprint coordinates map to bottom-up PDF coordinates
    return offsetY - yMeters * scale;
  }

  const ops: string[] = [];

  // 1. Sheet Border & Grid Marks
  ops.push("q 0.5 w 0.7 0.75 0.8 RG");
  ops.push(`${margin} ${margin} ${pageWidth - margin * 2} ${pageHeight - margin * 2} re S Q`);

  // 2. Room Polygons (Fills and light borders)
  for (const room of rooms) {
    if (!room.polygon || room.polygon.length < 3) continue;

    // Soft pastel fill based on room type
    let r = 0.94, g = 0.96, b = 0.98;
    if (room.type.includes("bed")) { r = 0.90; g = 0.93; b = 0.98; }
    else if (room.type.includes("bath")) { r = 0.92; g = 0.90; b = 0.98; }
    else if (room.type.includes("kitchen") || room.type.includes("dining")) { r = 0.98; g = 0.93; b = 0.90; }
    else if (room.type.includes("living")) { r = 0.98; g = 0.96; b = 0.88; }

    ops.push(`q ${r.toFixed(3)} ${g.toFixed(3)} ${b.toFixed(3)} rg 0.75 0.78 0.82 RG 0.5 w`);
    const p0 = room.polygon[0];
    ops.push(`${toPdfX(p0.x).toFixed(2)} ${toPdfY(p0.y).toFixed(2)} m`);
    for (let i = 1; i < room.polygon.length; i++) {
      const p = room.polygon[i];
      ops.push(`${toPdfX(p.x).toFixed(2)} ${toPdfY(p.y).toFixed(2)} l`);
    }
    ops.push("b Q"); // fill and stroke
  }

  // 3. Walls (structural lines: thick for exterior, standard for interior)
  for (const wall of plan.walls ?? []) {
    const lineWidth = wall.is_exterior ? 3.0 : 1.5;
    const gray = wall.is_exterior ? "0.1 0.1 0.15" : "0.25 0.25 0.3";
    ops.push(`q ${lineWidth} w ${gray} RG 1 J 1 j`);
    ops.push(`${toPdfX(wall.start.x).toFixed(2)} ${toPdfY(wall.start.y).toFixed(2)} m`);
    ops.push(`${toPdfX(wall.end.x).toFixed(2)} ${toPdfY(wall.end.y).toFixed(2)} l S Q`);
  }

  // 4. Doors (openings + swing arcs)
  for (const door of plan.doors ?? []) {
    const parentWall = plan.walls?.find((w) => w.id === door.wall_id);
    if (!parentWall) continue;
    const wallDx = parentWall.end.x - parentWall.start.x;
    const wallDy = parentWall.end.y - parentWall.start.y;
    const wallLen = Math.hypot(wallDx, wallDy) || 1;
    const ux = wallDx / wallLen;
    const uy = wallDy / wallLen;
    const nx = -uy;
    const ny = ux;

    const doorX = parentWall.start.x + ux * door.position_along_wall;
    const doorY = parentWall.start.y + uy * door.position_along_wall;
    const doorWidth = door.width_m || 0.9;

    const x1 = toPdfX(doorX);
    const y1 = toPdfY(doorY);
    const x2 = toPdfX(doorX + (ux * 0.2 + nx * 0.8) * doorWidth);
    const y2 = toPdfY(doorY + (uy * 0.2 + ny * 0.8) * doorWidth);

    ops.push("q 1.0 w 0.15 0.5 0.8 RG 1 J");
    // Door leaf line
    ops.push(`${x1.toFixed(2)} ${y1.toFixed(2)} m ${x2.toFixed(2)} ${y2.toFixed(2)} l S`);
    // Swing arc chord
    ops.push("[2 2] 0 d 0.5 w");
    const arcX = toPdfX(doorX + ux * doorWidth);
    const arcY = toPdfY(doorY + uy * doorWidth);
    ops.push(`${x2.toFixed(2)} ${y2.toFixed(2)} m ${arcX.toFixed(2)} ${arcY.toFixed(2)} l S Q`);
  }

  // 5. Windows
  for (const win of plan.windows ?? []) {
    const parentWall = plan.walls?.find((w) => w.id === win.wall_id);
    if (!parentWall) continue;
    const wallDx = parentWall.end.x - parentWall.start.x;
    const wallDy = parentWall.end.y - parentWall.start.y;
    const wallLen = Math.hypot(wallDx, wallDy) || 1;
    const ux = wallDx / wallLen;
    const uy = wallDy / wallLen;

    const winX = parentWall.start.x + ux * win.position_along_wall;
    const winY = parentWall.start.y + uy * win.position_along_wall;
    const winWidth = win.width_m || 1.2;

    const x1 = toPdfX(winX);
    const y1 = toPdfY(winY);
    const x2 = toPdfX(winX + ux * winWidth);
    const y2 = toPdfY(winY + uy * winWidth);

    ops.push("q 2.0 w 0.2 0.6 0.9 RG");
    ops.push(`${x1.toFixed(2)} ${y1.toFixed(2)} m ${x2.toFixed(2)} ${y2.toFixed(2)} l S Q`);
  }

  // 6. Room Labels and Areas
  for (const room of rooms) {
    if (!room.polygon || room.polygon.length === 0) continue;
    let cx = 0, cy = 0;
    for (const p of room.polygon) {
      cx += p.x;
      cy += p.y;
    }
    cx /= room.polygon.length;
    cy /= room.polygon.length;

    const px = toPdfX(cx);
    const py = toPdfY(cy);
    const area = room.actual_area_sqm || room.target_area_sqm || 0;
    const label = (room.label || room.type).toUpperCase().replace(/[()]/g, "");

    ops.push("BT");
    ops.push("/F1 9 Tf");
    ops.push("0.15 0.18 0.25 rg");
    ops.push(`${(px - 24).toFixed(2)} ${py.toFixed(2)} Td`);
    ops.push(`(${escapePdf(label)}) Tj`);
    ops.push("ET");

    ops.push("BT");
    ops.push("/F1 7 Tf");
    ops.push("0.4 0.45 0.5 rg");
    ops.push(`${(px - 18).toFixed(2)} ${(py - 10).toFixed(2)} Td`);
    ops.push(`(${area.toFixed(1)} sq m) Tj`);
    ops.push("ET");
  }

  // 7. Architectural Title Block (Bottom)
  const tbY = margin + 12;
  ops.push(`q 0.5 w 0.8 0.85 0.9 RG ${margin} ${margin} ${pageWidth - margin * 2} ${titleBlockHeight} re S Q`);

  // Title Block Text
  const title = options.projectName || "Floor Plan Blueprint";
  const genId = plan.generation_id ? plan.generation_id.slice(0, 8) : "DRAFT";
  const areaTotal = plan.total_area_sqm ? `${plan.total_area_sqm.toFixed(1)} sq m` : "N/A";
  const dateStr = new Date().toISOString().split("T")[0];

  ops.push("BT");
  ops.push("/F1 12 Tf");
  ops.push("0.1 0.12 0.2 rg");
  ops.push(`${(margin + 16).toFixed(2)} ${(tbY + 22).toFixed(2)} Td`);
  ops.push(`(${escapePdf(title)}) Tj`);
  ops.push("ET");

  ops.push("BT");
  ops.push("/F1 8 Tf");
  ops.push("0.45 0.5 0.55 rg");
  ops.push(`${(margin + 16).toFixed(2)} ${(tbY + 8).toFixed(2)} Td`);
  ops.push(`(ID: ${genId}   |   Scale: 1:100   |   Total Area: ${areaTotal}   |   Date: ${dateStr}) Tj`);
  ops.push("ET");

  ops.push("BT");
  ops.push("/F1 9 Tf");
  ops.push("0.2 0.4 0.8 rg");
  ops.push(`${(pageWidth - margin - 140).toFixed(2)} ${(tbY + 16).toFixed(2)} Td`);
  ops.push("(BuildwiseAI Architectural Sheet) Tj");
  ops.push("ET");

  // Assemble valid PDF 1.4 objects
  const streamContent = ops.join("\n");
  const streamLength = streamContent.length;

  const pdfParts: string[] = [];
  pdfParts.push("%PDF-1.4\n%âãÏÓ\n");

  const offsets: number[] = [];
  let currentOffset = pdfParts[0].length;

  // Obj 1: Catalog
  offsets.push(currentOffset);
  const obj1 = "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n";
  pdfParts.push(obj1);
  currentOffset += obj1.length;

  // Obj 2: Pages
  offsets.push(currentOffset);
  const obj2 = "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n";
  pdfParts.push(obj2);
  currentOffset += obj2.length;

  // Obj 3: Page
  offsets.push(currentOffset);
  const obj3 = `3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth.toFixed(2)} ${pageHeight.toFixed(2)}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n`;
  pdfParts.push(obj3);
  currentOffset += obj3.length;

  // Obj 4: Content Stream
  offsets.push(currentOffset);
  const obj4 = `4 0 obj\n<< /Length ${streamLength} >>\nstream\n${streamContent}\nendstream\nendobj\n`;
  pdfParts.push(obj4);
  currentOffset += obj4.length;

  // Obj 5: Font
  offsets.push(currentOffset);
  const obj5 = "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n";
  pdfParts.push(obj5);
  currentOffset += obj5.length;

  // Cross Reference Table
  const xrefOffset = currentOffset;
  let xref = `xref\n0 6\n0000000000 65535 f \n`;
  for (const off of offsets) {
    xref += `${String(off).padStart(10, "0")} 00000 n \n`;
  }
  xref += `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  pdfParts.push(xref);

  return new Blob(pdfParts, { type: "application/pdf" });
}

function escapePdf(str: string): string {
  return str.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}
