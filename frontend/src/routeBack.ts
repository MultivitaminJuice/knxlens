// Rückweg der Tunnelverbindung (Route-Back/NAT-Modus), gemeinsam für Busmonitor und „Gerät lesen“.
// 'auto': der Server entscheidet (Route-Back, wenn das Interface nicht im Subnetz der eigenen
// Absenderadresse liegt). 'on': erzwingen, z. B. bei Subnetz-Kollision mit dem eigenen LAN.
// Abschalten gibt es nicht: xknx schaltet Route-Back ohne eigenes Subnetz ohnehin selbst ein.

export type RouteBackMode = 'auto' | 'on'

const KEY = 'knxlens:routeBack'

export const ROUTE_BACK_OPTIONS: { label: string; value: RouteBackMode }[] = [
  { label: 'Rückweg: automatisch', value: 'auto' },
  { label: 'Route-Back erzwingen', value: 'on' },
]

export function loadRouteBack(): RouteBackMode {
  try {
    return localStorage.getItem(KEY) === 'on' ? 'on' : 'auto'
  } catch {
    return 'auto'
  }
}

export function saveRouteBack(mode: RouteBackMode) {
  try { localStorage.setItem(KEY, mode) } catch { /* nur Komfort */ }
}

/** Wert für die API: true = erzwingen, null = automatisch. */
export function routeBackValue(mode: RouteBackMode): boolean | null {
  return mode === 'on' ? true : null
}
