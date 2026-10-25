import { useEffect } from 'react'
import { fetchVolumeImport, type VolumeImport } from '../api'
import { samplingExpFromRate } from './methods'

export function usePreviewImportReload(record: VolumeImport | null): void {
  useEffect(() => {
    if (!record) return
    void fetchVolumeImport(record.importId).catch(() => undefined)
  }, [record])
}

export function samplingExpForRate(rate: number): number {
  return samplingExpFromRate(rate)
}
