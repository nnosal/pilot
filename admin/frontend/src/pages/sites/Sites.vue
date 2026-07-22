<template>
  <UpdatesAvailableButton />

  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center">
      <h1 class="font-medium text-ink-gray-8 text-base">
        Your sites <span class="font-normal text-ink-gray-5">({{ filteredSites.length }})</span>
      </h1>
    </div>

    <!-- Bar -->
    <div class="flex items-center gap-2 mt-4">
      <!-- Search text bar -->
      <FormControl v-model="search" type="text" placeholder="Search" class="flex-1">
        <template #prefix>
          <span class="size-4 text-ink-gray-5 lucide-search" />
        </template>
      </FormControl>
      <!-- Status filter -->
      <FormControl v-model="statusFilter" type="select" :options="statusOptions" class="max-w-24 sm:max-w-32" />
      <!-- List view type -->
      <TabButtons v-model="view" :options="viewOptions" class="hidden sm:block" />
    </div>

    <div v-if="loading" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <div v-else-if="error" class="mt-16">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="filteredSites.length" class="mt-4">
      <!-- Grid view -->
      <div v-if="view === 'grid'" class="gap-3 grid grid-cols-1 md:grid-cols-2">
        <!-- Site Card -->
        <div v-for="site in filteredSites" :key="site.name"
          class="flex flex-col gap-2 bg-surface-elevation-1 hover:bg-surface-gray-1 p-2 sm:p-4 border rounded-xl border-outline-gray-2 hover:border-outline-gray-3 transition-colors">
          <RouterLink :to="{ name: 'SiteDetail', params: { name: site.name } }"
            class="flex flex-1 items-center gap-3 min-w-0 no-underline">
            <!-- Icon -->
            <div class="place-items-center grid bg-surface-elevation-1 rounded-lg size-10 text-ink-gray-6 shrink-0">
              <span class="size-5 lucide-globe"></span>
            </div>
            <div class="flex-1 min-w-0">
              <!-- First Line -->
              <div class="gap-2 grid grid-cols-[3fr_1fr]">
                <div class="flex items-center gap-1.5 min-w-0">
                  <!-- Site Name -->
                  <span class="font-semibold text-ink-gray-9 text-base truncate">
                    {{ site.name }}
                  </span>

                  <!-- Status -->
                  <Badge :label="statusLabel(site)" :theme="statusTheme(site)" variant="subtle" size="sm"
                    class="shrink-0" />

                  <!-- Local vs slim HTTPS -->
                  <Badge :label="networkLabel(site)" :theme="networkTheme(site)" variant="subtle" size="sm"
                    class="shrink-0" />

                  <McpBadge v-if="site.mcp" :mcp="site.mcp" size="sm" />

                  <!-- Currently shared publicly -->
                  <a v-if="shareStatus[site.name]?.status === 'live'" :href="shareStatus[site.name].url"
                    target="_blank" rel="noopener" class="shrink-0" @click.stop>
                    <Badge label="Shared" theme="blue" variant="subtle" size="sm" />
                  </a>
                </div>

                <div class="flex justify-end">
                  <!-- Actions Dropdown -->
                  <Dropdown :options="siteMenuOptions(site)" placement="bottom-end">
                    <template #default="{ open }">
                      <Button variant="ghost" size="xs" class="!px-1.5">
                        <span class="size-4 lucide-more-horizontal" />
                      </Button>
                    </template>
                  </Dropdown>
                </div>
              </div>

              <!-- Second Line -->
              <p class="text-ink-gray-5 text-p-sm">
                {{ appsLabel(site) }}
              </p>
            </div>
          </RouterLink>

          <!-- Setup wizard not completed yet -->
          <div
            v-if="setupStatus[site.name] === false"
            class="flex items-center gap-2 bg-surface-amber-2 px-2.5 py-1.5 rounded border border-outline-amber-3"
          >
            <span class="size-4 text-ink-amber-8 lucide-triangle-alert shrink-0" />
            <span class="text-ink-amber-9 text-xs">Setup wizard not completed yet.</span>
            <Button variant="solid" size="sm" class="ml-auto" @click="openWizardDialog(site)">
              Run wizard
            </Button>
          </div>
        </div>
      </div>

      <!-- List view -->
      <ListView v-else :columns="listColumns" :rows="listRows" row-key="name"
        :options="{ selectable: false, showTooltip: false }">
        <template #cell="{ column, row, item }">
          <div v-if="column.key === 'site'" class="flex items-center gap-3">
            <!-- Icon -->
            <div class="place-items-center grid bg-surface-elevation-1 rounded-lg size-10 text-ink-gray-6 shrink-0">
              <span class="size-5 lucide-globe" />
            </div>
            <RouterLink :to="{ name: 'SiteDetail', params: { name: row.site.name } }"
              class="font-medium text-ink-gray-9 text-sm no-underline truncate">
              {{ row.site.name }}
            </RouterLink>
          </div>
          <div v-else-if="column.key === 'status'" class="flex items-center gap-1.5">
            <Badge :label="statusLabel(row.site)" :theme="statusTheme(row.site)" variant="subtle" size="sm" />
            <Badge :label="networkLabel(row.site)" :theme="networkTheme(row.site)" variant="subtle" size="sm" />
            <McpBadge v-if="row.site.mcp" :mcp="row.site.mcp" size="sm" />
            <a v-if="shareStatus[row.site.name]?.status === 'live'" :href="shareStatus[row.site.name].url"
              target="_blank" rel="noopener" @click.stop>
              <Badge label="Shared" theme="blue" variant="subtle" size="sm" />
            </a>
          </div>
          <div v-else-if="column.key === 'apps'" class="text-ink-gray-6 text-sm">
            {{ item }}
          </div>
          <div v-else-if="column.key === 'actions'" class="flex justify-end">
            <Dropdown :options="siteMenuOptions(row.site)" placement="bottom-end">
              <template #default="{ open }">
                <Button variant="ghost" size="sm" :active="open">
                  <span class="size-4 lucide-more-vertical" />
                </Button>
              </template>
            </Dropdown>
          </div>
        </template>
      </ListView>
    </div>

    <!-- No s -->
    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No sites found.</p>
  </div>

  <!-- New Site Button -->
  <Teleport v-if="!session.readOnly" defer to="#header-actions">
    <div class="flex items-center gap-2">
      <Button variant="solid" @click="showCreate = true">
        <template #prefix>
          <span class="size-4 lucide-plus" />
        </template>
        New site
      </Button>
    </div>
  </Teleport>

  <NewSiteDialog v-model="showCreate" :sites="sites" @started="(taskId) => openTaskDetailPage(router, taskId)" />

  <RunWizardDialog
    v-if="wizardSite"
    v-model="wizardDialogOpen"
    :site-name="wizardSite.name"
    :has-erpnext="wizardSite.installed_apps?.includes('erpnext') ?? false"
    @completed="setupStatus = { ...setupStatus, [wizardSite.name]: true }"
  />

  <MigrateSiteDialog v-if="migrateSite" v-model="migrateDialogOpen" :site-name="migrateSite.name" />
  <DropSiteDialog v-if="dropSite" v-model="dropDialogOpen" :site-name="dropSite.name" />
  <ShareSiteDialog v-if="shareSite" v-model="shareDialogOpen" :site-name="shareSite.name" />
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSession } from '@/composables/auth/useSession'
import {
  Badge,
  Button,
  Dropdown,
  ErrorMessage,
  FormControl,
  ListView,
  LoadingText,
  TabButtons,
  toast,
} from 'frappe-ui'
import McpBadge from '@/components/common/McpBadge.vue'
import NewSiteDialog from '@/components/sites/NewSiteDialog.vue'
import RunWizardDialog from '@/components/sites/RunWizardDialog.vue'
import MigrateSiteDialog from '@/components/sites/MigrateSiteDialog.vue'
import DropSiteDialog from '@/components/sites/DropSiteDialog.vue'
import ShareSiteDialog from '@/components/sites/ShareSiteDialog.vue'
import UpdatesAvailableButton from '@/components/common/UpdatesAvailableButton.vue'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useSites } from '@/composables/sites/useSites'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { shareApi } from '@/api/share'
import { openTaskDetailPage } from '@/utils/taskRoute'
import { openSiteLogin } from '@/utils/siteLogin'
import { siteStatus, siteStatusLabel, siteStatusTheme } from '@/utils/siteStatus'
import { siteNetworkLabel, siteNetworkTheme } from '@/utils/siteNetwork'

const router = useRouter()
const { session } = useSession()
const { setBreadcrumbs } = useBreadcrumbs()
const { sites, loading, error, load } = useSites()

setBreadcrumbs([
  { label: session.benchName || 'Project', route: { name: 'Projects', query: { expand: session.benchName } } },
  { label: 'Sites', route: { name: 'Sites' } },
])

const search = ref('')
const statusFilter = ref('all')
const view = ref('grid')

const viewOptions = [
  { value: 'grid', icon: 'lucide-layout-grid' },
  { value: 'list', icon: 'lucide-list' },
]

const statusOptions = [
  { label: 'Status', value: 'all' },
  { label: 'Active', value: 'online' },
  { label: 'Broken', value: 'broken' },
  { label: 'Paused', value: 'offline' },
  { label: 'Creating', value: 'provisioning' },
]

const statusLabel = siteStatusLabel
const statusTheme = siteStatusTheme
const networkLabel = siteNetworkLabel
const networkTheme = siteNetworkTheme

function appsLabel(site) {
  const count = site.installed_apps?.length || 0
  return count === 1 ? '1 app' : `${count} apps`
}

const filteredSites = computed(() => {
  const query = search.value.toLowerCase().trim()
  return sites.value.filter((site) => {
    const matchesSearch = !query || site.name.toLowerCase().includes(query)
    const matchesStatus = statusFilter.value === 'all' || siteStatus(site) === statusFilter.value
    return matchesSearch && matchesStatus
  })
})

const listColumns = [
  { label: 'Site', key: 'site', align: 'left', width: 3 },
  { label: 'Status', key: 'status', align: 'left', width: 1.5 },
  { label: 'Apps', key: 'apps', align: 'left', width: 1.5 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const listRows = computed(() =>
  filteredSites.value.map((site) => ({
    name: site.name,
    site,
    apps: appsLabel(site),
  })),
)

async function loginAsAdmin(site) {
  return openSiteLogin(() => sitesApi.loginLink(site.name))
}

function openSite(site) {
  toast.promise(loginAsAdmin(site), {
    loading: 'Logging in as admin',
    success: 'Logged in as admin',
    error: 'Could not log in as admin',
  })
}

async function backupNow(site) {
  try {
    const result = await sitesApi.backups.create(site.name)
    if (result.ok) openTaskDetailPage(router, result.task_id)
    else toast.error(apiErrorMessage(result, 'Could not start backup'))
  } catch (caught) {
    toast.error(caught.message || 'Could not start backup')
  }
}

const migrateSite = ref(null)
const migrateDialogOpen = ref(false)

function openMigrateDialog(site) {
  migrateSite.value = site
  migrateDialogOpen.value = true
}

const dropSite = ref(null)
const dropDialogOpen = ref(false)

function openDropDialog(site) {
  dropSite.value = site
  dropDialogOpen.value = true
}

const shareSite = ref(null)
const shareDialogOpen = ref(false)
const slimConnected = ref(false)
const shareStatus = ref({})

function openShareDialog(site) {
  shareSite.value = site
  shareDialogOpen.value = true
}

async function loadShareStatus() {
  const results = await Promise.all(
    sites.value.map((site) => sitesApi.share.status(site.name).catch(() => null)),
  )
  shareStatus.value = Object.fromEntries(
    sites.value.map((site, i) => [site.name, results[i]?.status ? results[i] : {}]),
  )
}

watch(shareDialogOpen, (open) => { if (!open) loadShareStatus() })

function siteMenuOptions(site) {
  return [
    { label: 'Open site', icon: 'lucide-external-link', onClick: () => openSite(site) },
    { label: 'Back up now', icon: 'lucide-archive', onClick: () => backupNow(site) },
    { label: 'Migrate site', icon: 'lucide-refresh-cw', onClick: () => openMigrateDialog(site) },
    {
      label: 'Share site',
      icon: 'lucide-share-2',
      disabled: !slimConnected.value,
      onClick: () => openShareDialog(site),
    },
    { label: 'Delete site', icon: 'lucide-trash-2', onClick: () => openDropDialog(site) },
  ]
}

const showCreate = ref(false)

// ----- setup wizard status/trigger, fetched once per site after the list loads -----
const setupStatus = ref({})
const wizardDialogOpen = ref(false)
const wizardSite = ref(null)

async function loadSetupStatus() {
  const results = await Promise.all(
    sites.value.map((site) => sitesApi.getSetupStatus(site.name).catch(() => null)),
  )
  setupStatus.value = Object.fromEntries(
    sites.value.map((site, i) => [site.name, results[i]?.setup_complete ?? null]),
  )
}

function openWizardDialog(site) {
  wizardSite.value = site
  wizardDialogOpen.value = true
}

onMounted(async () => {
  await load()
  loadSetupStatus()
  loadShareStatus()
  shareApi.slimStatus().then((s) => { slimConnected.value = s.connected }).catch(() => {})
})
</script>
