import { OrbitIcon } from "@/components/OrbitIcon"

interface EmptyChatStateProps {
  variant: "greeting" | "booting" | "loading"
}

const labels: Record<EmptyChatStateProps["variant"], string> = {
  greeting: "How may I clarify your thoughts today?",
  booting: "Preparing your workspace...",
  loading: "Loading conversation...",
}

export function EmptyChatState({ variant }: EmptyChatStateProps) {
  return (
    <section className="empty-state" aria-live="polite" aria-label={labels[variant]}>
      <OrbitIcon size={48} className="mb-4" />
      <p>{labels[variant]}</p>
    </section>
  )
}
