<template>
  <Dialog v-model="show" title="Project config" size="lg">
    <template #default>
      <div class="space-y-4" @pointerdown.stop>
        <div>
          <p class="mb-1.5 font-medium text-ink-gray-7 text-xs">Overlays</p>
          <div v-if="activeOverlays.length" class="flex flex-wrap gap-1.5 mb-3">
            <span v-for="ov in activeOverlays" :key="ov"
              class="inline-flex items-center gap-1 bg-surface-gray-2 px-2 py-0.5 rounded-full text-ink-gray-7 text-p-xs">
              {{ ov }}
              <button type="button" class="text-ink-gray-5 hover:text-ink-red-6 disabled:opacity-50"
                :disabled="removing === ov" :title="`Remove ${ov}`" @click="doRemove(ov)">
                <span class="size-3 lucide-x" />
              </button>
            </span>
          </div>
          <p v-else class="mb-3 text-ink-gray-4 text-xs">None active.</p>

          <div v-if="jobId" class="flex flex-col gap-3">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 text-sm">
                <Badge :theme="statusTheme" :label="statusLabel" />
                <span class="text-ink-gray-5">exit code: {{ job?.exit_code ?? '—' }}</span>
              </div>
              <Button v-if="job?.status && job.status !== 'running'" variant="ghost" size="sm" @click="resetJob">
                Done
              </Button>
            </div>
            <LogView :lines="logLines" :streaming="job?.status === 'running'" empty-text="Waiting for output…" />
          </div>
          <div v-else class="flex items-end gap-2">
            <FormControl
              v-if="availableOverlays.length"
              v-model="selectedOverlay"
              label="Add overlay"
              type="select"
              class="flex-1"
              :options="[{ label: 'Choose…', value: '' }, ...availableOverlays.map((o) => ({ label: o, value: o }))]"
            />
            <p v-else-if="!loadingOverlays" class="text-ink-gray-5 text-sm">
              No more overlays available — all of them are already active.
            </p>
            <Button variant="solid" :loading="adding" :disabled="!selectedOverlay" @click="doAdd">Apply</Button>
          </div>
        </div>

        <ErrorMessage v-if="error" :message="error" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { Badge, Button, Dialog, ErrorMessage, FormControl, toast } from 'frappe-ui'
import LogView from '@/components/logs/LogView.vue'
import { apiErrorMessage } from '@/api/client'
import { mefApi } from '@/api/mef'
import { processLine } from '@/utils/ansi'

const props = defineProps({
  modelValue: Boolean,
  project: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

// Local copy so removing/adding reflects immediately without waiting on a full
// registry reload — 'changed' still tells the parent to refresh in the background.
const activeOverlays = ref([])
const allOverlays = ref([])
const loadingOverlays = ref(false)
const availableOverlays = computed(() => allOverlays.value.filter((o) => !activeOverlays.value.includes(o)))

const selectedOverlay = ref('')
const adding = ref(false)
const removing = ref('')
const error = ref('')
const jobId = ref(null)
const job = ref(null)
let pollTimer = null

const STATUS_META = {
  running: { label: 'Running', theme: 'blue' },
  success: { label: 'Applied', theme: 'green' },
  failed: { label: 'Failed', theme: 'red' },
}
const statusLabel = computed(() => STATUS_META[job.value?.status]?.label ?? 'Queued')
const statusTheme = computed(() => STATUS_META[job.value?.status]?.theme ?? 'gray')
const logLines = computed(() => {
  const text = (job.value?.log || '').replace(/\n+$/, '')
  return text ? text.split('\n').map(processLine) : []
})

async function loadOverlays() {
  loadingOverlays.value = true
  try {
    const result = await mefApi.listOverlays()
    allOverlays.value = result.overlays || []
  } catch (caught) {
    error.value = caught.message || 'Could not load overlays.'
  } finally {
    loadingOverlays.value = false
  }
}

function stopPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

const POLL_INTERVAL_MS = 1500

async function pollJob() {
  if (!jobId.value) return
  try {
    const detail = await mefApi.getJob(jobId.value)
    job.value = detail
    if (detail.status === 'running') {
      pollTimer = setTimeout(pollJob, POLL_INTERVAL_MS)
    } else if (detail.status === 'success') {
      toast.success(`Overlay '${selectedOverlay.value}' applied`)
      activeOverlays.value = [...activeOverlays.value, selectedOverlay.value]
      selectedOverlay.value = ''
      emit('changed', props.project?.name)
    } else {
      error.value = `frappe:overlay exited with code ${detail.exit_code}. See log below.`
    }
  } catch {
    pollTimer = setTimeout(pollJob, POLL_INTERVAL_MS)
  }
}

function resetJob() {
  jobId.value = null
  job.value = null
}

async function doAdd() {
  if (!props.project || !selectedOverlay.value) return
  adding.value = true
  error.value = ''
  try {
    const result = await mefApi.addOverlay(props.project.name, selectedOverlay.value)
    if (!result.job_id) {
      error.value = apiErrorMessage(result, 'Could not start overlay task.')
      return
    }
    jobId.value = result.job_id
    pollJob()
  } catch (caught) {
    error.value = caught.message || 'Could not start overlay task.'
  } finally {
    adding.value = false
  }
}

async function doRemove(overlay) {
  if (!props.project) return
  removing.value = overlay
  error.value = ''
  try {
    const result = await mefApi.removeOverlay(props.project.name, overlay)
    if (result?.error) {
      error.value = apiErrorMessage(result, `Could not remove '${overlay}'.`)
      return
    }
    activeOverlays.value = activeOverlays.value.filter((o) => o !== overlay)
    toast.success(`Overlay '${overlay}' removed — already-applied file changes stay until a fresh setup`)
    emit('changed', props.project?.name)
  } catch (caught) {
    error.value = caught.message || `Could not remove '${overlay}'.`
  } finally {
    removing.value = ''
  }
}

watch(show, (visible) => {
  if (visible) {
    activeOverlays.value = [...(props.project?.overlays || [])]
    selectedOverlay.value = ''
    error.value = ''
    resetJob()
    loadOverlays()
  } else {
    stopPoll()
  }
})

onBeforeUnmount(() => {
  stopPoll()
})
</script>
