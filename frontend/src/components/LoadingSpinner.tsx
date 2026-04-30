import { OrbitIcon } from "@/components/OrbitIcon"

export function LoadingSpinner() {
  return (
    <section className="boot-screen" aria-live="polite">
      <OrbitIcon size={48} />
      <p>Preparing your workspace...</p>
    </section>
  )
}
