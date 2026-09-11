import type { SVGProps } from "react"
import { cn } from "@/lib/utils"

type MarkProps = SVGProps<SVGSVGElement> & { title?: string }

export function ReasonframeMark({ className, title, ...props }: MarkProps) {
  return <svg
    viewBox="0 0 48 48"
    fill="none"
    className={cn("shrink-0", className)}
    role={title ? "img" : undefined}
    aria-hidden={title ? undefined : true}
    focusable="false"
    {...props}
  >
    {title && <title>{title}</title>}
    <rect x="6.5" y="13.5" width="28" height="28" rx="4.5" stroke="currentColor" strokeWidth="4.5" />
    <rect x="13.5" y="6.5" width="28" height="28" rx="4.5" stroke="var(--primary)" strokeWidth="4.5" />
    <path d="M34.5 29v9" stroke="currentColor" strokeWidth="4.5" strokeLinecap="round" />
  </svg>
}

export function ReasonframeLockup({ className }: { className?: string }) {
  return <div className={cn("flex items-center gap-2.5", className)} aria-label="Reasonframe">
    <ReasonframeMark className="size-7" />
    <span className="font-semibold tracking-[-0.02em]">Reasonframe</span>
  </div>
}
