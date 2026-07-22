<template>
  <UpdatesAvailableButton />

  <div v-if="loading" class="flex justify-center py-12">
    <LoadingText />
  </div>
  <div v-else-if="error" class="py-12">
    <ErrorMessage :message="error" />
  </div>
  <div v-else-if="site" class="mx-auto w-full max-w-3xl">
    <!-- Hero -->
    <div class="relative -mx-4 sm:-mx-6 -mt-6 px-4 sm:px-6 pt-6 pb-7 overflow-hidden">
      <div class="absolute inset-0 pointer-events-none dot-field" aria-hidden="true" />
      <div
        class="relative flex justify-between items-center gap-3 bg-surface-base p-2 sm:p-4 border rounded-xl border-outline-gray-2">
        <div class="flex items-center gap-3 min-w-0">
          <span
            class="place-items-center grid bg-surface-elevation-1 border rounded-xl border-outline-gray-2 size-10 sm:size-12 text-ink-gray-6 shrink-0">
            <span class="size-5 sm:size-6 lucide-globe" />
          </span>
          <div class="min-w-0">
            <div class="flex items-center gap-2 min-w-0">
              <h1 class="font-semibold text-ink-gray-9 text-base sm:text-xl truncate">{{ site.name }}</h1>
              <button v-if="status === 'online'" type="button" class="shrink-0 hover:underline underline-offset-2"
                @click="openSite">
                <Badge :label="statusLabel" :theme="statusBadgeTheme" variant="subtle" size="md" />
              </button>
              <Badge v-else :label="statusLabel" :theme="statusBadgeTheme" variant="subtle" size="md" class="shrink-0" />
              <Badge :label="networkLabel" :theme="networkTheme" variant="subtle" size="md" class="shrink-0" />
              <a v-if="shareStatus.status === 'live'" :href="shareStatus.url" target="_blank" rel="noopener"
                class="shrink-0 hover:underline underline-offset-2" @click.stop>
                <Badge label="Shared" theme="blue" variant="subtle" size="md" />
              </a>
            </div>
            <div class="hidden sm:flex items-center gap-1.5 mt-1 text-ink-gray-5 text-sm">
              <span class="size-3.5 lucide-box" />
              {{ version || 'Version -' }}
              <template v-if="session.benchName">
                <span class="text-ink-gray-4">·</span>
                <RouterLink
                  :to="{ name: 'Projects', query: { expand: session.benchName } }"
                  class="flex items-center gap-1 hover:text-ink-gray-9 hover:underline underline-offset-2"
                >
                  <span class="size-3.5 lucide-folder" />
                  {{ session.benchName }}
                </RouterLink>
              </template>
              <template v-if="setupComplete === false">
                <span class="text-ink-gray-4">·</span>
                <span class="flex items-center gap-1 text-ink-amber-8">
                  <span class="size-3.5 lucide-triangle-alert" />
                  Setup wizard not completed
                </span>
                <button
                  class="text-ink-gray-6 hover:text-ink-gray-9 text-sm underline underline-offset-2"
                  @click="wizardDialogOpen = true"
                >
                  Run wizard
                </button>
              </template>
            </div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <Button v-if="!session.readOnly" size="sm" class="hidden sm:flex" @click="goToMarketplace">
            <template #prefix><span class="size-4 lucide-plus" /></template>
            Install app
          </Button>
          <Dropdown :options="menuOptions" placement="bottom-end">
            <template #default="{ open }">
              <Button variant="subtle" size="sm" :active="open">
                <span class="size-4 lucide-ellipsis" />
              </Button>
            </template>
          </Dropdown>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <TabButtons v-model="activeTab" :options="tabs" />

    <!-- Sections -->
    <SiteApps v-if="activeTab === 'apps'" :site-name="siteName" />
    <SiteBackups v-else-if="activeTab === 'backups'" :site-name="siteName" />
    <SiteConfig v-else-if="activeTab === 'config'" :site-name="siteName" />
    <SiteSettings v-else-if="activeTab === 'settings'" :site-name="siteName" />
  </div>

  <RunWizardDialog
    v-if="site"
    v-model="wizardDialogOpen"
    :site-name="siteName"
    :has-erpnext="site.installed_apps?.includes('erpnext') ?? false"
    @completed="setupComplete = true"
  />

  <MigrateSiteDialog v-if="site" v-model="showMigrate" :site-name="siteName" />
  <DropSiteDialog v-if="site" v-model="showDrop" :site-name="siteName" />
  <ShareSiteDialog v-if="site" v-model="showShare" :site-name="siteName" />

  <Teleport defer to="#header-actions">
    <Button variant="subtle" size="sm" @click="openSite">
      <template #prefix><span class="size-4 lucide-external-link" /></template>
      <span class="hidden sm:inline">Open site</span>
      <span class="sm:hidden">Open</span>
    </Button>
  </Teleport>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, Dropdown, ErrorMessage, LoadingText, TabButtons, toast } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/common/UpdatesAvailableButton.vue'
import { useSession } from '@/composables/auth/useSession'
import SiteApps from '@/components/sites/Apps.vue'
import SiteBackups from '@/components/sites/Backups.vue'
import SiteConfig from '@/components/sites/Config.vue'
import SiteSettings from '@/components/sites/Settings.vue'
import RunWizardDialog from '@/components/sites/RunWizardDialog.vue'
import MigrateSiteDialog from '@/components/sites/MigrateSiteDialog.vue'
import DropSiteDialog from '@/components/sites/DropSiteDialog.vue'
import ShareSiteDialog from '@/components/sites/ShareSiteDialog.vue'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { shareApi } from '@/api/share'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useSite } from '@/composables/sites/useSite'
import { useBench } from '@/composables/benches/useBench'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { openTaskDetailPage } from '@/utils/taskRoute'
import { siteNetworkLabel, siteNetworkTheme } from '@/utils/siteNetwork'

const route = useRoute()
const router = useRouter()
const siteName = route.params.name

const { setBreadcrumbs } = useBreadcrumbs()
const { site, loading, error, status, load, login, backup } = useSite(siteName)
const { version, load: loadBench } = useBench()
const { session } = useSession()

setBreadcrumbs([
  { label: session.benchName || 'Project', route: { name: 'Projects', query: { expand: session.benchName } } },
  { label: 'Sites', route: { name: 'Sites' } },
  { label: siteName },
])

const STATUS_THEMES = { online: 'green', broken: 'red', offline: 'orange', provisioning: 'blue' }
const STATUS_LABELS = { online: 'Live', broken: 'Broken', offline: 'Paused', provisioning: 'Creating' }

const statusLabel = computed(() => STATUS_LABELS[status.value] ?? status.value)
const statusBadgeTheme = computed(() => STATUS_THEMES[status.value] ?? 'gray')
const networkLabel = computed(() => (site.value ? siteNetworkLabel(site.value) : ''))
const networkTheme = computed(() => (site.value ? siteNetworkTheme(site.value) : 'gray'))

const tabs = [
  { value: 'apps', label: 'Apps' },
  { value: 'backups', label: 'Backups' },
  { value: 'config', label: 'Config' },
  { value: 'settings', label: 'Settings' },
]

const VALID_TABS = tabs.map((t) => t.value)
const activeTab = ref(VALID_TABS.includes(route.params.tab) ? route.params.tab : 'apps')

watch(activeTab, (tab) => {
  router.replace({ name: 'SiteDetail', params: { name: siteName, tab } })
})

watch(() => route.params.tab, (tab) => {
  if (tab && VALID_TABS.includes(tab) && tab !== activeTab.value) activeTab.value = tab
})

const tabLabel = computed(() => tabs.find((t) => t.value === activeTab.value)?.label ?? '')
watchEffect(() => {
  const suffix = session.benchName ? ` (${session.benchName})` : ''
  if (site.value) document.title = `${site.value.name} | ${tabLabel.value}${suffix}`
})

const isMobile = useIsMobile()

function openSite() {
  toast.promise(login(), {
    loading: 'Logging in as admin',
    success: 'Logged in as admin',
    error: 'Could not log in as admin',
  })
}

function goToMarketplace() {
  router.push({ path: '/marketplace', query: { site: siteName } })
}

function loginAsAdmin() {
  toast.promise(login(), {
    loading: 'Logging in as admin',
    success: 'Logged in as admin',
    error: 'Could not log in as admin',
  })
}

// ----- setup wizard status/trigger -----
const setupComplete = ref(null)
const wizardDialogOpen = ref(false)

async function loadSetupStatus() {
  try {
    const result = await sitesApi.getSetupStatus(siteName)
    setupComplete.value = result?.setup_complete ?? null
  } catch {
    setupComplete.value = null
  }
}

async function resetWizard() {
  try {
    const result = await sitesApi.resetWizard(siteName)
    if (result?.error) {
      toast.error(apiErrorMessage(result, 'Could not reset the setup wizard.'))
      return
    }
    setupComplete.value = false
    toast.success(`Setup wizard reset for ${siteName}`)
  } catch (caught) {
    toast.error(caught.message || 'Could not reset the setup wizard.')
  }
}

async function backupNow() {
  try {
    const result = await backup()
    if (result.ok) openTaskDetailPage(router, result.task_id)
    else toast.error(apiErrorMessage(result, 'Could not start backup'))
  } catch (caught) {
    toast.error(caught.message || 'Could not start backup')
  }
}

const showMigrate = ref(false)
const showDrop = ref(false)
const showShare = ref(false)
const slimConnected = ref(false)
const shareStatus = ref({})

const menuOptions = computed(() => [
  ...(isMobile.value && !session.readOnly ? [{ label: 'Install app', icon: 'lucide-plus', onClick: goToMarketplace }] : []),
  { label: 'Login as admin', icon: 'lucide-log-in', onClick: loginAsAdmin },
  ...(session.readOnly ? [] : [{ label: 'Back up now', icon: 'lucide-archive', onClick: backupNow }]),
  ...(session.readOnly || setupComplete.value !== true
    ? []
    : [{ label: 'Reset setup wizard', icon: 'lucide-rotate-ccw', onClick: resetWizard }]),
  ...(session.readOnly ? [] : [{ label: 'Migrate site', icon: 'lucide-refresh-cw', onClick: () => { showMigrate.value = true } }]),
  ...(session.readOnly
    ? []
    : [{
        label: shareStatus.value.status === 'live' ? 'Manage sharing' : 'Share site',
        icon: 'lucide-share-2',
        disabled: !slimConnected.value,
        onClick: () => { showShare.value = true },
      }]),
  ...(session.readOnly ? [] : [{ label: 'Delete site', icon: 'lucide-trash-2', onClick: () => { showDrop.value = true } }]),
])

// Provisioning is a transient state (a new-site/reinstall task still running);
// poll until it clears instead of leaving the badge stuck on "Creating".
let provisioningPoll = null
watch(status, (value) => {
  if (value === 'provisioning' && !provisioningPoll) {
    provisioningPoll = setInterval(load, 3000)
  } else if (value !== 'provisioning' && provisioningPoll) {
    clearInterval(provisioningPoll)
    provisioningPoll = null
  }
})
onUnmounted(() => { if (provisioningPoll) clearInterval(provisioningPoll) })

function refreshShareStatus() {
  sitesApi.share.status(siteName).then((s) => { shareStatus.value = s.status ? s : {} }).catch(() => {})
}

watch(showShare, (open) => { if (!open) refreshShareStatus() })

onMounted(() => {
  load()
  loadBench()
  loadSetupStatus()
  shareApi.slimStatus().then((s) => { slimConnected.value = s.connected }).catch(() => {})
  refreshShareStatus()
})
</script>

<style scoped>
.dot-field {
  background-image: radial-gradient(var(--outline-gray-3) 1.1px, transparent 1.3px);
  background-size: 20px 20px;
  background-position: -8px -8px;
  mask-image: linear-gradient(to bottom, rgb(0 0 0 / .95), transparent 90%);
}
</style>
