import subprocess
import zipfile
from pathlib import Path


def test_wheel_contains_skill(tmp_path):
    root = Path(__file__).parent.parent
    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(tmp_path)], cwd=root, check=True, capture_output=True, timeout=300)
    [wheel] = tmp_path.glob('*.whl')
    names = zipfile.ZipFile(wheel).namelist()
    assert 'repeat_o_scope/skill/SKILL.md' in names, names
