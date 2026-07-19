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

  <!-- Delete confirmation sub-dialog: type-the-name + log streaming -->
  <Dialog v-model="showDelete" title="Delete mef project" size="lg">
    <template #default>
      <div v-if="deleteJobId" class="flex flex-col gap-3" @pointerdown.stop>
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-sm">
            <Badge :theme="deleteStatusTheme" :label="deleteStatusLabel" />
            <span class="text-ink-gray-5">exit code: {{ deleteJob?.exit_code ?? '—' }}</span>
          </div>
          <Button variant="ghost" size="sm" @click="onDeleteClose">Close</Button>
        </div>
        <LogView
          :lines="deleteLogLines"
          :streaming="deleteJob?.status === 'running'"
          empty-text="Waiting for output…"
        />
      </div>

      <div v-else class="space-y-4" @pointerdown.stop>
        <p class="text-ink-gray-7 text-sm leading-relaxed">
          Permanently delete
          <strong class="text-ink-gray-9">{{ toDelete?.name }}</strong>?
          This runs <code class="font-mono text-ink-gray-7">mise r delete</code>:
          bench, databases, redis and the project directory are wiped. Cannot be undone.
        </p>
        <FormControl
          v-model="typedName"
          :label="`Type the project name to confirm`"
          type="text"
          :placeholder="toDelete?.name"
        />
        <ErrorMessage v-if="deleteError" :message="deleteError" />
        <div class="flex justify-end gap-2">
          <Button variant="subtle" @click="showDelete = false">Cancel</Button>
          <Button
            variant="solid"
            theme="red"
            :loading="deleting"
            :disabled="typedName !== toDelete?.name"
            @click="doDelete"
          >
            Delete project
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { Badge, Button, Dialog, ErrorMessage, FormControl, ListView, toast } from 'frappe-ui'
import LogView from '@/components/logs/LogView.vue'
import { apiErrorMessage } from '@/api/client'
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
const toDelete = ref(null)
const typedName = ref('')
const showDelete = computed({
  get: () => toDelete.value !== null,
  set: (val) => {
    if (!val) {
      toDelete.value = null
      typedName.value = ''
      deleteError.value = ''
      deleteJobId.value = null
      deleteJob.value = null
      stopDeletePoll()
    }
  },
})
const deleting = ref(false)
const deleteError = ref('')
const deleteJobId = ref(null)
const deleteJob = ref(null)
let deletePollTimer = null

const STATUS_META = {
  running: { label: 'Running', theme: 'blue' },
  success: { label: 'Deleted', theme: 'green' },
  failed: { label: 'Failed', theme: 'red' },
}
const deleteStatusLabel = computed(() => STATUS_META[deleteJob.value?.status]?.label || 'Queued')
const deleteStatusTheme = computed(() => STATUS_META[deleteJob.value?.status]?.theme || 'gray')
const deleteLogLines = computed(() => {
  const text = (deleteJob.value?.log || '').replace(/\n+$/, '')
  return text ? text.split('\n') : []
})

function confirmDelete(project) {
  if (project.is_self) return
  toDelete.value = project
  typedName.value = ''
  deleteError.value = ''
  deleteJobId.value = null
  deleteJob.value = null
}

const POLL_INTERVAL_MS = 1500

function stopDeletePoll() {
  if (deletePollTimer) {
    clearTimeout(deletePollTimer)
    deletePollTimer = null
  }
}

async function pollDeleteJob() {
  if (!deleteJobId.value) return
  try {
    const detail = await mefApi.getJob(deleteJobId.value)
    deleteJob.value = detail
    if (detail.status === 'running') {
      deletePollTimer = setTimeout(pollDeleteJob, POLL_INTERVAL_MS)
    } else {
      stopDeletePoll()
      if (detail.status === 'success') {
        toast.success(`Deleted ${toDelete.value?.name}`)
        emit('deleted', toDelete.value?.name)
        // Drop the row locally + close the sub-dialog.
        const name = toDelete.value?.name
        projects.value = projects.value.filter((p) => p.name !== name)
        toDelete.value = null
        typedName.value = ''
      } else {
        deleteError.value = `mise r delete exited with code ${detail.exit_code}. See log below.`
      }
    }
  } catch {
    deletePollTimer = setTimeout(pollDeleteJob, POLL_INTERVAL_MS)
  }
}

async function doDelete() {
  const project = toDelete.value
  if (!project || typedName.value !== project.name) return
  deleting.value = true
  deleteError.value = ''
  try {
    const result = await mefApi.deleteProject(project.name)
    if (!result.job_id) {
      deleteError.value = apiErrorMessage(result, 'Could not start deletion.')
      return
    }
    deleteJobId.value = result.job_id
    pollDeleteJob()
  } catch (caught) {
    deleteError.value = caught.message || 'Could not start deletion.'
  } finally {
    deleting.value = false
  }
}

function onDeleteClose() {
  showDelete.value = false
}

onBeforeUnmount(() => {
  stopDeletePoll()
})
</script>
