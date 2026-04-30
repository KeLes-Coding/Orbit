export function LoadingSpinner() {
  return (
    <section className="boot-screen" aria-live="polite">
      <svg
        className="boot-svg"
        viewBox="0 0 24 24"
        fill="currentColor"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <path d="M12 2C12 2 6 10 6 14.5C6 17.8 8.7 20.5 12 20.5C15.3 20.5 18 17.8 18 14.5C18 10 12 2 12 2Z" />
      </svg>
      <p>Preparing your workspace...</p>
    </section>
  )
}
