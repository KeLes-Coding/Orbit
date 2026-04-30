import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider transition-colors focus:outline-none focus:ring-2 focus:ring-accent-orange/40 focus:ring-offset-1",
  {
    variants: {
      variant: {
        default: "bg-[var(--primary)] text-[var(--on-primary)]",
        secondary: "bg-[var(--surface-mid)] text-[var(--ink-soft)]",
        outline:
          "border border-[var(--line)] text-[var(--ink-muted)] bg-transparent",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
