"""Check feature selection against final EPS renders, not just mask helpers."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from pngvector.core import ConversionError, Options, convert
from pngvector.eps import inspect_eps


@pytest.mark.parametrize('mode', ['pixel', 'smooth'])
@pytest.mark.parametrize('colour', ['black', 'white'])
@pytest.mark.parametrize('cutoff', [0, 128, 255])
def test_cutoff_selects_expected_features_in_final_eps(tmp_path, mode, colour, cutoff):
    # Broad bands avoid renderer antialiasing at the sampled interior pixels.
    bands = [(value, 255) for value in (0, 64, 127, 128, 192, 254, 255)]
    bands += [(0, 0), (255, 0), (0, 127), (255, 127), (0, 128), (255, 128)]
    source = tmp_path / 'brightness bands.png'
    image = Image.new('RGBA', (len(bands) * 12, 24))
    for index, (brightness, alpha) in enumerate(bands):
        image.paste((brightness, brightness, brightness, alpha),
                    (index * 12, 0, (index + 1) * 12, image.height))
    image.save(source)
    original_hash = sha256(source.read_bytes()).hexdigest()
    result = convert(source, tmp_path / 'out',
                     Options(mode=mode, line_color=colour, line_threshold=cutoff))
    with Image.open(result.preview.with_name('brightness bands.preview-alpha.png')) as preview:
        preview = preview.convert('RGBA')
        for index, (brightness, alpha) in enumerate(bands):
            selected = alpha >= 128 and (brightness < cutoff if colour == 'black' else brightness >= cutoff)
            pixel = preview.getpixel((index * 12 + 6, 12))
            assert pixel[3] == (255 if selected else 0), (index, brightness, alpha, pixel)
            if selected:
                assert pixel[:3] == ((0, 0, 0) if colour == 'black' else (255, 255, 255))
    assert result.output.suffix == '.eps'
    assert inspect_eps(result.output)['vector_only']
    assert sha256(source.read_bytes()).hexdigest() == original_hash
    with result.output.with_suffix('.report.json').open() as handle:
        report = json.load(handle)
    assert report['options']['line_threshold'] == cutoff


@pytest.mark.parametrize('mode', ['pixel', 'smooth'])
@pytest.mark.parametrize('colour,cutoff,source_colour', [
    ('black', 0, 'black'), ('white', 255, '#fefefe'),
])
def test_cutoff_can_remove_every_feature_without_painting_background(tmp_path, mode, colour, cutoff, source_colour):
    source = tmp_path / 'empty selection.png'
    Image.new('RGBA', (24, 24), source_colour).save(source)
    result = convert(source, tmp_path / 'out',
                     Options(mode=mode, line_color=colour, line_threshold=cutoff))
    with Image.open(result.preview.with_name('empty selection.preview-alpha.png')) as preview:
        assert preview.convert('RGBA').getchannel('A').getextrema() == (0, 0)
    assert inspect_eps(result.output)['vector_only']


@pytest.mark.parametrize('cutoff', [-1, 256, 127.5, None, '128'])
def test_invalid_cutoff_does_not_create_output(tmp_path, cutoff):
    source = tmp_path / 'lines.png'
    Image.new('RGB', (12, 12), '#808080').save(source)
    output = tmp_path / 'out'
    with pytest.raises(ConversionError, match='(?i)(cutoff|threshold|brightness)'):
        convert(source, output, Options(line_color='black', line_threshold=cutoff))
    assert not output.exists()


@pytest.mark.parametrize('mode', ['pixel', 'smooth'])
@pytest.mark.parametrize('colour', ['black', 'white'])
def test_opacity_cutoff_recovers_faint_lines_without_revealing_hidden_rgb(tmp_path, mode, colour):
    source = tmp_path / 'faint lines.png'
    image = Image.new('RGBA', (36, 24), colour)
    image.paste((*image.getpixel((0, 0))[:3], 0), (0, 0, 12, 24))
    image.paste((*image.getpixel((12, 0))[:3], 64), (12, 0, 24, 24))
    image.save(source)
    for cutoff in (1, 128):
        result = convert(source, tmp_path / 'out', Options(
            mode=mode, line_color=colour, line_alpha_threshold=cutoff,
        ))
        with Image.open(result.preview.with_name('faint lines.preview-alpha.png')) as preview:
            assert preview.getpixel((6, 12))[3] == 0
            assert preview.getpixel((18, 12))[3] == (255 if cutoff == 1 else 0)
            assert preview.getpixel((30, 12))[3] == 255


@pytest.mark.parametrize('cutoff', [0, 256, 127.5, None])
def test_invalid_opacity_cutoff_does_not_create_output(tmp_path, cutoff):
    source = tmp_path / 'lines.png'
    Image.new('RGBA', (12, 12), (0, 0, 0, 64)).save(source)
    output = tmp_path / 'out'
    with pytest.raises(ConversionError, match='(?i)(cutoff|threshold|opacity)'):
        convert(source, output, Options(line_color='black', line_alpha_threshold=cutoff))
    assert not output.exists()


def run_cli(source, output, *arguments):
    return subprocess.run(
        [sys.executable, '-m', 'pngvector.cli', str(source), '-o', str(output), *arguments],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize('colour', ['black', 'white'])
def test_cli_cutoff_changes_exported_feature_coverage(tmp_path, colour):
    source = tmp_path / 'gray feature.png'
    Image.new('RGB', (24, 24), '#808080').save(source)
    for cutoff in (128, 129):
        completed = run_cli(source, tmp_path / 'out', '--mode', 'pixel',
                            '--line-color', colour, '--line-threshold', str(cutoff))
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        preview_path = Path(result['preview']).with_name('gray feature.preview-alpha.png')
        with Image.open(preview_path) as preview:
            expected = (128 < cutoff if colour == 'black' else 128 >= cutoff)
            assert preview.convert('RGBA').getpixel((12, 12))[3] == (255 if expected else 0)


@pytest.mark.parametrize('value', ['-1', '256', '127.5'])
@pytest.mark.parametrize('estimate_only', [False, True])
def test_cli_rejects_invalid_cutoff_even_for_estimates(tmp_path, value, estimate_only):
    source = tmp_path / 'lines.png'
    Image.new('RGB', (12, 12), 'black').save(source)
    output = tmp_path / 'out'
    arguments = ['--line-color', 'black', '--line-threshold', value]
    if estimate_only:
        arguments.append('--estimate-only')
    completed = run_cli(source, output, *arguments)
    assert completed.returncode != 0
    assert 'line-threshold' in completed.stderr
    assert not output.exists()
