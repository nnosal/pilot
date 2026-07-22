/** Same shape as the .config/overlays/mcp transform.py entry, but with the token
 * inlined rather than a `${FRAPPE_MCP_TOKEN_...}` placeholder: the placeholder only
 * resolves when `claude` is launched from a shell mise has loaded this project's
 * .env into, so a copy meant to be pasted anywhere (a different project, a global
 * config) needs the literal secret to work out of the box. */
export function mcpConfigJson(mcp) {
  return JSON.stringify(
    {
      mcpServers: {
        [mcp.server_name]: {
          type: 'http',
          url: mcp.url,
          headers: {
            Authorization: `token ${mcp.token}`,
            'X-Frappe-Site-Name': mcp.site,
          },
        },
      },
    },
    null,
    2,
  )
}

export async function copyMcpConfig(mcp) {
  await navigator.clipboard.writeText(mcpConfigJson(mcp))
}
