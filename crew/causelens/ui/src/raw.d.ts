// Vite's ?raw imports resolve to the file's text.
declare module '*?raw' {
  const text: string
  export default text
}
