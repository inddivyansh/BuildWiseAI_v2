"use client";

import React, { useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls, Html, Grid } from "@react-three/drei";
import * as THREE from "three";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { ROOM_COLORS, type Room, type WallSegment } from "@/types/floorPlan";
import { polygonCentroid, wallLength, wallAngle } from "@/lib/geometry";

const WALL_HEIGHT = 2.7;
const FLOOR_THICKNESS = 0.1;

export function ThreeViewer() {
  const { currentPlan, selectedRoomId, selectRoom, hoverRoom } =
    useFloorPlanStore();

  if (!currentPlan) return null;

  const { rooms } = currentPlan.spec;
  const walls = currentPlan.walls;

  return (
    <div className="w-full h-full bg-background">
      <Canvas
        shadows
        camera={{ position: [15, 20, 15], fov: 50 }}
        gl={{ antialias: true }}
      >
        <color attach="background" args={["#0a0a0a"]} />

        {/* Lighting */}
        <ambientLight intensity={0.4} />
        <directionalLight
          position={[20, 30, 10]}
          intensity={0.8}
          castShadow
          shadow-mapSize-width={2048}
          shadow-mapSize-height={2048}
        />

        {/* Grid floor */}
        <Grid
          args={[50, 50]}
          cellSize={1}
          cellThickness={0.5}
          cellColor="#222"
          sectionSize={5}
          sectionThickness={1}
          sectionColor="#333"
          fadeDistance={40}
          position={[0, -0.01, 0]}
        />

        {/* Room floors */}
        {rooms.map((room) => (
          <RoomFloor
            key={room.id}
            room={room}
            isSelected={selectedRoomId === room.id}
            onSelect={() => selectRoom(room.id)}
            onHover={(h) => hoverRoom(h ? room.id : null)}
          />
        ))}

        {/* Walls */}
        {walls.map((wall) => (
          <Wall3D key={wall.id} wall={wall} />
        ))}

        <OrbitControls
          makeDefault
          maxPolarAngle={Math.PI / 2.1}
          minDistance={5}
          maxDistance={60}
        />
      </Canvas>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Room floor slab
// ---------------------------------------------------------------------------

function RoomFloor({
  room,
  isSelected,
  onSelect,
  onHover,
}: {
  room: Room;
  isSelected: boolean;
  onSelect: () => void;
  onHover: (hovering: boolean) => void;
}) {
  const shape = useMemo(() => {
    if (!room.polygon || room.polygon.length < 3) return null;
    const s = new THREE.Shape();
    // Convert to Three.js XZ plane (y → z)
    s.moveTo(room.polygon[0].x, room.polygon[0].y);
    for (let i = 1; i < room.polygon.length; i++) {
      s.lineTo(room.polygon[i].x, room.polygon[i].y);
    }
    s.closePath();
    return s;
  }, [room.polygon]);

  const centroid = useMemo(() => {
    if (!room.polygon) return { x: 0, y: 0 };
    return polygonCentroid(room.polygon);
  }, [room.polygon]);

  if (!shape) return null;

  const colors = ROOM_COLORS[room.type] ?? ROOM_COLORS.other;

  return (
    <group>
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, FLOOR_THICKNESS / 2, 0]}
        receiveShadow
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        onPointerEnter={() => onHover(true)}
        onPointerLeave={() => onHover(false)}
      >
        <extrudeGeometry
          args={[
            shape,
            {
              depth: FLOOR_THICKNESS,
              bevelEnabled: false,
            },
          ]}
        />
        <meshStandardMaterial
          color={colors.dark}
          opacity={isSelected ? 0.9 : 0.7}
          transparent
        />
      </mesh>

      {/* Room label */}
      <Html
        position={[centroid.x, 1.5, centroid.y]}
        center
        distanceFactor={15}
        style={{ pointerEvents: "none" }}
      >
        <div className="text-[10px] text-white/80 font-medium whitespace-nowrap bg-black/50 px-1.5 py-0.5 rounded">
          {room.label}
        </div>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Wall box
// ---------------------------------------------------------------------------

function Wall3D({ wall }: { wall: WallSegment }) {
  const len = wallLength(wall);
  const angle = wallAngle(wall);
  const midX = (wall.start.x + wall.end.x) / 2;
  const midZ = (wall.start.y + wall.end.y) / 2;
  const thickness = Math.max(wall.thickness_m, 0.05);

  return (
    <mesh
      position={[midX, WALL_HEIGHT / 2, midZ]}
      rotation={[0, -angle, 0]}
      castShadow
      receiveShadow
    >
      <boxGeometry args={[len, WALL_HEIGHT, thickness]} />
      <meshStandardMaterial
        color={wall.is_exterior ? "#555" : "#444"}
        opacity={0.85}
        transparent
      />
    </mesh>
  );
}
