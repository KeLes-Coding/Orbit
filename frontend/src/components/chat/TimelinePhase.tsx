import type { ReactNode } from "react"

interface TimelinePhaseProps {
  icon: ReactNode
  label: string
  detail?: string
  isLast?: boolean
  children?: ReactNode
}

export function TimelinePhase({
  icon,
  label,
  detail,
  isLast = false,
  children,
}: TimelinePhaseProps) {
  return (
    <div className={`tl-phase${isLast ? " tl-phase-last" : ""}`}>
      <div className="tl-connector">
        <span className="tl-dot">{icon}</span>
        {!isLast && <span className="tl-line" aria-hidden="true" />}
      </div>
      <div className="tl-body">
        <div className="tl-header-static">
          <span className="tl-label">{label}</span>
          {detail && <span className="tl-detail">{detail}</span>}
        </div>
        {children && <div className="tl-content">{children}</div>}
      </div>
    </div>
  )
}
