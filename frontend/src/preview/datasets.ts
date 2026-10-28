/** Built-in Wasm datasets in Raycaster C++ order (Raycaster.h volumes). */

export type BuiltinDataset = {
  name: string
  size: number
  /**
   * Stem of the runnable file under `volumes/` (`<stem>_<dims>_uint8.dat`).
   * Built-ins ship as QVis lz4 payloads the import chain cannot read, so only
   * materialized stems can also feed the agent renderer. Without a stem the
   * dataset is preview-only and must never claim to be the run volume.
   * Materialize with: python scripts/materialize_dataset.py <dataset>.dat
   */
  sourceStem?: string
}

export const BUILTIN_DATASETS: BuiltinDataset[] = [
  { name: 'Sphere', size: 256 },
  { name: 'Linear Ramp', size: 256 },
  { name: 'Marschner-Lobb', size: 256 },
  { name: 'C60 Molecule', size: 64, sourceStem: 'c60' },
  { name: 'Aneurism', size: 256, sourceStem: 'aneurism' },
  { name: 'Bonsai Tree', size: 128, sourceStem: 'bonsai' },
  { name: 'Visible Human Head', size: 512, sourceStem: 'head512' },
  { name: 'Christmas Tree', size: 512, sourceStem: 'xmas' },
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
