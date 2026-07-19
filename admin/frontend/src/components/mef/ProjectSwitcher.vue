<template>
  <Dropdown v-if="projects.length" :options="options" placement="bottom-end">
    <template #default="{ open }">
      <Button variant="outline" size="sm" :active="open" :loading="loading">
        <template #prefix>
          <span class="size-4 lucide-folder-tree" />
        </template>
        <span class="truncate max-w-[12ch]">{{ currentLabel }}</span>
        <template #suffix>
          <span class="size-4 lucide-chevron-down" />
        </template>
      </Button>
    </template>
  </Dropdown>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { Button, Dropdown } from 'frappe-ui'
import { useRegistry } from '@/composables/mef/useRegistry'

const { projects, loading, load } = useRegistry()

const current = computed(() => projects.value.find((p) => p.is_self))
const currentLabel = computed(() => current.value?.name || 'Projects')

// Each option opens the target project's admin in a new tab. The "current"
// row is surfaced but disabled so the operator can see where they are without
// being able to re-open this same admin.
const options = computed(() =>
  projects.value.map((project) => ({
    label: project.name,
    disabled: project.is_self,
    selected: project.is_self,
    description: project.is_self
      ? 'current'
      : project.pilot_running
        ? 'pilot running'
        : 'pilot stopped',
    onClick: () => openAdmin(project),
  })),
)

function openAdmin(project) {
  if (!project.pilot_port || project.is_self) return
  // Cross-origin: the JWT cookie does not carry over, so the new tab lands on
  // the target admin's login page. Auto-login via /api/v1/auto-login-token is
  // a future enhancement.
  // Hardcode http:// for localhost (dev-only) to avoid mixed-content issues.
  window.open(
    `http://localhost:${project.pilot_port}`,
    '_blank',
    'noopener',
  )
}

onMounted(load)
</script>
