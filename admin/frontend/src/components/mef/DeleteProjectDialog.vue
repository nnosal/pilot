<template>
  <Dialog v-model="show" title="Delete mef project" size="lg">
    <template #default>
      <div v-if="deleteJobId" class="flex flex-col gap-3" @pointerdown.stop>
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 text-sm">
            <Badge :theme="deleteStatusTheme" :label="deleteStatusLabel" />
            <span v-if="deleteCurrentStep" class="text-ink-gray-5 font-mono">{{ deleteCurrentStep }}</span>
            <span class="text-ink-gray-5">exit code: {{ deleteJob?.exit_code ?? '—' }}</span>
          </div>
          <Button variant="ghost" size="sm" @click="show = false">Close</Button>
        </div>
        <ErrorMessage v-if="deleteError" :message="deleteError" />
        <LogView
          :lines="deleteLogLines"
          :streaming="deleteJob?.status === 'running'"
          empty-text="Waiting for output…"
        />
      </div>

      <div v-else class="space-y-4" @pointerdown.stop>
        <p class="text-ink-gray-7 text-sm leading-relaxed">
          Permanently delete
          <strong class="text-ink-gray-9">{{ project?.name }}</strong>?
          This runs <code class="font-mono text-ink-gray-7">mise r delete</code>:
          bench, databases, redis and the project directory are wiped. Cannot be undone.
        </p>
        <FormControl
          v-model="typedName"
          :label="`Type the project name to confirm`"
          type="text"
          :placeholder="project?.name"
        />
        <ErrorMessage v-if="deleteError" :message="deleteError" />
        <div class="flex justify-end gap-2">
          <Button variant="subtle" @click="show = false">Cancel</Button>
          <Button
            variant="solid"
            theme="red"
            :loading="deleting"
            :disabled="typedName !== project?.name"
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
import { Badge, Button, Dialog, ErrorMessage, FormControl, toast } from 'frappe-ui'
import LogView from '@/components/logs/LogView.vue'
import { apiErrorMessage } from '@/api/client'
import { mefApi } from '@/api/mef'
import { processLine } from '@/utils/ansi'

const props = defineProps({
  modelValue: Boolean,
  project: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'deleted'])

const show = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const typedName = ref('')
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
// Every mise task pipeline prints "[task] ERROR task failed" on the way out
// (see .config/mise/tasks/delete). When polling loses the job the exit_code
// is gone, but this marker in the last log we did receive is still reliable.
const deleteLogIndicatesFailure = computed(() => /\]\s+ERROR task failed\s*$/m.test(deleteJob.value?.log || ''))
const deleteStatusLabel = computed(() => {
  if (STATUS_META[deleteJob.value?.status]) return STATUS_META[deleteJob.value.status].label
  if (deleteLogIndicatesFailure.value) return 'Failed (from log)'
  return deleteError.value ? 'Unknown' : 'Queued'
})
const deleteStatusTheme = computed(() => {
  if (STATUS_META[deleteJob.value?.status]) return STATUS_META[deleteJob.value.status].theme
  return deleteLogIndicatesFailure.value ? 'red' : 'gray'
})
const deleteLogLines = computed(() => {
  const text = (deleteJob.value?.log || '').replace(/\n+$/, '')
  return text ? text.split('\n').map(processLine) : []
})

const STEP_RE = /^\[([\w:.-]+)\]/gm
const deleteCurrentStep = computed(() => {
  const text = deleteJob.value?.log || ''
  STEP_RE.lastIndex = 0
  let last = ''
  let match
  while ((match = STEP_RE.exec(text))) last = match[1]
  return last
})

const POLL_INTERVAL_MS = 1500
// A poll error (session expired mid-job, backend blip) gets a few retries in
// case it's transient, then surfaces instead of silently going quiet forever.
const MAX_POLL_ERRORS = 5
let deletePollErrorCount = 0

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
    if (detail?.error) {
      deletePollErrorCount += 1
      if (deletePollErrorCount <= MAX_POLL_ERRORS) {
        deletePollTimer = setTimeout(pollDeleteJob, POLL_INTERVAL_MS)
        return
      }
      stopDeletePoll()
      deleteError.value = deleteLogIndicatesFailure.value
        ? `${apiErrorMessage(detail, 'Lost track of the job.')} The log shows it already failed — see below.`
        : `${apiErrorMessage(detail, 'Lost track of the job.')} Reload the page — the operation may still be running in the background.`
      return
    }
    deletePollErrorCount = 0
    deleteJob.value = detail
    if (detail.status === 'running') {
      deletePollTimer = setTimeout(pollDeleteJob, POLL_INTERVAL_MS)
    } else {
      stopDeletePoll()
      if (detail.status === 'success') {
        toast.success(`Deleted ${props.project?.name}`)
        emit('deleted', props.project?.name)
        show.value = false
      } else {
        deleteError.value = `mise r delete exited with code ${detail.exit_code}. See log below.`
      }
    }
  } catch {
    deletePollTimer = setTimeout(pollDeleteJob, POLL_INTERVAL_MS)
  }
}

async function doDelete() {
  if (!props.project || typedName.value !== props.project.name) return
  deleting.value = true
  deleteError.value = ''
  try {
    const result = await mefApi.deleteProject(props.project.name)
    if (!result.job_id) {
      deleteError.value = apiErrorMessage(result, 'Could not start deletion.')
      return
    }
    deleteJobId.value = result.job_id
    deletePollErrorCount = 0
    pollDeleteJob()
  } catch (caught) {
    deleteError.value = caught.message || 'Could not start deletion.'
  } finally {
    deleting.value = false
  }
}

watch(show, (visible) => {
  if (!visible) {
    typedName.value = ''
    deleteError.value = ''
    deleteJobId.value = null
    deleteJob.value = null
    stopDeletePoll()
  }
})

onBeforeUnmount(() => {
  stopDeletePoll()
})
</script>
