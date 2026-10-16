"use client";

import React, { useRef, useCallback, useEffect, useState } from "react";
import { Stage, Layer, Line, Rect, Text, Group, Circle, Arc } from "react-konva";
import type Konva from "konva";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { useEditorStore } from "@/stores/editorStore";
import {
  metersToCanvas,
  polygonCentroid,
  planBounds,
  fitBoundsToViewport,
  pointOnWall,
  wallAngle,
  wallLength,
} from "@/lib/geometry";
import { RoomShape } from "./RoomShape";
import type { WallSegment, DoorPlacement, WindowPlacement, Coordinate } from "@/types/floorPlan";

export function FloorPlanCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<Konva.Stage>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });

  const { currentPlan, selectRoom, selectedRoomId } = useFloorPlanStore();
  const { zoom, panOffset, showGrid, showSetbacks, showDimensions, setZoom, setPan, activeTool } =
    useEditorStore();

  // Auto-size via ResizeObserver
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setSize({ width, height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Compute scale/offset to auto-fit the plan
  const rooms = currentPlan?.spec.rooms ?? [];
  const bounds = planBounds(rooms);
  const fit = fitBoundsToViewport(bounds, size.width, size.height);

  const scale = fit.scale * zoom;
  const offset = {
    x: fit.offset.x + panOffset.x,
    y: fit.offset.y + panOffset.y,
  };

  // Zoom toward cursor on scroll
  const handleWheel = useCallback(
    (e: Konva.KonvaEventObject<WheelEvent>) => {
      e.evt.preventDefault();
      const stage = stageRef.current;
      if (!stage) return;

      const pointer = stage.getPointerPosition();
      if (!pointer) return;

      const direction = e.evt.deltaY > 0 ? -1 : 1;
      const factor = 1.1;
      const newZoom = direction > 0 ? zoom * factor : zoom / factor;
      const clampedZoom = Math.max(0.1, Math.min(10, newZoom));

      // Adjust pan so zoom centers on cursor
      const mousePointTo = {
        x: (pointer.x - offset.x) / scale,
        y: (pointer.y - offset.y) / scale,
      };
      const newScale = fit.scale * clampedZoom;
      const newOffset = {
        x: pointer.x - mousePointTo.x * newScale - fit.offset.x,
        y: pointer.y - mousePointTo.y * newScale - fit.offset.y,
      };

      setZoom(clampedZoom);
      setPan(newOffset);
    },
    [zoom, scale, offset, fit.scale, fit.offset, setZoom, setPan],
  );

  // Pan on drag (when pan tool or middle mouse)
  const isPanningRef = useRef(false);
  const lastPosRef = useRef({ x: 0, y: 0 });

  const handleMouseDown = useCallback(
    (e: Konva.KonvaEventObject<MouseEvent>) => {
      if (activeTool === "pan" || e.evt.button === 1) {
        isPanningRef.current = true;
        lastPosRef.current = { x: e.evt.clientX, y: e.evt.clientY };
        e.evt.preventDefault();
      }
    },
    [activeTool],
  );

  const handleMouseMove = useCallback(
    (e: Konva.KonvaEventObject<MouseEvent>) => {
      if (!isPanningRef.current) return;
      const dx = e.evt.clientX - lastPosRef.current.x;
      const dy = e.evt.clientY - lastPosRef.current.y;
      lastPosRef.current = { x: e.evt.clientX, y: e.evt.clientY };
      setPan({ x: panOffset.x + dx, y: panOffset.y + dy });
    },
    [panOffset, setPan],
  );

  const handleMouseUp = useCallback(() => {
    isPanningRef.current = false;
  }, []);

  // Click on empty area = deselect
  const handleStageClick = useCallback(
    (e: Konva.KonvaEventObject<MouseEvent>) => {
      if (e.target === stageRef.current) {
        selectRoom(null);
      }
    },
    [selectRoom],
  );

  const plot = currentPlan?.spec.plot;
  const walls = currentPlan?.walls ?? [];
  const doors = currentPlan?.doors ?? [];
  const windows = currentPlan?.windows ?? [];

  const cursorClass =
    activeTool === "pan"
      ? "cursor-grab active:cursor-grabbing"
      : activeTool === "measure"
        ? "cursor-crosshair"
        : "cursor-default";

  return (
    <div ref={containerRef} className={`w-full h-full ${cursorClass}`}>
      <Stage
        ref={stageRef}
        width={size.width}
        height={size.height}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onClick={handleStageClick}
      >
        {/* Grid layer */}
        {showGrid && (
          <Layer listening={false}>
            <GridOverlay
              scale={scale}
              offset={offset}
              width={size.width}
              height={size.height}
            />
          </Layer>
        )}

        {/* Plot boundary + setbacks */}
        {showSetbacks && plot && (
          <Layer listening={false}>
            <PlotBoundary plot={plot} scale={scale} offset={offset} />
          </Layer>
        )}

        {/* Rooms */}
        <Layer>
          {rooms.map((room) => (
            <RoomShape
              key={room.id}
              room={room}
              scale={scale}
              offset={offset}
              isSelected={selectedRoomId === room.id}
              showDimensions={showDimensions}
              complianceRoomIds={[]}
            />
          ))}
        </Layer>

        {/* Walls */}
        <Layer listening={false}>
          {walls.map((wall) => (
            <WallLine key={wall.id} wall={wall} scale={scale} offset={offset} />
          ))}
        </Layer>

        {/* Doors */}
        <Layer listening={false}>
          {doors.map((door) => {
            const wall = walls.find((w) => w.id === door.wall_id);
            if (!wall) return null;
            return (
              <DoorSymbol
                key={door.id}
                door={door}
                wall={wall}
                scale={scale}
                offset={offset}
              />
            );
          })}
        </Layer>

        {/* Windows */}
        <Layer listening={false}>
          {windows.map((win) => {
            const wall = walls.find((w) => w.id === win.wall_id);
            if (!wall) return null;
            return (
              <WindowSymbol
                key={win.id}
                window={win}
                wall={wall}
                scale={scale}
                offset={offset}
              />
            );
          })}
        </Layer>
      </Stage>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function GridOverlay({
  scale,
  offset,
  width,
  height,
}: {
  scale: number;
  offset: { x: number; y: number };
  width: number;
  height: number;
}) {
  const gridSize = scale; // 1 meter grid
  if (gridSize < 5) return null; // too small to draw

  const lines: React.ReactNode[] = [];
  const startX = Math.floor(-offset.x / gridSize) * gridSize + offset.x;
  const startY = Math.floor(-offset.y / gridSize) * gridSize + offset.y;

  for (let x = startX; x < width; x += gridSize) {
    lines.push(
      <Line
        key={`gv${x}`}
        points={[x, 0, x, height]}
        stroke="rgba(255,255,255,0.04)"
        strokeWidth={1}
      />,
    );
  }
  for (let y = startY; y < height; y += gridSize) {
    lines.push(
      <Line
        key={`gh${y}`}
        points={[0, y, width, y]}
        stroke="rgba(255,255,255,0.04)"
        strokeWidth={1}
      />,
    );
  }
  return <>{lines}</>;
}

function PlotBoundary({
  plot,
  scale,
  offset,
}: {
  plot: { width_m: number; depth_m: number; setbacks: { front_m: number; rear_m: number; left_m: number; right_m: number } };
  scale: number;
  offset: { x: number; y: number };
}) {
  const topLeft = metersToCanvas({ x: 0, y: 0 }, scale, offset);
  const w = plot.width_m * scale;
  const h = plot.depth_m * scale;

  const sb = plot.setbacks;
  const iTopLeft = metersToCanvas({ x: sb.left_m, y: sb.front_m }, scale, offset);
  const iw = (plot.width_m - sb.left_m - sb.right_m) * scale;
  const ih = (plot.depth_m - sb.front_m - sb.rear_m) * scale;

  return (
    <>
      {/* Lot boundary */}
      <Rect
        x={topLeft.x}
        y={topLeft.y}
        width={w}
        height={h}
        stroke="rgba(255,255,255,0.15)"
        strokeWidth={1.5}
        dash={[8, 4]}
      />
      {/* Buildable envelope */}
      <Rect
        x={iTopLeft.x}
        y={iTopLeft.y}
        width={iw}
        height={ih}
        stroke="rgba(99,102,241,0.3)"
        strokeWidth={1}
        dash={[4, 4]}
      />
    </>
  );
}

function WallLine({
  wall,
  scale,
  offset,
}: {
  wall: WallSegment;
  scale: number;
  offset: { x: number; y: number };
}) {
  const s = metersToCanvas(wall.start, scale, offset);
  const e = metersToCanvas(wall.end, scale, offset);
  const thickness = Math.max(1, wall.thickness_m * scale);

  return (
    <Line
      points={[s.x, s.y, e.x, e.y]}
      stroke={wall.is_exterior ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.35)"}
      strokeWidth={wall.is_exterior ? thickness : thickness * 0.6}
      lineCap="round"
    />
  );
}

function DoorSymbol({
  door,
  wall,
  scale,
  offset,
}: {
  door: DoorPlacement;
  wall: WallSegment;
  scale: number;
  offset: { x: number; y: number };
}) {
  const pos = pointOnWall(wall, door.position_along_wall);
  const p = metersToCanvas(pos, scale, offset);
  const angle = wallAngle(wall);
  const r = (door.width_m * scale) / 2;

  return (
    <Group x={p.x} y={p.y} rotation={(angle * 180) / Math.PI}>
      {/* Door arc */}
      <Arc
        innerRadius={0}
        outerRadius={r}
        angle={90}
        fill="rgba(255,255,255,0.06)"
        stroke="rgba(255,255,255,0.3)"
        strokeWidth={1}
      />
      {/* Door leaf line */}
      <Line points={[0, 0, r, 0]} stroke="rgba(255,255,255,0.4)" strokeWidth={1.5} />
    </Group>
  );
}

function WindowSymbol({
  window: win,
  wall,
  scale,
  offset,
}: {
  window: WindowPlacement;
  wall: WallSegment;
  scale: number;
  offset: { x: number; y: number };
}) {
  const pos = pointOnWall(wall, win.position_along_wall);
  const p = metersToCanvas(pos, scale, offset);
  const angle = wallAngle(wall);
  const halfW = (win.width_m * scale) / 2;

  return (
    <Group x={p.x} y={p.y} rotation={(angle * 180) / Math.PI}>
      {/* Double line for window */}
      <Line
        points={[-halfW, -2, halfW, -2]}
        stroke="rgba(147,197,253,0.6)"
        strokeWidth={1.5}
      />
      <Line
        points={[-halfW, 2, halfW, 2]}
        stroke="rgba(147,197,253,0.6)"
        strokeWidth={1.5}
      />
    </Group>
  );
}
