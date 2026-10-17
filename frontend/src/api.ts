// Typen und Aufrufe der Editor-API (knxlens/editor). Die Logik bleibt im Python-Kern.

export interface TreeNode {
  key: string
  label: string
  icon?: string
  leaf?: boolean
  data: {
    type: string
    uid?: string
    pa?: string | null
    address?: string
    dpt?: string
    name?: string
    linked?: number
    medium?: string
    area?: number
    line?: number
    level?: 'main' | 'middle'
    space_type?: string
    type_label?: string
    child_types?: string[]
    holds_devices?: boolean
    space?: string
    medium_id?: string
  }
  children?: TreeNode[]
}

export interface ProjectInfo {
  name: string
  file: string
  project_id: string
  dirty: boolean
  undo: string | null
  redo: string | null
  devices: number
  group_addresses: number
  links: number
  orphans: number
  space_types: Record<string, string>
  space_root: string[]
  media: Record<string, string>
}

export interface DeviceObject {
  ref: string
  number: number
  text: string
  function: string
  size: string | null
  dpt: string
  flags: string
  active: boolean
  gas: { address: string; name: string }[]
}

export interface ParamItem {
  id: string
  text: string
  display: string
  value: number | string | null
  default: string
  changed: boolean
  kind: 'enum' | 'number' | 'time' | 'float' | 'text' | 'color' | 'none'
  editable: boolean
  suffix: string
  options?: { value: number; label: string }[]
  checkbox?: boolean
  min?: number | null
  max?: number | null
  maxlength?: number
}

export interface DeviceDetail {
  uid: string
  pa: string | null
  name: string
  description: string
  origin: string
  product: string
  app_id: string
  has_products: boolean
  objects: DeviceObject[]
  params: { path: string[]; items: ParamItem[] }[]
}

export interface GaDetail {
  address: string
  name: string
  dpt: string
  description: string
  central: boolean
  users: { uid: string; pa: string | null; device: string; ref: string; number: number | null; object: string; sending: boolean }[]
}

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(message: string, status = 0, detail: unknown = null) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

export interface MonitorRow {
  id: number; ts: number; src: string; src_name: string; dst: string; dst_name: string; dpt: string
  service: string; raw: string; value: string; known: boolean
}

export interface Orphan { ref: string; number: number; text: string; gas: string[] }

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let msg = `${res.status}`
    let detail: unknown = null
    try {
      detail = (await res.json()).detail ?? null
      if (typeof detail === 'string') msg = detail
      else if (detail && typeof detail === 'object' && 'message' in detail) msg = String((detail as { message: unknown }).message)
    } catch {
      /* keine JSON-Antwort */
    }
    throw new ApiError(msg, res.status, detail)
  }
  return res.json() as Promise<T>
}
const get = <T>(url: string) => call<T>(url)
const send = <T>(method: string, url: string, body?: unknown) =>
  call<T>(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })

export const api = {
  project: () => get<ProjectInfo>('/api/project'),
  tree: (kind: 'topology' | 'buildings' | 'groupaddresses') => get<TreeNode[]>(`/api/tree/${kind}`),
  device: (uid: string) => get<DeviceDetail>(`/api/device/${encodeURIComponent(uid)}`),
  ga: (address: string) => get<GaDetail>(`/api/ga/${address}`),
  setParam: (uid: string, id: string, value: string | null, orphans: 'ask' | 'keep' | 'unlink' = 'ask') =>
    send<DeviceDetail>('PUT', `/api/device/${encodeURIComponent(uid)}/param`, { id, value, orphans }),
  monitor: (since: number) =>
    get<{ state: string; gateway: string; own: string; error: string; rows: MonitorRow[] }>(`/api/monitor?since=${since}`),
  monitorStart: (gateway: string) => send<{ state: string }>('POST', '/api/monitor/start', { gateway }),
  monitorStop: () => send<{ state: string }>('POST', '/api/monitor/stop'),
  monitorClear: () => send<{ ok: boolean }>('POST', '/api/monitor/clear'),
  orphans: () => get<{ uid: string; pa: string | null; name: string; orphans: Orphan[] }[]>('/api/orphans'),
  unlinkOrphans: (uids: string[] | null) => send<{ unlinked: number }>('POST', '/api/orphans/unlink', { uids }),
  save: () => send<{ saved: string }>('POST', '/api/project/save'),
  op: <T = unknown>(op: string, args: Record<string, unknown>) =>
    send<{ result: T; undo: string | null }>('POST', '/api/op', { op, args }),
  undo: () => send<{ undone: string }>('POST', '/api/undo'),
  redo: () => send<{ redone: string }>('POST', '/api/redo'),
  catalog: () => get<CatalogItem[]>('/api/catalog'),
  addProducts: (file: File) =>
    call<{ products: { product_ref: string; name: string; order: string; new: boolean }[]; applications: number }>(
      `/api/products?name=${encodeURIComponent(file.name)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file },
    ),
}

export interface CatalogItem {
  product_ref: string
  h2p_ref: string
  app_id: string
  name: string
  order: string
  manufacturer: string
  application: string
}
