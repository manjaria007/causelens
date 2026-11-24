// TEST STUB, not the Crew host. Stands in for '@kirocrew/app-sdk' so the built page can be rendered in Node.
// It records what the page passes to the host components; it does not run a chat, call an agent or reach MCP.
import { createElement } from 'react'

export const calls = { chatEmbed: [], launcher: [] }

export function ChatEmbed(props) {
  calls.chatEmbed.push(props)
  return createElement('div', { 'data-stub': 'ChatEmbed', 'data-slot': props.slotKey, 'data-agent': props.agent })
}

export function useChatLauncher() {
  return { openChat: (opts) => calls.launcher.push(opts) }
}
