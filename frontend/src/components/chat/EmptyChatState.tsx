function WaterDrop({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path d="M12 2C12 2 6 10 6 14.5C6 17.8 8.7 20.5 12 20.5C15.3 20.5 18 17.8 18 14.5C18 10 12 2 12 2Z" />
    </svg>
  )
}

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
      <WaterDrop className="empty-state-icon" />
      <p>{labels[variant]}</p>
    </section>
  )
}
