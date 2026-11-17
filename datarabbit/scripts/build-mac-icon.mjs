import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import sharp from "sharp"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const buildDir = path.join(root, "build")
const logoSource = readFileSync(
  path.join(root, "src", "components", "desktop", "rvbbit-logo.tsx"),
  "utf8",
)
const logoPaths = [...logoSource.matchAll(/<path\s+d="([^"]+)"/g)].map((match) => match[1])

if (logoPaths.length < 2) {
  throw new Error("Could not extract the two vector paths from RvbbitLogo")
}

mkdirSync(buildDir, { recursive: true })
rmSync(path.join(buildDir, "DataRabbit.iconset"), { recursive: true, force: true })

const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="background" x1="128" y1="96" x2="896" y2="928" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#262118"/>
      <stop offset="1" stop-color="#0e0d0a"/>
    </linearGradient>
    <radialGradient id="glow" cx="0" cy="0" r="1" gradientTransform="translate(512 514) rotate(90) scale(430)">
      <stop stop-color="#f3ae3d" stop-opacity=".16"/>
      <stop offset="1" stop-color="#f3ae3d" stop-opacity="0"/>
    </radialGradient>
    <pattern id="grid" width="64" height="64" patternUnits="userSpaceOnUse">
      <path d="M64 0H0V64" fill="none" stroke="#f3ae3d" stroke-opacity=".055" stroke-width="2"/>
    </pattern>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="24" stdDeviation="22" flood-color="#000" flood-opacity=".48"/>
    </filter>
  </defs>
  <rect x="48" y="48" width="928" height="928" rx="216" fill="url(#background)"/>
  <rect x="48" y="48" width="928" height="928" rx="216" fill="url(#grid)"/>
  <rect x="48" y="48" width="928" height="928" rx="216" fill="url(#glow)"/>
  <rect x="61" y="61" width="902" height="902" rx="203" fill="none" stroke="#f3ae3d" stroke-opacity=".22" stroke-width="6"/>
  <g transform="translate(102 303) scale(.593)" fill="#f3ae3d" filter="url(#shadow)">
    <path d="${logoPaths[0]}"/>
    <path d="${logoPaths[1]}"/>
  </g>
</svg>`

const sourcePath = path.join(buildDir, "icon-source.svg")
writeFileSync(sourcePath, svg)

await sharp(Buffer.from(svg))
  .resize(1024, 1024)
  .png()
  .toFile(path.join(buildDir, "icon.png"))

console.log("Created build/icon.png from the shared Data Rabbit vector mark")
