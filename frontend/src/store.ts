// Gemeinsamer Zustand des Editors. Alle Änderungen laufen als Befehle über den Kern (api.op);
// danach werden Bäume und Inspector neu geladen. Die Oberfläche rechnet selbst nichts KNX-Spezifisches.
import { reactive } from 'vue'
import { api, ApiError, type CatalogItem, type DeviceDetail, type GaDetail, type ProjectInfo, type TreeNode } from './api'

export type TreeKind = 'groupaddresses' | 'topology' | 'buildings' | 'catalog'

/** Ein Element im Baum (vereinheitlicht für Auswahl, Ziehen, Kontextmenü). */
export interface Item {
  key: string
  kind: 'area' | 'line' | 'device' | 'object' | 'link' | 'range' | 'ga' | 'space' | 'product' | 'folder'
  label: string
  sub?: string
  badge?: string
  warn?: boolean
  mono?: string // Adress-Präfix in Festbreitenschrift
  lazy?: boolean
  children?: Item[]
  // Bezüge
  uid?: string // Gerät
  ref?: string // Objekt-Ref
  number?: number
  address?: string // GA
  rangeUid?: string
  level?: 'main' | 'middle'
  area?: number
  line?: number
  product?: CatalogItem
  name?: string
}

export type Notify = (type: 'success' | 'error' | 'info' | 'warning', text: string) => void

export const store = reactive({
  project: null as ProjectInfo | null,
  trees: { groupaddresses: [], topology: [], buildings: [], catalog: [] } as Record<TreeKind, Item[]>,
  objects: {} as Record<string, Item[]>, // Geräte-UID → Objektknoten (nachgeladen)
  selection: [] as Item[],
  device: null as DeviceDetail | null, // Detail für Inspector/Parameter
  ga: null as GaDetail | null,
  paramsFor: null as string | null, // Gerät, dessen Parameterdialog offen ist
  clipboard: null as { kind: 'devices'; uids: string[] } | null,
  drag: null as Item[] | null,
  busy: false,
  version: 0, // zählt nach jeder Änderung hoch (Bäume neu aufbauen)
  notify: (() => {}) as Notify,
})

// --- Bäume aus der API ---------------------------------------------------------------

function fromGa(nodes: TreeNode[]): Item[] {
  return nodes.map((n) => {
    if (n.data.type === 'range') {
      const [num, ...rest] = n.label.split(' ')
      return {
        key: n.key, kind: 'range', label: rest.join(' '), mono: num, rangeUid: n.data.uid, level: n.data.level,
        name: n.data.name, children: fromGa(n.children ?? []),
      }
    }
    const linked = n.data.linked ?? 0
    return {
      key: n.key, kind: 'ga', label: n.data.name || '(ohne Namen)', mono: n.data.address, address: n.data.address,
      sub: n.data.dpt?.replace('DPST-', '').replace('DPT-', '').replace('-', '.') ?? '',
      badge: linked ? String(linked) : 'frei', warn: !linked, name: n.data.name,
    }
  })
}

function fromTopo(nodes: TreeNode[]): Item[] {
  return nodes.map((n) => {
    const t = n.data.type
    if (t === 'area') return { key: n.key, kind: 'area', label: n.label.replace(/^\d+ /, ''), mono: n.label.split(' ')[0], children: fromTopo(n.children ?? []) }
    if (t === 'line')
      return {
        key: n.key, kind: 'line', label: `Linie ${n.data.medium ?? ''}`.trim(), mono: `${n.data.area}.${n.data.line}`,
        area: n.data.area, line: n.data.line, children: fromTopo(n.children ?? []),
      }
    const [pa, ...rest] = n.label.split(' ')
    return { key: n.key, kind: 'device', label: rest.join(' '), mono: pa, uid: n.data.uid, lazy: true }
  })
}

function fromBuildings(nodes: TreeNode[]): Item[] {
  return nodes.map((n) => {
    if (n.data.type === 'device') {
      const [pa, ...rest] = n.label.split(' ')
      return { key: n.key, kind: 'device', label: rest.join(' '), mono: pa, uid: n.data.uid, lazy: true }
    }
    return { key: n.key, kind: 'space', label: n.label, sub: n.data.space_type, children: fromBuildings(n.children ?? []) }
  })
}

function fromCatalog(items: CatalogItem[]): Item[] {
  const byMfr = new Map<string, Item[]>()
  for (const p of items) {
    const list = byMfr.get(p.manufacturer) ?? []
    list.push({ key: `p:${p.product_ref}`, kind: 'product', label: p.name, sub: p.order, product: p })
    byMfr.set(p.manufacturer, list)
  }
  return [...byMfr].map(([m, children]) => ({ key: `m:${m}`, kind: 'folder', label: m, children }))
}

export function objectItems(d: DeviceDetail): Item[] {
  return d.objects.map((o) => ({
    key: `${d.uid}#${o.ref}`, kind: 'object', label: `${o.text}${o.function ? ' – ' + o.function : ''}`,
    mono: String(o.number), sub: [o.dpt, o.flags].filter(Boolean).join('  '), uid: d.uid, ref: o.ref, number: o.number,
    warn: !o.active && o.gas.length > 0, badge: !o.active && o.gas.length ? 'inaktiv' : undefined,
    children: o.gas.map((g, i) => ({
      key: `${d.uid}#${o.ref}@${g.address}`, kind: 'link', label: g.name || '(ohne Namen)', mono: g.address,
      uid: d.uid, ref: o.ref, address: g.address, badge: i === 0 ? 'sendend' : undefined,
    })),
  }))
}

// --- Laden ----------------------------------------------------------------------------

export async function loadAll() {
  const [project, ga, topo, bld, cat] = await Promise.all([
    api.project(), api.tree('groupaddresses'), api.tree('topology'), api.tree('buildings'), api.catalog(),
  ])
  store.project = project
  store.trees = { groupaddresses: fromGa(ga), topology: fromTopo(topo), buildings: fromBuildings(bld), catalog: fromCatalog(cat) }
  // nachgeladene Objekte bekannter Geräte aktualisieren
  await Promise.all(Object.keys(store.objects).map((uid) => loadObjects(uid, true)))
  await refreshDetail()
  store.version++
}

export async function loadObjects(uid: string, force = false): Promise<Item[]> {
  if (!force && store.objects[uid]) return store.objects[uid]
  try {
    const d = await api.device(uid)
    store.objects[uid] = objectItems(d)
  } catch {
    delete store.objects[uid] // Gerät gibt es nicht mehr
  }
  return store.objects[uid] ?? []
}

async function refreshDetail() {
  const first = store.selection[0]
  store.device = null
  store.ga = null
  if (!first) return
  try {
    if (first.uid && ['device', 'object', 'link'].includes(first.kind)) store.device = await api.device(first.uid)
    if (first.address && ['ga', 'link'].includes(first.kind)) store.ga = await api.ga(first.address)
  } catch {
    store.selection = []
  }
}

export async function select(items: Item[]) {
  store.selection = items
  await refreshDetail()
}

// --- Befehle --------------------------------------------------------------------------

export async function run(op: string, args: Record<string, unknown>, done?: string) {
  store.busy = true
  try {
    const r = await api.op(op, args)
    await loadAll()
    if (done) store.notify('success', `${done} – ⌘Z macht es rückgängig`)
    return r.result
  } catch (e) {
    store.notify('error', e instanceof ApiError ? e.message : String(e))
    return undefined
  } finally {
    store.busy = false
  }
}

export async function undo() {
  try {
    const r = await api.undo()
    await loadAll()
    store.notify('info', `Rückgängig: ${r.undone}`)
  } catch (e) {
    store.notify('warning', (e as Error).message)
  }
}

export async function redo() {
  try {
    const r = await api.redo()
    await loadAll()
    store.notify('info', `Wiederholt: ${r.redone}`)
  } catch (e) {
    store.notify('warning', (e as Error).message)
  }
}

export async function save() {
  try {
    await api.save()
    if (store.project) store.project.dirty = false
    store.notify('success', 'Gespeichert')
  } catch (e) {
    store.notify('error', (e as Error).message)
  }
}

// --- Ziehen & Ablegen: was darf wohin? -------------------------------------------------

export function dropAction(target: Item, items: Item[], copy: boolean): { label: string; apply: () => Promise<unknown> } | null {
  const kinds = new Set(items.map((i) => i.kind))
  const only = (k: Item['kind']) => kinds.size === 1 && kinds.has(k)

  // GA → Objekt: verknüpfen (⌥ = als sendende GA)
  if (only('ga') && (target.kind === 'object' || target.kind === 'link') && target.uid && target.ref) {
    const ga = items[0].address!
    return {
      label: copy ? `${ga} als sendende GA verknüpfen` : `${ga} verknüpfen`,
      apply: () => run('link', { device_uid: target.uid, ref: target.ref, address: ga, number: target.number ?? null, sending: copy }, 'Verknüpft'),
    }
  }
  // Objekt → GA: verknüpfen
  if (only('object') && target.kind === 'ga' && items[0].uid) {
    const o = items[0]
    return {
      label: `mit ${target.address} verknüpfen`,
      apply: () => run('link', { device_uid: o.uid, ref: o.ref, address: target.address, number: o.number }, 'Verknüpft'),
    }
  }
  // GAs → Mittelgruppe: verschieben (⌥ = kopieren)
  if (only('ga') && target.kind === 'range' && target.level === 'middle') {
    const addrs = items.map((i) => i.address!)
    return copy
      ? { label: `${addrs.length} GA nach „${target.label}“ kopieren`, apply: () => run('ga_copy', { addresses: addrs, target_uid: target.rangeUid }, 'Kopiert') }
      : { label: `${addrs.length} GA nach „${target.label}“ verschieben`, apply: () => run('ga_move', { addresses: addrs, target_uid: target.rangeUid }, 'Verschoben') }
  }
  // Geräte → Linie: verschieben (⌥ = kopieren inkl. Parameter)
  if (only('device') && target.kind === 'line') {
    const uids = items.map((i) => i.uid!)
    const args = { uids, area: target.area, line: target.line }
    return copy
      ? { label: `${uids.length} Gerät(e) nach ${target.mono} kopieren`, apply: () => run('device_copy', args, 'Kopiert') }
      : { label: `${uids.length} Gerät(e) nach ${target.mono} verschieben`, apply: () => run('device_move', args, 'Verschoben') }
  }
  // Produkt → Linie: einfügen
  if (only('product') && target.kind === 'line') {
    const p = items[0].product!
    return {
      label: `${p.name} in ${target.mono} einfügen`,
      apply: () => run('device_insert', { product_ref: p.product_ref, h2p_ref: p.h2p_ref, app_id: p.app_id, area: target.area, line: target.line }, 'Gerät eingefügt'),
    }
  }
  return null
}

// --- Offene Fenster (dockview-Panels) ---------------------------------------------------
// Jede Baumansicht meldet sich hier an, damit Suche/Befehle sie ansteuern können.
export interface PaneHandle { kind: TreeKind; reveal: (key: string) => Promise<boolean | undefined> }
export const panes = new Map<string, PaneHandle>()

export const VIEW_TITLES: Record<TreeKind | 'inspector', string> = {
  groupaddresses: 'Gruppenadressen',
  topology: 'Topologie',
  buildings: 'Gebäude',
  catalog: 'Katalog',
  inspector: 'Eigenschaften',
}
