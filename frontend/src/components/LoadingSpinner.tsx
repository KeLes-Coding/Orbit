export function LoadingSpinner() {
  return (
    <section className="boot-screen" aria-live="polite">
      <span className="material-symbols-outlined" aria-hidden="true">
        water_drop
      </span>
      <p>Preparing your workspace...</p>
    </section>
  )
}
