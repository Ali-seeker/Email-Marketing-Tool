import { ShieldCheck } from 'lucide-react'

export function SafetyPage() {
  return (
    <div className="p-8 text-center bg-card rounded-lg border border-border">
      <ShieldCheck className="w-8 h-8 mx-auto text-primary mb-2" />
      <h2 className="text-base font-bold text-foreground">Health & Safety Monitor</h2>
      <p className="text-xs text-muted-foreground mt-1">
        Gauges, alerts, emergency killswitch, and resumption. Full implementation arriving in Phase B.
      </p>
    </div>
  )
}
