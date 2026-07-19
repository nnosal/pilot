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
import { mefApi } from '@/api/mef'

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

async function openAdmin(project) {
  if (project.is_self) return

  try {
    if (project.pilot_running) {
      // Pilot running: open with auto-login using mise r pilot:open
      await mefApi.pilotOpen(project.name)
    } else {
      // Pilot stopped: start it first
      await mefApi.pilotUp(project.name)
      // Wait a moment for pilot to start, then open
      await new Promise(resolve => setTimeout(resolve, 2000))
      await mefApi.pilotOpen(project.name)
    }
  } catch (error) {
    console.error('Failed to open project admin:', error)
  }
}

onMounted(load)
</script>
