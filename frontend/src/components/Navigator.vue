<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NInput, NTabPane, NTabs, NTree, type TreeOption } from 'naive-ui'
import { api, type TreeNode } from '../api'

export type Selection = { type: 'device'; uid: string } | { type: 'ga'; address: string }

const emit = defineEmits<{ select: [Selection] }>()

const kinds = [
  { value: 'topology', label: 'Topologie' },
  { value: 'buildings', label: 'Gebäude' },
  { value: 'groupaddresses', label: 'Gruppenadressen' },
] as const

const trees = ref<Record<string, TreeOption[]>>({})
const expanded = ref<Record<string, string[]>>({})
const pattern = ref('')
const error = ref('')

// API-Knoten → Naive-Baumknoten (isLeaf statt leaf)
function convert(nodes: TreeNode[]): TreeOption[] {
  return nodes.map((n) => ({
    key: n.key,
    label: n.label,
    isLeaf: n.leaf ?? !n.children?.length,
    meta: n.data,
    children: n.children?.length ? convert(n.children) : undefined,
  }))
}

function topKeys(nodes: TreeOption[], depth: number, out: string[] = []): string[] {
  for (const n of nodes) {
    if (depth > 0 && n.children?.length) {
      out.push(String(n.key))
      topKeys(n.children, depth - 1, out)
    }
  }
  return out
}

onMounted(async () => {
  try {
    for (const k of kinds) {
      const data = convert(await api.tree(k.value))
      trees.value[k.value] = data
      expanded.value[k.value] = topKeys(data, k.value === 'groupaddresses' ? 1 : 2)
    }
  } catch (e) {
    error.value = String(e)
  }
})

function onSelect(_keys: Array<string | number>, options: Array<TreeOption | null>) {
  const meta = options[0]?.meta as TreeNode['data'] | undefined
  if (meta?.type === 'device' && meta.uid) emit('select', { type: 'device', uid: meta.uid })
  if (meta?.type === 'ga' && meta.address) emit('select', { type: 'ga', address: meta.address })
}
</script>

<template>
  <div class="nav">
    <p v-if="error" class="error">{{ error }}</p>
    <div class="filter"><NInput v-model:value="pattern" size="small" clearable placeholder="Filtern …" /></div>
    <NTabs type="line" size="small" animated class="tabs" pane-class="pane">
      <NTabPane v-for="k in kinds" :key="k.value" :name="k.value" :tab="k.label" display-directive="show">
        <NTree
          :data="trees[k.value] || []"
          v-model:expanded-keys="expanded[k.value]"
          :pattern="pattern"
          :show-irrelevant-nodes="false"
          block-line
          selectable
          virtual-scroll
          class="tree"
          :node-props="({ option }: { option: TreeOption }) => ({ title: String(option.label) })"
          @update:selected-keys="onSelect"
        />
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.nav { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.filter { padding: 8px 8px 0; }
.tabs { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 8px; }
.tabs :deep(.n-tabs-pane-wrapper), .tabs :deep(.pane) { flex: 1; min-height: 0; height: 100%; }
.tree { height: 100%; font-size: 13px; }
.tree :deep(.n-tree-node-content__text) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tree :deep(.n-tree-node-content) { min-width: 0; }
.error { color: #d03050; padding: 8px; }
</style>
