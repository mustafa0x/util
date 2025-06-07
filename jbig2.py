# /// script
# dependencies = [
#   "tqdm",
# ]
# ///
# `brew install jbig2enc poppler`

from pathlib import Path
from subprocess import run, Popen, PIPE, STDOUT
from tempfile import TemporaryDirectory
import re, sys
from tqdm import tqdm


def page_count(pdf: Path) -> int:
    out = run(f'pdfinfo "{pdf}"', shell=True, check=True, capture_output=True, text=True).stdout
    m = re.search(r'^Pages:\s+(\d+)', out, re.M)
    return int(m.group(1)) if m else 0


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: jbig2.py INPUT.pdf [OUTPUT.pdf]')
        sys.exit(1)

    in_pdf = Path(sys.argv[1]).resolve()
    out_pdf = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) > 2
        else in_pdf.with_stem(in_pdf.stem + '_jbig2').with_suffix('.pdf')
    )

    pages = page_count(in_pdf)
    if not pages:
        sys.exit('✗ Could not determine page count.')

    with TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) Rasterise page-by-page → PBM
        pbar = tqdm(total=pages, desc='Rasterising', unit='pg')
        for pg in range(1, pages + 1):
            base = f'{tmp}/page-{pg:05d}'
            run(f'pdftoppm -mono -r 300 -singlefile -f {pg} -l {pg} "{in_pdf}" "{base}"', shell=True, check=True)
            pbar.update(1)
        pbar.close()

        # 2) JBIG2 encode   (-s symbol mode · -p create page streams)
        ebar = tqdm(total=pages, desc='JBIG2 encoding', unit='pg')
        enc = f'jbig2 -s -p -v -b "{tmp}/out" {tmp}/page-*.pbm'  # wildcard unquoted
        with Popen(enc, shell=True, text=True, stdout=PIPE, stderr=STDOUT, bufsize=1) as proc:
            for line in proc.stdout:
                if line.lstrip().startswith('Processing '):
                    ebar.update(1)
            proc.wait()
        ebar.close()
        if proc.returncode:
            sys.exit(proc.returncode)

        # 3) Stitch streams → single PDF  (stdout → file)
        print('Stitching streams into PDF…')
        with open(out_pdf, 'wb') as fout:
            run(['jbig2topdf.py', f'{tmp}/out'], stdout=fout, check=True)

    orig_size = in_pdf.stat().st_size
    new_size = out_pdf.stat().st_size
    reduction = 100 * (orig_size - new_size) / orig_size
    print(f'Original:   {orig_size / 1_048_576:.2f} MB')
    print(f'Compressed: {new_size / 1_048_576:.2f} MB  (−{reduction:.1f} %)')

    print(f'✓ Compressed PDF written to: {out_pdf}')


if __name__ == '__main__':
    main()
