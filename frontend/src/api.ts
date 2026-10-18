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
    load?: LoadState
    count?: number
  }
  children?: TreeNode[]
}

export interface LoadState { adr: boolean; prg: boolean; par: boolean; grp: boolean; cfg: boolean }

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
  priority: string
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
  load: LoadState
  objects: DeviceObject[]
  params: { path: string[]; items: ParamItem[] }[]
}

export interface GaDetail {
  address: string
  name: string
  dpt: string
  description: string
  central: boolean
  users: {
    uid: string; pa: string | null; device: string; ref: string; number: number | null; object: string; sending: boolean
    dpt: string; size: string; flags: string; priority: string
  }[]
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

export interface ProjectFile { path: string; name: string; folder: string; mtime: number; current: boolean }
export interface VersionEntry {
  id: number; ts: number; comment: string
  stats: { devices: number; group_addresses: number; links: number; lines: number; spaces: number; params: number }
}
export interface VersionDiff {
  devices_added: string[]; devices_removed: string[]; devices_changed: string[]
  gas_added: string[]; gas_removed: string[]; gas_renamed: string[]
  lines: { added: string[]; removed: string[] }; spaces_changed: string[]
}

export interface DeviceCheck {
  read: boolean; last_read?: number; param_equal: number; param_diff: number
  diffs?: { segment: string; offset: number; device: number; computed: number; params: string[] }[]
  address_table: boolean | null; association_table: boolean | null
  object_table: [number, number] | null; ets_image: [number, number] | null
  simulation?: { equal: number; diff: number; error: string | null }
}
export interface DevicePlan {
  kind: string; total: number
  steps: { op: string; text: string; writes: boolean; size: number; address: number | null; blocks: number }[]
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
  files: () => get<{ folder: string; files: ProjectFile[] }>('/api/files'),
  openProject: (path: string, force = false) => send<ProjectInfo>('POST', '/api/project/open', { path, force }),
  newProject: (name: string, force = false) => send<ProjectInfo>('POST', `/api/project/new?force=${force}`, { name }),
  saveAs: (name: string) => send<ProjectInfo>('POST', '/api/project/save_as', { name }),
  importProject: (file: File, force = false) =>
    call<ProjectInfo>(`/api/project/import?name=${encodeURIComponent(file.name)}&force=${force}`,
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file }),
  versions: () => get<VersionEntry[]>('/api/versions'),
  addVersion: (comment: string) => send<{ id: number }>('POST', '/api/versions', { comment }),
  versionDiff: (id: number) => get<VersionDiff>(`/api/versions/${id}/diff`),
  restoreVersion: (id: number) => send<ProjectInfo>('POST', `/api/versions/${id}/restore`),
  deviceCheck: (uid: string) => get<DeviceCheck>(`/api/device/${encodeURIComponent(uid)}/check`),
  devicePlan: (uid: string, kind: string) => get<DevicePlan>(`/api/device/${encodeURIComponent(uid)}/plan?kind=${kind}`),
  deviceRead: (uid: string, gateway: string) => send<DeviceCheck>('POST', `/api/device/${encodeURIComponent(uid)}/read`, { gateway }),
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
