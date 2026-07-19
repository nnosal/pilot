<template>
  <UpdatesAvailableButton />

  <div class="mx-auto max-w-6xl">
    <!-- Header -->
    <div class="flex justify-between items-start gap-3">
      <div class="min-w-0">
        <h1 class="font-medium text-ink-gray-8 text-base">Registry</h1>
        <p class="mt-1 text-ink-gray-5 text-xs truncate font-mono">
          {{ mefRoot || 'mef root unavailable' }}
        </p>
      </div>
      <Button variant="ghost" size="sm" :loading="loading" @click="load" title="Refresh">
        <template #prefix>
          <span class="size-4 lucide-refresh-cw" />
        </template>
      </Button>
    </div>

    <ErrorMessage v-if="error" :message="error" class="mt-4" />

    <!-- Port collision warning -->
    <div v-if="portCollisions.size" class="mt-4 bg-surface-amber-2 px-3 py-2 rounded border border-outline-amber-3">
      <div class="flex items-start gap-2">
        <span class="size-4 text-ink-amber-8 lucide-triangle-alert shrink-0" />
        <div class="text-sm">
          <p class="font-medium text-ink-amber-9">Port collision detected</p>
          <p class="text-ink-amber-8 mt-0.5">
            {{ portCollisions.size }} project{{ portCollisions.size > 1 ? 's' : '' }} share the same pilot port.
            "Open admin" may land on the wrong project.
          </p>
        </div>
      </div>
    </div>

    <div v-if="loading && !projects.length" class="mt-16 flex justify-center">
      <LoadingText />
    </div>
    <div v-else-if="!projects.length" class="mt-16 text-ink-gray-5 text-sm text-center">
      No mef projects found.
    </div>

    <ListView
      v-else
      :columns="columns"
      :rows="rows"
      row-key="name"
      class="mt-4"
      :options="{ selectable: false, showTooltip: false, rowHeight: 52 }"
    >
      <template #cell="{ column, row }">
        <!-- Project name + self badge -->
        <div v-if="column.key === 'name'" class="flex items-center gap-2 min-w-0">
          <span class="font-medium text-ink-gray-9 text-sm truncate">{{ row.name }}</span>
          <Badge v-if="row.project.is_self" label="this admin" theme="green" size="sm" />
        </div>

        <!-- Pilot running badge -->
        <div v-else-if="column.key === 'pilot'" class="flex justify-center">
          <Badge
            :theme="pilotTheme(row.project)"
            :label="pilotLabel(row.project)"
            variant="subtle"
            size="sm"
          />
        </div>

        <!-- Numeric counts -->
        <div v-else-if="column.key === 'sites'" class="text-ink-gray-6 text-sm tabular-nums">
          {{ row.project.sites_count ?? 0 }}
        </div>
        <div v-else-if="column.key === 'apps'" class="text-ink-gray-6 text-sm tabular-nums">
          {{ row.project.apps_count ?? 0 }}
        </div>

        <!-- Profile + version -->
        <div v-else-if="column.key === 'profile'" class="text-ink-gray-6 text-sm">
          <span v-if="row.project.profile">{{ row.project.profile }}</span>
          <span v-else class="text-ink-gray-4">—</span>
        </div>
        <div v-else-if="column.key === 'version'" class="text-ink-gray-6 text-sm">
          <span v-if="row.project.frappe_version">{{ row.project.frappe_version }}</span>
          <span v-else class="text-ink-gray-4">—</span>
        </div>

        <!-- Ports -->
        <div v-else-if="column.key === 'ports'" class="text-ink-gray-6 text-sm font-mono">
          <span v-if="row.project.pilot_port" class="inline-flex items-center gap-1">
            <span class="text-ink-gray-4">admin</span>
            <span>{{ row.project.pilot_port }}</span>
          </span>
          <span v-if="row.project.ports?.web" class="ml-2 inline-flex items-center gap-1">
            <span class="text-ink-gray-4">web</span>
            <span>{{ row.project.ports.web }}</span>
          </span>
          <span v-if="!row.project.pilot_port && !row.project.ports?.web" class="text-ink-gray-4">—</span>
        </div>

        <!-- Actions -->
        <div v-else-if="column.key === 'actions'" class="flex justify-end items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            :title="`Open admin :${row.project.pilot_port}`"
            :disabled="!row.project.pilot_port"
            @click="openAdmin(row.project)"
          >
            <template #prefix>
              <span class="size-4 lucide-external-link" />
            </template>
          </Button>
          <span v-if="controlLoading === row.name" class="flex justify-center items-center w-7 h-7">
            <span class="size-4 lucide-loader-2 text-ink-gray-5 animate-spin" />
          </span>
          <Button
            v-else-if="!row.project.pilot_running && !row.project.is_self"
            variant="ghost"
            size="sm"
            :title="`Start pilot :${row.project.pilot_port}`"
            @click="onStart(row.project)"
          >
            <template #prefix>
              <span class="size-4 lucide-play" />
            </template>
          </Button>
          <Button
            v-else-if="row.project.pilot_running && !row.project.is_self"
            variant="ghost"
            size="sm"
            theme="red"
            :title="`Stop pilot :${row.project.pilot_port}`"
            @click="onStop(row.project)"
          >
            <template #prefix>
              <span class="size-4 lucide-square" />
            </template>
          </Button>
          <!-- Self row: no start/stop (would kill this admin) -->
          <span
            v-else-if="row.project.is_self"
            class="text-ink-gray-4 text-xs px-1"
            title="This is the admin you are using"
          >
            —
          </span>
        </div>
      </template>
    </ListView>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { Badge, Button, ErrorMessage, ListView, LoadingText, toast } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/common/UpdatesAvailableButton.vue'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useRegistry } from '@/composables/mef/useRegistry'

const { setBreadcrumbs } = useBreadcrumbs()
const { projects, mefRoot, loading, controlLoading, error, load, startPilot, stopPilot } =
  useRegistry()

setBreadcrumbs([{ label: 'Registry', route: { name: 'Registry' } }])

const columns = [
  { label: 'Project', key: 'name', align: 'left', width: 2.5 },
  { label: 'Pilot', key: 'pilot', align: 'center', width: 1.2 },
  { label: 'Sites', key: 'sites', align: 'left', width: '4rem' },
  { label: 'Apps', key: 'apps', align: 'left', width: '4rem' },
  { label: 'Profile', key: 'profile', align: 'left', width: 1 },
  { label: 'Frappe', key: 'version', align: 'left', width: 1 },
  { label: 'Ports', key: 'ports', align: 'left', width: 2 },
  { label: '', key: 'actions', align: 'right', width: '6rem' },
]

const rows = computed(() => projects.value.map((project) => ({ name: project.name, project })))

const PILOT_STATUS = {
  running: { label: 'Running', theme: 'green' },
  stopped: { label: 'Stopped', theme: 'gray' },
}
const pilotStatus = (project) => (project.pilot_running ? 'running' : 'stopped')
const pilotLabel = (project) => PILOT_STATUS[pilotStatus(project)].label
const pilotTheme = (project) => PILOT_STATUS[pilotStatus(project)].theme

function openAdmin(project) {
  if (!project.pilot_port) return
  // JWT sid cookie is per-origin, so the new tab lands on the target admin's
  // login page rather than carrying this session over. Auto-login via
  // /api/v1/auto-login-token is a future enhancement.
  // Hardcode http:// for localhost (dev-only) to avoid mixed-content issues.
  window.open(
    `http://localhost:${project.pilot_port}`,
    '_blank',
    'noopener',
  )
}

async function onStart(project) {
  const ok = await startPilot(project.name)
  if (ok) toast.success(`Starting pilot for ${project.name}`)
}

async function onStop(project) {
  const ok = await stopPilot(project.name)
  if (ok) toast.success(`Stopping pilot for ${project.name}`)
}

onMounted(load)
</script>
