<template>
  <Tooltip :text="`${mcp.url} — click to copy .mcp.json entry`">
    <Badge label="MCP" theme="violet" variant="subtle" :size="size" class="shrink-0 cursor-pointer"
      @click.stop.prevent="copy" />
  </Tooltip>
</template>

<script setup>
import { Badge, Tooltip, toast } from 'frappe-ui'
import { copyMcpConfig } from '@/utils/mcpConfig'

const props = defineProps({
  mcp: { type: Object, required: true },
  size: { type: String, default: 'md' },
})

async function copy() {
  try {
    await copyMcpConfig(props.mcp)
    toast.success('MCP config copied')
  } catch {
    toast.error('Could not copy MCP config')
  }
}
</script>
