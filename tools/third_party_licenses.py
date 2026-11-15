"""Write THIRD_PARTY_LICENSES.txt: licences of Python and the packages bundled into page.exe."""
import importlib.metadata as md
import pathlib
import sys

PACKAGES = ["customtkinter", "darkdetect", "packaging", "tkinterdnd2", "pillow"]
parts = ["Licences of the code bundled into page.exe.\n"]
py = pathlib.Path(sys.base_prefix) / "LICENSE.txt"
if not py.exists():
    sys.exit(f"Python licence not found at {py}")
parts.append(f"\n==== Python {sys.version.split()[0]} (includes Tcl/Tk and other bundled libraries) ====\n\n"
             + py.read_text(encoding="utf-8", errors="replace"))
for name in PACKAGES:
    dist = md.distribution(name)
    files = [f for f in (dist.files or [])
             if f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]
    if not files:
        sys.exit(f"no licence file for {name}")
    for f in files:
        parts.append(f"\n==== {dist.metadata['Name']} {dist.version} ({f.name}) ====\n\n"
                     + f.locate().read_text(encoding="utf-8", errors="replace"))
pathlib.Path("THIRD_PARTY_LICENSES.txt").write_text("".join(parts), encoding="utf-8")
print(len(parts) - 1, "sections")
