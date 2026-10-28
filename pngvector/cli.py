"""Batch command line interface; no GUI or network needed for conversion."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import signal
from threading import Event
from .core import Options, convert, estimate, ConversionError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Trace local PNGs to vector EPS. Partial alpha requires an explicit choice.')
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('-o', '--output-dir', type=Path, required=True)
    parser.add_argument('--mode', choices=['smooth', 'pixel'], default='smooth')
    parser.add_argument('--tracer', choices=['auto', 'vtracer', 'potrace'], default='auto')
    parser.add_argument('--detail', type=int, default=6, choices=range(0, 11))
    parser.add_argument('--smoothing', type=float, default=0.0)
    parser.add_argument('--color-precision', type=int, default=8, choices=range(1, 9))
    parser.add_argument('--min-component', type=int, default=0)
    parser.add_argument('--alpha', choices=['ask', 'composite', 'retain'], default='ask', help='retain produces SVG/PDF instead of EPS for partial alpha')
    parser.add_argument('--background', default='#ffffff', help='explicit composite and comparison background #RRGGBB')
    parser.add_argument('--dpi', type=float, help='override PNG DPI; absent PNG DPI defaults to 96')
    parser.add_argument('--allow-large', action='store_true', help='acknowledge displayed content-dependent size estimate')
    parser.add_argument('--estimate-only', action='store_true')
    args = parser.parse_args(argv)
    options = Options(mode=args.mode, tracer=args.tracer, detail=args.detail, smoothing=args.smoothing,
                      color_precision=args.color_precision, min_component=args.min_component,
                      alpha_policy=args.alpha, background=args.background, dpi=args.dpi,
                      force_large=args.allow_large)
    stop = Event()
    previous_handler = signal.signal(signal.SIGINT, lambda *_: stop.set())
    failed = 0
    for source in args.inputs:
        if stop.is_set():
            return 130
        try:
            info = estimate(source, options)
            print(json.dumps({'source': str(source), 'estimate': info}, ensure_ascii=False), file=sys.stderr)
            if args.estimate_only:
                continue
            result = convert(source, args.output_dir, options, cancelled=stop.is_set,
                             progress=lambda fraction, text: print(f'{fraction:5.0%} {text}', file=sys.stderr))
            print(json.dumps(asdict(result), ensure_ascii=False, default=str))
        except KeyboardInterrupt:
            print('Interrupted; no source files were modified.', file=sys.stderr)
            return 130
        except (ConversionError, OSError, ValueError) as exc:
            if stop.is_set():
                print('Cancelled; incomplete output removed.', file=sys.stderr)
                signal.signal(signal.SIGINT, previous_handler)
                return 130
            failed += 1
            print(f'{source}: {exc}', file=sys.stderr)
    signal.signal(signal.SIGINT, previous_handler)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
