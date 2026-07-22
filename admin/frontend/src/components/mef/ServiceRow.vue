<template>
  <div class="flex items-center gap-2 bg-surface-white px-2.5 py-1.5 rounded border border-outline-gray-1">
    <span class="w-12 text-ink-gray-6 text-xs shrink-0">{{ label }}</span>
    <Badge :theme="theme" :label="statusLabel" variant="subtle" size="sm" />
    <Badge v-if="port" :label="`:${port}`" theme="gray" variant="subtle" size="sm" />

    <div class="flex items-center gap-1 ml-auto">
      <span v-if="loading" class="flex justify-center items-center w-7 h-7">
        <span class="size-4 text-ink-gray-5 lucide-loader-2 animate-spin" />
      </span>
      <template v-else-if="canControl">
        <Button
          v-if="status !== 'running'"
          variant="ghost"
          size="sm"
          :title="`Start ${label}`"
          @click="$emit('start')"
        >
          <template #prefix>
            <span class="size-4 lucide-play" />
          </template>
        </Button>
        <Button v-else variant="ghost" size="sm" theme="red" :title="`Stop ${label}`" @click="$emit('stop')">
          <template #prefix>
            <span class="size-4 lucide-square" />
          </template>
        </Button>
      </template>
      <span v-else class="text-ink-gray-4 text-xs px-1" title="This is the admin you are using">—</span>

      <slot name="extra" />

      <Button
        v-if="canOpen"
        variant="ghost"
        size="sm"
        :disabled="status !== 'running'"
        :title="`Open ${label}`"
        @click="$emit('open')"
      >
        <template #prefix>
          <span class="size-4 lucide-external-link" />
        </template>
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Badge, Button } from 'frappe-ui'

const props = defineProps({
  label: { type: String, required: true },
  status: { type: String, required: true }, // 'running' | 'stopped' | 'errored' | 'unknown'
  loading: { type: Boolean, default: false },
  canControl: { type: Boolean, default: true },
  canOpen: { type: Boolean, default: false },
  port: { type: [Number, String], default: null },
})
defineEmits(['start', 'stop', 'open'])

const STATUS_META = {
  running: { label: 'Running', theme: 'green' },
  stopped: { label: 'Stopped', theme: 'gray' },
  errored: { label: 'Errored', theme: 'red' },
  unknown: { label: 'Unknown', theme: 'gray' },
}
const statusLabel = computed(() => STATUS_META[props.status]?.label || STATUS_META.unknown.label)
const theme = computed(() => STATUS_META[props.status]?.theme || STATUS_META.unknown.theme)
</script>
