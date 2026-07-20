<template>
  <UpdatesAvailableButton />

  <div class="mx-auto max-w-4xl">
    <!-- Header -->
    <div class="flex justify-between items-start gap-3">
      <div class="min-w-0">
        <h1 class="font-medium text-ink-gray-8 text-base">Projects</h1>
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

    <Teleport v-if="session.allowMefManagement" defer to="#header-actions">
      <Button variant="solid" @click="showNewProject = true">
        <template #prefix>
          <span class="size-4 lucide-plus" />
        </template>
        New project
      </Button>
    </Teleport>

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

    <div v-else class="mt-4 border border-outline-gray-2 rounded-lg divide-y divide-outline-gray-2 overflow-hidden">
      <div v-for="project in projects" :key="project.name">
        <!-- Collapsed row -->
        <button
          type="button"
          class="flex items-center gap-2 hover:bg-surface-gray-1 px-3 py-2.5 w-full text-left"
          @click="toggleExpand(project.name)"
        >
          <span
            class="size-4 shrink-0 text-ink-gray-4"
            :class="isExpanded(project.name) ? 'lucide-chevron-down' : 'lucide-chevron-right'"
          />
          <span class="font-medium text-ink-gray-9 text-sm truncate">{{ project.name }}</span>
          <Badge v-if="project.is_self" label="this admin" theme="green" size="sm" />
          <Badge v-if="project.profile" :label="`mise: ${project.profile}`" theme="blue" variant="subtle" size="sm" />
          <Badge
            v-if="project.frappe_version"
            :label="`branch: ${project.frappe_version}`"
            theme="violet"
            variant="subtle"
            size="sm"
          />
          <span class="flex items-center gap-1.5" @click.stop>
            <Badge label="Dev" theme="amber" variant="subtle" size="sm" />
            <Switch :model-value="false" disabled title="Production pipeline not implemented yet" />
            <span class="text-ink-gray-4 text-xs">Prod</span>
          </span>
          <Badge
            class="ml-auto"
            :theme="pilotTheme(project)"
            :label="pilotLabel(project)"
            variant="subtle"
            size="sm"
          />
          <Button
            variant="ghost"
            size="sm"
            :title="`Open admin :${project.pilot_port}`"
            :disabled="!project.pilot_port"
            @click.stop="openAdmin(project)"
          >
            <template #prefix>
              <span class="size-4 lucide-external-link" />
            </template>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            theme="red"
            :disabled="project.is_self"
            :title="project.is_self ? 'Cannot delete the project hosting this admin' : `Delete ${project.name}`"
            @click.stop="confirmDelete(project)"
          >
            <template #prefix>
              <span class="size-4 lucide-trash-2" />
            </template>
          </Button>
        </button>

        <!-- Expanded detail -->
        <div v-if="isExpanded(project.name)" class="bg-surface-gray-1 px-3 py-3 border-t border-outline-gray-2">
          <!-- Services -->
          <div class="space-y-1 mt-3">
            <ServiceRow
              v-for="service in SERVICES"
              :key="service.key"
              :label="service.label"
              :status="serviceStatus(project, service.key)"
              :loading="controlLoading === `${project.name}:${service.key}`"
              :can-control="service.key !== 'pilot' || !project.is_self"
              :can-open="service.canOpen"
              @start="startService(project.name, service.key)"
              @stop="stopService(project.name, service.key)"
              @open="openService(project, service.key)"
            >
              <template v-if="service.key === 'mailpit'" #extra>
                <Button
                  variant="ghost"
                  size="sm"
                  :loading="jobLoading === `${project.name}:mailpit-test`"
                  :disabled="serviceStatus(project, 'mailpit') !== 'running'"
                  title="Send a test email (frappe.sendmail -> mailpit)"
                  @click="testMailpit(project)"
                >
                  <template #prefix>
                    <span class="size-4 lucide-send" />
                  </template>
                </Button>
              </template>
            </ServiceRow>
          </div>

          <!-- Sites -->
          <div class="mt-4">
            <p class="mb-1.5 font-medium text-ink-gray-7 text-xs">Sites</p>
            <div v-if="projectDetails[project.name]?.loading" class="text-ink-gray-4 text-xs">Loading…</div>
            <div v-else-if="!projectDetails[project.name]?.sites?.length" class="text-ink-gray-4 text-xs">
              No sites.
            </div>
            <div v-else class="space-y-1">
              <div
                v-for="site in projectDetails[project.name].sites"
                :key="site.name"
                class="bg-surface-white px-2.5 py-1.5 rounded border border-outline-gray-1"
              >
                <div class="flex items-center gap-2">
                  <span class="text-ink-gray-8 text-sm truncate">{{ site.name }}</span>
                  <Badge
                    class="ml-auto"
                    :theme="siteStatusTheme(site)"
                    :label="siteStatusLabel(site)"
                    variant="subtle"
                    size="sm"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    :disabled="!project.ports?.web"
                    title="Open site"
                    @click="openSite(project, site)"
                  >
                    <template #prefix>
                      <span class="size-4 lucide-external-link" />
                    </template>
                  </Button>
                </div>

                <!-- Setup wizard not completed yet -->
                <div
                  v-if="projectDetails[project.name]?.siteSetup?.[site.name] === false"
                  class="flex items-center gap-2 bg-surface-amber-2 mt-1.5 px-2.5 py-1.5 rounded border border-outline-amber-3"
                >
                  <span class="size-4 text-ink-amber-8 lucide-triangle-alert shrink-0" />
                  <span class="text-ink-amber-9 text-xs">Setup wizard not completed yet.</span>
                  <Button
                    variant="solid"
                    size="sm"
                    class="ml-auto"
                    :loading="jobLoading === `${project.name}:${site.name}:wizard`"
                    @click="runWizardForSite(project, site)"
                  >
                    Run wizard
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <NewProjectDialog v-if="session.allowMefManagement" v-model="showNewProject" @created="load" />
  <DeleteProjectDialog v-model="showDeleteDialog" :project="deleteTarget" @deleted="load" />
</template>

<script setup>
import { onMounted, ref, computed } from 'vue'
import { Badge, Button, ErrorMessage, LoadingText, Switch, toast } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/common/UpdatesAvailableButton.vue'
import ServiceRow from '@/components/mef/ServiceRow.vue'
import NewProjectDialog from '@/components/mef/NewProjectDialog.vue'
import DeleteProjectDialog from '@/components/mef/DeleteProjectDialog.vue'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useSession } from '@/composables/auth/useSession'
import { useRegistry } from '@/composables/mef/useRegistry'
import { siteStatusLabel, siteStatusTheme } from '@/utils/siteStatus'
import { mefApi } from '@/api/mef'
import { apiErrorMessage } from '@/api/client'

const { session } = useSession()
const { setBreadcrumbs } = useBreadcrumbs()
const showNewProject = ref(false)
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
const {
  projects,
  mefRoot,
  loading,
  controlLoading,
  error,
  portCollisions,
  projectDetails,
  load,
  startService,
  stopService,
  isExpanded,
  toggleExpand,
  refreshProjectDetail,
} = useRegistry()

setBreadcrumbs([{ label: 'Projects', route: { name: 'Projects' } }])

const SERVICES = [
  { key: 'pilot', label: 'pilot', canOpen: true },
  { key: 'app', label: 'app', canOpen: true },
  { key: 'redis', label: 'redis', canOpen: false },
  { key: 'db', label: 'db', canOpen: false },
  { key: 'mailpit', label: 'mailpit', canOpen: true },
]

const PILOT_STATUS = {
  running: { label: 'Running', theme: 'green' },
  stopped: { label: 'Stopped', theme: 'gray' },
}
const pilotStatus = (project) => (project.pilot_running ? 'running' : 'stopped')
const pilotLabel = (project) => PILOT_STATUS[pilotStatus(project)].label
const pilotTheme = (project) => PILOT_STATUS[pilotStatus(project)].theme

// db isn't a pitchfork daemon, so its status lives in the lazily-fetched
// project detail rather than the registry scan's `services` map.
function serviceStatus(project, key) {
  if (key === 'db') return projectDetails.value[project.name]?.dbStatus || 'unknown'
  return project.services?.[key] || 'unknown'
}

function openAdmin(project) {
  if (!project.pilot_port) return
  // JWT sid cookie is per-origin, so the new tab lands on the target admin's
  // login page rather than carrying this session over. Auto-login via
  // /api/v1/auto-login-token is a future enhancement.
  // Hardcode http:// for localhost (dev-only) to avoid mixed-content issues.
  window.open(`http://localhost:${project.pilot_port}`, '_blank', 'noopener')
}

function openService(project, key) {
  if (key === 'pilot') return openAdmin(project)
  if (key === 'app' && project.ports?.web) {
    window.open(`http://localhost:${project.ports.web}`, '_blank', 'noopener')
  }
  if (key === 'mailpit' && project.ports?.mailpit) {
    window.open(`http://localhost:${project.ports.mailpit}`, '_blank', 'noopener')
  }
}

function openSite(project, site) {
  if (!project.ports?.web) return
  window.open(`http://${site.name}:${project.ports.web}`, '_blank', 'noopener')
}

const jobLoading = ref(null)
const JOB_POLL_MS = 1500

// Fire-and-poll a job-spawning endpoint, toasting the outcome. `kind` scopes
// the loading flag per project+action so unrelated buttons don't spin too.
async function runJob(project, kind, dispatch, { successMsg, failureMsgPrefix, onSuccess } = {}) {
  const loadingKey = `${project.name}:${kind}`
  jobLoading.value = loadingKey
  try {
    const result = await dispatch()
    if (!result.job_id) {
      toast.error(apiErrorMessage(result, `Could not run ${kind}.`))
      jobLoading.value = null
      return
    }
    pollJob(result.job_id, loadingKey, { successMsg, failureMsgPrefix, onSuccess })
  } catch (caught) {
    toast.error(caught.message || `Could not run ${kind}.`)
    jobLoading.value = null
  }
}

async function pollJob(jobId, loadingKey, opts) {
  let detail
  try {
    detail = await mefApi.getJob(jobId)
  } catch {
    setTimeout(() => pollJob(jobId, loadingKey, opts), JOB_POLL_MS)
    return
  }
  if (detail?.error) {
    toast.error(apiErrorMessage(detail, 'Lost track of the job.'))
    if (jobLoading.value === loadingKey) jobLoading.value = null
    return
  }
  if (detail.status === 'running') {
    setTimeout(() => pollJob(jobId, loadingKey, opts), JOB_POLL_MS)
    return
  }
  if (jobLoading.value === loadingKey) jobLoading.value = null
  if (detail.status === 'success') {
    toast.success(opts.successMsg)
    opts.onSuccess?.()
  } else {
    toast.error(`${opts.failureMsgPrefix} (exit code ${detail.exit_code}). Check pilot logs.`)
  }
}

function testMailpit(project) {
  runJob(project, 'mailpit-test', () => mefApi.testMailpit(project.name), {
    successMsg: `Test mail sent to mailpit for ${project.name}`,
    failureMsgPrefix: 'Test mail failed',
  })
}

function runWizardForSite(project, site) {
  runJob(project, `${site.name}:wizard`, () => mefApi.runWizard(project.name, site.name), {
    successMsg: `Wizard completed for ${site.name}`,
    failureMsgPrefix: 'Wizard failed',
    onSuccess: () => refreshProjectDetail(project.name),
  })
}

onMounted(load)
</script>
