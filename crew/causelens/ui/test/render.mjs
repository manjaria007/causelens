// Renders the BUILT page (dist/index.mjs) with react-dom/server against the TEST STUBS in test/stubs/. Shared by page.test.mjs and
// browser.test.mjs. This is not the Crew host: the stubs only record what the page asks for.
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const ui = join(here, '..')
export const built = readFileSync(join(ui, 'dist', 'index.mjs'), 'utf8')
export const packagedConsole = readFileSync(join(ui, 'console.html'), 'utf8')
const stub = (name) => pathToFileURL(join(here, 'stubs', name)).href

const rewired = built
  .replaceAll('"@kirocrew/app-sdk/ui"', JSON.stringify(stub('app-sdk-ui.mjs')))
  .replaceAll('"@kirocrew/app-sdk"', JSON.stringify(stub('app-sdk.mjs')))
  .replaceAll('"lucide-react"', JSON.stringify(stub('lucide-react.mjs')))
const tmp = mkdtempSync(join(ui, 'node_modules', '.page-test-')) // inside the ui tree so 'react' resolves from node_modules
process.on('exit', () => rmSync(tmp, { recursive: true, force: true }))
const file = join(tmp, 'page.mjs')
writeFileSync(file, rewired)

const React = await import('react')
const { renderToStaticMarkup } = await import('react-dom/server')
const { default: Page } = await import(pathToFileURL(file).href)
export const { calls } = await import(stub('app-sdk.mjs'))
export const html = renderToStaticMarkup(React.createElement(Page))
export const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

export function unescapeAttr(value) {
  return value.replaceAll('&quot;', '"').replaceAll('&#x27;', "'").replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&')
}

/** The single iframe the page renders: its attribute text and its decoded srcDoc. */
export function frame() {
  const frames = [...html.matchAll(/<iframe([^>]*)>/g)].map((m) => m[1])
  if (frames.length !== 1) throw new Error(`expected one iframe, got ${frames.length}`)
  return { attrs: frames[0], srcdoc: unescapeAttr(frames[0].match(/srcDoc="([^"]*)"/i)[1]) }
}
