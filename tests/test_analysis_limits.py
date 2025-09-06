from pathlib import Path
from src.core.analysis import collect_repo_sample
from src.core.variant import VARIANT

def test_collect_repo_sample_limits(tmp_path: Path):
    # create >120 small files, but limit should cut at VARIANT.analysis_max_files
    root = tmp_path / 'repo'
    root.mkdir()
    for i in range(130):
        (root / f'f{i}.py').write_text(f'# file {i}\nprint({i})\n', encoding='utf-8')
    samples = collect_repo_sample(root)
    assert len(samples) <= VARIANT.analysis_max_files
    assert samples, 'samples should not be empty'
