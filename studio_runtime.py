"""Resources for both the folder build and the single-file Windows release."""
import shutil
import sys
from pathlib import Path

VERSION='2.1.0'


def resource_root():
    if not getattr(sys,'frozen',False):
        return Path(__file__).resolve().parent
    beside=Path(sys.executable).resolve().parent
    if (beside/'web/studio.html').is_file() and (beside/'data/wardrobe').is_dir():
        return beside
    root=beside/'DrinkStudio_Data'
    bundle=Path(sys._MEIPASS)
    for name in ('data/wardrobe','web','models','docs'):
        source=bundle/name
        if not source.is_dir(): continue
        for path in source.rglob('*'):
            if not path.is_file(): continue
            dest=root/name/path.relative_to(source)
            if not dest.exists():
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,dest)
    return root
