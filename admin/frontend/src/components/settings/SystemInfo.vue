<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">System</p>
      <div class="divide-y divide-outline-gray-1">
        <div v-for="(value, label) in systemRows" :key="label" class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">{{ label }}</span>
          <span class="text-ink-gray-9 text-sm">{{ value }}</span>
        </div>
      </div>
    </div>

    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">Runtime</p>
      <div class="divide-y divide-outline-gray-1">
        <div v-for="(value, label) in info.runtime" :key="label" class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">{{ label }}</span>
          <a v-if="label === 'Pilot'" :href="`https://github.com/frappe/pilot/commit/${value}`" target="_blank"
            rel="noopener noreferrer" class="text-ink-gray-9 text-sm hover:underline">{{ value }}</a>
          <span v-else class="text-ink-gray-9 text-sm">{{ value }}</span>
        </div>
        <p v-if="!Object.keys(info.runtime).length" class="py-2.5 text-ink-gray-5 text-sm">
          No runtime versions detected.
        </p>
      </div>
    </div>

    <div v-if="mefRows">
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">mef project</p>
      <div class="divide-y divide-outline-gray-1">
        <div v-for="(value, label) in mefRows" :key="label" class="flex justify-between items-center py-2.5">
          <span class="text-ink-gray-7 text-sm">{{ label }}</span>
          <span class="text-ink-gray-9 text-sm font-mono">{{ value }}</span>
        </div>
      </div>
      <p v-if="info.mef.FRAPPE_OVERLAYS" class="mt-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
        Overlays are active: app installs, updates and migrate from this UI would overwrite
        their patches. Use <span class="font-mono">mise r apps:sync</span> from the project folder instead.
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { monitorApi } from '@/api/monitor'
import { formatBytes } from '@/utils/format'

const loading = ref(true)
const info = ref({ disk_total: 0, runtime: {} })

const systemRows = computed(() => {
  const rows = {
    OS: info.value.os_version || '',
    Kernel: info.value.kernel_version || '',
    vCPUs: info.value.cpu_count || '',
    RAM: info.value.memory_total ? formatBytes(info.value.memory_total) : '',
    Swap: info.value.swap_total ? formatBytes(info.value.swap_total) : '',
    'Disk size': info.value.disk_total ? formatBytes(info.value.disk_total) : '',
  }
  return Object.fromEntries(Object.entries(rows).filter(([, value]) => value))
})

const mefRows = computed(() => {
  const mef = info.value.mef
  if (!mef) return null
  const rows = {
    Project: mef.project_dir || '',
    'DB engine': mef.DB_ENGINE || 'mariadb',
    Overlays: mef.FRAPPE_OVERLAYS || 'none',
    'Site domain': mef.SITE_DOMAIN || '',
    'Web port': mef.WEB_PORT || '',
    'DB port': mef.DB_PORT || '',
    'Redis port': mef.REDIS_PORT || '',
  }
  return Object.fromEntries(Object.entries(rows).filter(([, value]) => value))
})

onMounted(async () => {
  try {
    info.value = await monitorApi.systemInfo()
  } finally {
    loading.value = false
  }
})
</script>
