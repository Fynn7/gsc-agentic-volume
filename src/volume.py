"""Numeric import capability. No Dataset Card and no semantic labels."""
import sys
import json
import dataclasses
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from volume_context.convert import convert_to_canonical, renderer_uint8
from volume_context.hashing import sha256_file
from volume_context.stats import compute_statistics
from volume_context.budgets import Budgets
from volume_context.readers.common import numpy_dtype
from volume_context.readers import read_source_header


def prepare(source, directory):
    source = Path(source).resolve()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    h = read_source_header(source)
    budgets = Budgets()
    original = {str(p): sha256_file(p) for p in {source, h.payload_path}}
    payload = h.open_payload(directory, budgets)
    dtype = numpy_dtype(h.scalar_type, h.endianness)
    def chunks(): return payload.iter_chunks(h.item_bytes, budgets.chunk_bytes)
    stats = compute_statistics(dtype=dtype, scalar_type=h.scalar_type,
                               dimensions=h.dimensions, chunk_reader=chunks(), budgets=budgets)
    c = convert_to_canonical(dtype=dtype, source_scalar_type=h.scalar_type, statistics=stats,
                             chunk_reader=chunks(), staging_dir=directory, spacing=h.geometry.spacing)
    for name, digest in original.items():
        if sha256_file(Path(name)) != digest:
            raise ValueError('Source changed during import')
    raw = np.fromfile(
        c.payload_path, dtype='<u1' if c.scalar_type == 'uint8' else '<u2')
    mapped = renderer_uint8(raw)
    mapped.tofile(directory/'render.raw')
    spacing = h.geometry.spacing or (1, 1, 1)
    (directory/'render.dat').write_text('ObjectFileName: render.raw\nResolution: %d %d %d\nSliceThickness: %g %g %g\nFormat: uchar\nEndianess: little\n' % (*h.dimensions, *spacing))
    result = {'version': 1, 'sourceHashes': original, 'volumeHash': sha256_file(c.payload_path), 'canonicalPath': str(c.payload_path), 'canonicalType': c.scalar_type, 'rendererPath': str(directory/'render.dat'), 'rendererPayloadHash': sha256_file(directory/'render.raw'), 'dimensions': h.dimensions, 'geometry': dataclasses.asdict(
        h.geometry), 'orientation': 'unknown', 'axisOrder': 'x-fastest; NumPy z,y,x', 'transforms': c.transforms, 'rendererMapping': 'uint8 unchanged; uint16 full-volume min/max integer scaling (ported renderer_uint8)', 'statistics': dataclasses.asdict(stats), 'spacingDefaulted': h.geometry.spacing is None}
    (directory/'volume.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    return result


def inspect_slice(volume, axis, index, directory):
    dims = volume['dimensions']
    ai = 'xyz'.index(axis)
    if type(index) != int or not 0 <= index < dims[ai]:
        raise ValueError('Slice index out of bounds')
    a = np.fromfile(Path(volume['rendererPath']).with_name(
        'render.raw'), dtype='u1').reshape(tuple(reversed(dims)))
    pixels = [lambda:a[:, :, index], lambda:a[:,
                                              index, :], lambda:a[index, :, :]][ai]()
    out = Path(directory)/'slice.png'
    Image.fromarray(pixels).save(out)
    return {'image': str(out), 'axis': axis, 'index': index, 'pixelAxes': [['y', 'z'], ['x', 'z'], ['x', 'y']][ai], 'pixelOrigin': [0, 0], 'verticalFlip': False, 'valueDomain': 'renderer-uint8', 'imageSha256': sha256_file(out)}
