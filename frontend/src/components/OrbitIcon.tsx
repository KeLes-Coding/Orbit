import { useTheme } from "@/hooks/useTheme"
import { cn } from "@/lib/utils"

interface OrbitIconProps {
  className?: string
  size?: number
  /** When true, invert the icon in light mode (e.g. when on a dark background) */
  alwaysInvert?: boolean
}

export function OrbitIcon({ className, size = 24, alwaysInvert }: OrbitIconProps) {
  const { isDark } = useTheme()

  const invert = alwaysInvert ? !isDark : isDark

  return (
    <img
      src="/orbit-icon.svg"
      alt="Orbit"
      width={size}
      height={size}
      className={cn("select-none", className)}
      style={{
        filter: invert ? "invert(1)" : undefined,
      }}
    />
  )
}
