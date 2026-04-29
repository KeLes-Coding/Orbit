import { useOrbitStore } from '@/stores/useOrbitStore'

export function useTheme() {
  const isDark = useOrbitStore((s) => s.isDark)
  const toggleTheme = useOrbitStore((s) => s.toggleTheme)
  return { isDark, toggleTheme }
}
