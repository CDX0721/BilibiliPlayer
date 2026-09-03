"""生成 cloud-init NoCloud 种子 ISO（user-data / meta-data）。"""
import sys
from pathlib import Path

import pycdlib

BASE = Path(__file__).resolve().parent.parent
PUBKEY = (BASE / "data" / "guest_key.pub").read_text().strip()

USER_DATA = f"""#cloud-config
users:
  - name: tester
    plain_text_passwd: 'test1234'
    lock_passwd: false
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - {PUBKEY}
ssh_pwauth: true
package_update: true
packages: [ffmpeg, libportaudio2]
runcmd:
  - pip3 install --break-system-packages numpy scipy sounddevice requests websocket-client imageio-ffmpeg || true
"""

META_DATA = "instance-id: bp-vm-001\nlocal-hostname: bpvm\n"

out = BASE / "guest" / "seed.iso"
out.parent.mkdir(exist_ok=True)
iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, vol_ident="cidata")
iso.add_file(str(_tmp_ud := out.parent / "_user-data"), "/user-data.;1") if False else None
ud, md = out.parent / "_user-data", out.parent / "_meta-data"
ud.write_text(USER_DATA)
md.write_text(META_DATA)
iso.add_file(str(ud), "/USERDATA.;1")
iso.add_file(str(md), "/METADATA.;1")
iso.write(str(out))
iso.close()
ud.unlink()
md.unlink()
print("seed:", out, out.stat().st_size, "bytes")
