/** Built-in Wasm datasets in Raycaster C++ order (Raycaster.h volumes). */

export type BuiltinDataset = { name: string; size: number }

export const BUILTIN_DATASETS: BuiltinDataset[] = [
  { name: 'Sphere', size: 256 },
  { name: 'Linear Ramp', size: 256 },
  { name: 'Marschner-Lobb', size: 256 },
  { name: 'C60 Molecule', size: 64 },
  { name: 'Aneurism', size: 256 },
  { name: 'Bonsai Tree', size: 128 },
  { name: 'Visible Human Head', size: 512 },
  { name: 'Christmas Tree', size: 512 },
]

/** MEMFS slot reused for every external preview volume. */
export const EXTERNAL_DAT_NAME = '/ext_preview.dat'
export const EXTERNAL_RAW_NAME = '/ext_preview.raw'

export function datasetLabel(index: number, externalName?: string | null): string {
  if (index < BUILTIN_DATASETS.length) {
    const d = BUILTIN_DATASETS[index]
    return `${d.name} (${d.size}\u00B3)`
  }
  return externalName ? `External — ${externalName}` : 'External volume'
}
