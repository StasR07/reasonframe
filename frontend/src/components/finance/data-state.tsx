import { AlertCircle, DatabaseZap, RefreshCw } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import type { QueryResponse } from "@/api/types"

export function ChartSkeleton() {
  return <div className="space-y-4 py-6" aria-label="Loading data"><Skeleton className="h-72 w-full" /><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-4/5" /></div>
}

export function NetworkError({ message, retry }: { message: string; retry: () => void }) {
  return <Alert variant="destructive"><AlertCircle /><AlertTitle>Could not load data</AlertTitle><AlertDescription className="flex flex-wrap items-center justify-between gap-3">
    <span>{message}</span><Button variant="outline" size="sm" onClick={retry}><RefreshCw />Try again</Button>
  </AlertDescription></Alert>
}

export function ResultState({ response }: { response: QueryResponse }) {
  const titles = { UNSUPPORTED: "This request is not supported", UNAVAILABLE: "No observations are available", INVALID: "This combination is not valid", SUCCESS: "" }
  if (response.status === "SUCCESS") return null
  return <Alert><DatabaseZap /><AlertTitle>{titles[response.status]}</AlertTitle><AlertDescription>{response.errors.join(" ")}</AlertDescription></Alert>
}
