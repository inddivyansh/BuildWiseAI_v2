"use client";

import React, { useState } from "react";
import { Group, Line, Text } from "react-konva";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { metersToCanvas, polygonCentroid, polygonArea } from "@/lib/geometry";
import { ROOM_COLORS, type Room } from "@/types/floorPlan";

interface Props {
  room: Room;
  scale: number;
  offset: { x: number; y: number };
  isSelected: boolean;
  showDimensions: boolean;
  complianceRoomIds: string[];
}

export function RoomShape({
  room,
  scale,
  offset,
  isSelected,
  showDimensions,
  complianceRoomIds,
}: Props) {
  const { selectRoom, hoverRoom, hoveredRoomId } = useFloorPlanStore();
  const [localHover, setLocalHover] = useState(false);

  if (!room.polygon || room.polygon.length < 3) return null;

  const isHovered = hoveredRoomId === room.id || localHover;
  const hasViolation = complianceRoomIds.includes(room.id);

  const colors = ROOM_COLORS[room.type] ?? ROOM_COLORS.other;
  const baseFill = colors.dark;

  // Brighten on hover
  const fillOpacity = isHovered ? 0.85 : 0.65;

  const points = room.polygon.flatMap((p) => {
    const cp = metersToCanvas(p, scale, offset);
    return [cp.x, cp.y];
  });

  // Centroid for label
  const centroid = polygonCentroid(room.polygon);
  const cCanvas = metersToCanvas(centroid, scale, offset);
  const area = room.actual_area_sqm ?? polygonArea(room.polygon);

  // Stroke styles
  let stroke = "rgba(255,255,255,0.15)";
  let strokeWidth = 1;
  let dash: number[] | undefined;

  if (isSelected) {
    stroke = "rgba(255,255,255,0.9)";
    strokeWidth = 2;
    dash = [6, 3];
  } else if (hasViolation) {
    stroke = "rgba(239,68,68,0.8)";
    strokeWidth = 2;
  }

  // Only show label if room is big enough on screen
  const showLabel = scale > 5;

  return (
    <Group
      onClick={() => selectRoom(room.id)}
      onTap={() => selectRoom(room.id)}
      onMouseEnter={() => {
        setLocalHover(true);
        hoverRoom(room.id);
      }}
      onMouseLeave={() => {
        setLocalHover(false);
        hoverRoom(null);
      }}
    >
      <Line
        points={points}
        fill={baseFill}
        opacity={fillOpacity}
        closed
        stroke={stroke}
        strokeWidth={strokeWidth}
        dash={dash}
        hitStrokeWidth={10}
      />
      {showLabel && (
        <>
          <Text
            x={cCanvas.x}
            y={cCanvas.y - 8}
            text={room.label}
            fontSize={11}
            fontFamily="Inter, system-ui, sans-serif"
            fill="rgba(255,255,255,0.85)"
            fontStyle="600"
            align="center"
            offsetX={room.label.length * 3}
          />
          {showDimensions && (
            <Text
              x={cCanvas.x}
              y={cCanvas.y + 6}
              text={`${area.toFixed(1)} m²`}
              fontSize={9}
              fontFamily="Inter, system-ui, sans-serif"
              fill="rgba(255,255,255,0.5)"
              align="center"
              offsetX={(`${area.toFixed(1)} m²`).length * 2.5}
            />
          )}
        </>
      )}
    </Group>
  );
}
