<template>
  <Dialog v-model="show" title="mef projects" size="3xl" :show-close-button="true">
    <template #default>
      <div class="flex flex-col gap-3" @pointerdown.stop>
        <!-- Toolbar -->
        <div class="flex justify-between items-center gap-2">
          <p class="text-ink-gray-5 text-xs truncate">
            {{ mefRoot || 'mef root unavailable' }}
          </p>
          <div class="flex items-center gap-1">
            <Button variant="ghost" size="sm" :loading="loading" @click="load" title="Refresh">
              <template #prefix>
                <span class="size-4 lucide-refresh-cw" />
              </template>
            </Button>
            <Button variant="subtle" size="sm" @click="$emit('new-project')">
              <template #prefix>
                <span class="size-4 lucide-plus" />
              </template>
              New project
            </Button>
          </div>
        </div>

        <ErrorMessage v-if="error" :message="error" />

        <div v-if="loading && !projects.length" class="py-10 text-ink-gray-5 text-sm text-center">
          Loading…
        </div>
        <div v-else-if="!projects.length" class="py-10 text-ink-gray-4 text-sm text-center">
          No mef projects found.
        </div>

        <ListView v-else :columns="columns" :rows="rows" row-key="name"
          :options="{ selectable: false, showTooltip: false, rowHeight: 52 }">
          <template #cell="{ column, row }">
            <div v-if="column.key === 'name'" class="flex items-center gap-2 min-w-0">
              <span class="font-medium text-ink-gray-9 text-sm truncate">{{ row.name }}</span>
              <Badge v-if="row.project.is_self" label="this admin" theme="green" size="sm" />
            </div>

            <div v-else-if="column.key === 'engine'" class="text-ink-gray-6 text-sm">
              {{ row.project.env?.DB_ENGINE || '—' }}
            </div>

            <div v-else-if="column.key === 'ports'" class="text-ink-gray-6 text-sm font-mono">
              <span v-if="row.project.env?.WEB_PORT">web {{ row.project.env.WEB_PORT }}</span>
              <span v-if="row.project.env?.DB_PORT" class="ml-2">db {{ row.project.env.DB_PORT }}</span>
              <span v-if="!row.project.env?.WEB_PORT && !row.project.env?.DB_PORT">—</span>
            </div>

            <div v-else-if="column.key === 'actions'" class="flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                theme="red"
                :disabled="row.project.is_self"
                :title="row.project.is_self ? 'Cannot delete the project hosting this admin' : `Delete ${row.name}`"
                @click="confirmDelete(row.project)"
              >
                <template #prefix>
                  <span class="size-4 lucide-trash-2" />
                </template>
              </Button>
            </div>
          </template>
        </ListView>
      </div>
    </template>
  </Dialog>

  <DeleteProjectDialog v-model="showDeleteDialog" :project="deleteTarget" @deleted="onProjectDeleted" />
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Badge, Button, Dialog, ErrorMessage, ListView } from 'frappe-ui'
import DeleteProjectDialog from '@/components/mef/DeleteProjectDialog.vue'
import { mefApi } from '@/api/mef'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'new-project', 'deleted'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const projects = ref([])
const mefRoot = ref('')
const loading = ref(false)
const error = ref('')

const columns = [
  { label: 'Project', key: 'name', align: 'left', width: 2.5 },
  { label: 'Engine', key: 'engine', align: 'left', width: 1 },
  { label: 'Ports', key: 'ports', align: 'left', width: 1.5 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const rows = computed(() =>
  projects.value.map((project) => ({ name: project.name, project })),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await mefApi.listProjects()
    projects.value = data.projects || []
    mefRoot.value = data.mef_root || ''
  } catch (caught) {
    error.value = caught.message || 'Could not load projects.'
    projects.value = []
  } finally {
    loading.value = false
  }
}

watch(show, (visible) => {
  if (!visible) return
  // Refresh on each open: sibling projects may have been added/removed
  // since the dialog was last shown.
  load()
})

// ----- Delete flow -----
const deleteTarget = ref(null)
const showDeleteDialog = computed({
  get: () => deleteTarget.value !== null,
  set: (val) => {
    if (!val) deleteTarget.value = null
  },
})

function confirmDelete(project) {
  if (project.is_self) return
  deleteTarget.value = project
}

function onProjectDeleted(name) {
  emit('deleted', name)
  projects.value = projects.value.filter((p) => p.name !== name)
}
</script>
