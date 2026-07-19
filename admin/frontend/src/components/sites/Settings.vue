<template>
  <div class="space-y-9 mt-5">
    <SettingsGeneral :site-name="siteName" />
    <SettingsDomains :site-name="siteName" />
    <SettingsActions :site-name="siteName" />
    <SettingsDanger v-if="!session.readOnly" :site-name="siteName" />
  </div>
</template>

<script setup>
import SettingsGeneral from './settings/General.vue'
import SettingsDomains from './settings/Domains.vue'
import SettingsActions from './settings/Actions.vue'
import SettingsDanger from './settings/Danger.vue'
import { useSession } from '@/composables/auth/useSession'

const { session } = useSession()

defineProps({ siteName: { type: String, required: true } })
</script>
