"use client";

interface VolumeIndicatorProps {
  volume: number; // 0-100
}

export function VolumeIndicator({ volume }: VolumeIndicatorProps) {
  return (
    <div className="flex items-center justify-center">
      <div
        className="h-1 w-32 overflow-hidden rounded-full"
        style={{ background: "rgba(0,0,0,0.06)" }}
      >
        <div
          className="h-full rounded-full transition-all duration-75"
          style={{
            width: `${volume}%`,
            background: "rgba(74,222,128,0.7)",
          }}
        />
      </div>
    </div>
  );
}
