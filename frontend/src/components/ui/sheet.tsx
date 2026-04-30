"use client"

import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

const Sheet = DialogPrimitive.Root
const SheetTrigger = DialogPrimitive.Trigger
const SheetClose = DialogPrimitive.Close

const SheetOverlay = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-50 bg-black/40 backdrop-blur-[1px]",
      "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className,
    )}
    {...props}
  />
))
SheetOverlay.displayName = "SheetOverlay"

const SheetContent = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
    side?: "left" | "right"
  }
>(({ className, side = "left", children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <SheetOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed z-50 flex flex-col gap-4 border-[var(--line)] bg-[var(--background)] shadow-[var(--shadow-float)]",
        "transition-transform duration-300 ease-in-out",
        "data-[state=open]:animate-in data-[state=closed]:animate-out",
        side === "left" && [
          "inset-y-0 left-0 h-full w-[280px] border-r",
          "data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left",
        ],
        side === "right" && [
          "inset-y-0 right-0 h-full w-[280px] border-l",
          "data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
        ],
        className,
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-3 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-accent-orange/40 focus:ring-offset-1 disabled:pointer-events-none text-[var(--ink-muted)]">
        <X className="h-4 w-4" />
        <span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
))
SheetContent.displayName = "SheetContent"

const SheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col gap-1.5 px-6 pt-6", className)} {...props} />
)
SheetHeader.displayName = "SheetHeader"

const SheetTitle = React.forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("font-serif text-lg font-semibold text-[var(--ink)]", className)}
    {...props}
  />
))
SheetTitle.displayName = "SheetTitle"

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetTitle }
