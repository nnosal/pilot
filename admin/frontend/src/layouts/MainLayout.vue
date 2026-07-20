<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Badge, Breadcrumbs, Button } from 'frappe-ui'
import AppSidebar from '@/components/common/AppSidebar.vue'
import PilotShutdownButton from '@/components/mef/PilotShutdownButton.vue'
import ProjectSwitcher from '@/components/mef/ProjectSwitcher.vue'
import NewProjectDialog from '@/components/mef/NewProjectDialog.vue'
import MefProjectsDialog from '@/components/mef/MefProjectsDialog.vue'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useSession } from '@/composables/auth/useSession'

const route = useRoute()
const { session } = useSession()
const { items, resetBreadcrumbs } = useBreadcrumbs()
const isMobile = useIsMobile()

watch(() => route.name, resetBreadcrumbs)

const breadcrumbs = computed(() => {
  const all = items.value || breadcrumbsFromRouteMeta(route.meta)
  return isMobile.value ? all.slice(-1) : all
})

function breadcrumbsFromRouteMeta({ title = '', group }) {
  return group ? [{ label: group }, { label: title }] : [{ label: title }]
}

const showMefProjects = ref(false)
const showNewProject = ref(false)

// Open the new-project dialog from inside the projects manager.
function openNewProject() {
  showMefProjects.value = false
  showNewProject.value = true
}

// Once a project creation job has been spawned, surface the projects manager so
// the operator can watch progress and act on the new entry when it lands.
function onProjectCreated() {
  showMefProjects.value = true
}
</script>

<template>
  <div class="flex bg-surface-elevation-1 h-screen overflow-hidden">
    <AppSidebar />
    <main class="flex flex-col flex-1 overflow-hidden">
      <header
        class="top-0 z-10 sticky flex items-center gap-2 px-4 sm:px-6 py-2.5 border-b border-outline-alpha-gray-1 shrink-0">
        <Breadcrumbs :items="breadcrumbs" />
        <Badge v-if="session.readOnly" theme="orange" variant="subtle" label="Read-only" />
        <div id="header-badge" class="flex items-center" />
        <div class="flex items-center gap-2 ml-auto">
          <template v-if="session.allowMefManagement">
            <PilotShutdownButton />
            <ProjectSwitcher />
            <Button v-if="route.name !== 'Projects'" variant="subtle" size="sm" @click="showMefProjects = true">
              <template #prefix>
                <span class="size-4 lucide-folder-tree" />
              </template>
              Projects
            </Button>
          </template>
          <div id="header-actions" class="flex items-center gap-2" />
        </div>
      </header>
      <div class="flex-1 p-4 sm:p-6 min-h-0 overflow-auto [scrollbar-gutter:stable]">
        <slot />
      </div>
    </main>
  </div>

  <template v-if="session.allowMefManagement">
    <NewProjectDialog v-model="showNewProject" @created="onProjectCreated" />
    <MefProjectsDialog v-model="showMefProjects" @new-project="openNewProject" />
  </template>
</template>
