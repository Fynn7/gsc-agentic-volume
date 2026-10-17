"""File-based preflight avoids Windows PowerShell 5.1 native -c quoting."""
import os
import struct
import sys


if __name__=='__main__':
    if os.name!='nt' or struct.calcsize('P')!=8:
        print('64-bit native Windows Python is required: platform=%s, pointerBits=%d' % (os.name,struct.calcsize('P')*8),file=sys.stderr)
        sys.exit(1)
