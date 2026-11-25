// TEST STUB, not the Crew host. Stands in for '@kirocrew/app-sdk' so the built page can be rendered in Node.
// It records what the page passes to the host components; it does not run a chat, call an agent or reach MCP.
import { createElement } from 'react'

export const calls = { chatEmbed: [], launcher: [], api: [] }

export function ChatEmbed(props) {
  calls.chatEmbed.push(props)
  return createElement('div', { 'data-stub': 'ChatEmbed', 'data-slot': props.slotKey, 'data-agent': props.agent })
}

export function useChatLauncher() {
  return { openChat: (opts) => calls.launcher.push(opts) }
}

// Records the scoped API requests the page makes. Under react-dom/server effects do not run, so a render posts nothing.
export function useAppApi() {
  return { post: async (path, body) => { calls.api.push({ path, body }); return { key: body?.name, memory_mode: body?.memory_mode, agent: body?.agent } } }
}
