import { Settings as SettingsIcon } from 'lucide-react'

export function SettingsPage() {
  return (
    <div className="p-8 text-center bg-card rounded-lg border border-border">
      <SettingsIcon className="w-8 h-8 mx-auto text-primary mb-2" />
      <h2 className="text-base font-bold text-foreground">Runtime Settings</h2>
      <p className="text-xs text-muted-foreground mt-1">
        Safety thresholds, sending caps, and configuration. Full implementation arriving in Phase B.
      </p>
    </div>
  )
}
