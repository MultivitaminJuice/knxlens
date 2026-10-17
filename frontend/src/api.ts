// Typen und Aufrufe der Editor-API (knxlens/editor). Die Logik bleibt im Python-Kern.

export interface TreeNode {
  key: string
  label: string
  icon?: string
  leaf?: boolean
  data: { type: string; uid?: string; address?: string; dpt?: string; medium?: string }
  children?: TreeNode[]
}

export interface ProjectInfo {
  name: string
  file: string
  project_id: string
  devices: number
  group_addresses: number
  links: number
}

export interface DeviceObject {
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
  changed: boolean
  from_project: boolean
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
  users: { uid: string; pa: string | null; device: string; number: number | null; object: string; sending: boolean }[]
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

export const api = {
  project: () => get<ProjectInfo>('/api/project'),
  tree: (kind: 'topology' | 'buildings' | 'groupaddresses') => get<TreeNode[]>(`/api/tree/${kind}`),
  device: (uid: string) => get<DeviceDetail>(`/api/device/${encodeURIComponent(uid)}`),
  ga: (address: string) => get<GaDetail>(`/api/ga/${address}`),
}
