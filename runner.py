from __future__ import annotations

import argparse
import runpy
import subprocess
import sys


MODULE_MAP = {
    ('commodity', 'spot_all'): 'src.streamers.commodity_spot',
    ('commodity', 'futures_all'): 'src.streamers.commodity_futures',
    ('commodity', 'options_all'): 'src.streamers.commodity_options',
    ('currency', 'spot_all'): 'src.streamers.currency_spot',
    ('currency', 'quotes_all'): 'src.streamers.currency_spot',
    ('currency', 'futures_all'): 'src.streamers.currency_futures',
    ('currency', 'options_all'): 'src.streamers.currency_options',
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Local (non-Docker) launcher for streamers. On EC2, prefer '
                     'orchestrator.py -- it gives each stream its own restart policy '
                     'and structured per-process logs, whereas this script simply '
                     'spawns subprocesses in one Python process and exits when any '
                     'of them do.'
    )
    parser.add_argument('--asset', choices=('commodity', 'currency', 'all'), required=True)
    parser.add_argument('--flow', choices=('spot_all', 'futures_all', 'options_all', 'quotes_all', 'all'), default='all')
    parser.add_argument('--mode', choices=('live', 'delayed', 'all'), default='delayed')
    parser.add_argument('--expiry-month', help='Used by futures/options flows')
    parser.add_argument('--batch-size', type=int)
    parser.add_argument('--flush-interval-seconds', type=float)
    parser.add_argument('--request-delay-seconds', type=float)
    return parser.parse_args()


def build_forward_args(args: argparse.Namespace, flow: str) -> list[str]:
    forward = ['--mode', args.mode]
    if flow in ('futures_all', 'options_all') and args.expiry_month:
        forward.extend(['--expiry-month', args.expiry_month])
    if args.batch_size is not None:
        forward.extend(['--batch-size', str(args.batch_size)])
    if args.flush_interval_seconds is not None:
        forward.extend(['--flush-interval-seconds', str(args.flush_interval_seconds)])
    if args.request_delay_seconds is not None:
        forward.extend(['--request-delay-seconds', str(args.request_delay_seconds)])
    return forward


def main() -> None:
    args = parse_args()

    if args.asset == 'all' or args.mode == 'all' or args.flow == 'all':
        processes: list[subprocess.Popen] = []
        try:
            assets = ('commodity', 'currency') if args.asset == 'all' else (args.asset,)
            modes = ('live', 'delayed') if args.mode == 'all' else (args.mode,)
            flows = ('spot_all', 'futures_all', 'options_all') if args.flow == 'all' else (args.flow,)

            for asset in assets:
                for mode in modes:
                    for flow in flows:
                        module_name = MODULE_MAP.get((asset, flow))
                        if not module_name:
                            print(f'Skipping unsupported combination: {asset}:{flow}')
                            continue
                        child_args = argparse.Namespace(**vars(args))
                        child_args.mode = mode
                        forward_args = build_forward_args(child_args, flow)
                        cmd = [sys.executable, '-m', module_name, *forward_args]
                        print(f'Starting {module_name} ({asset}:{flow}) with args: {forward_args}')
                        processes.append(subprocess.Popen(cmd))

            if not processes:
                raise SystemExit('No valid stream combination selected.')

            exit_codes = [proc.wait() for proc in processes]
            if any(code != 0 for code in exit_codes):
                raise SystemExit(f'One or more streams exited with non-zero codes: {exit_codes}')
            return
        except KeyboardInterrupt:
            print('Stopping all streams...')
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()
            for proc in processes:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    if proc.poll() is None:
                        proc.kill()
            raise SystemExit(130)

    module_name = MODULE_MAP.get((args.asset, args.flow))
    if not module_name:
        raise SystemExit(f'Unknown combination: {args.asset}:{args.flow}')

    forward_args = build_forward_args(args, args.flow)
    print(f'Starting {module_name} with args: {forward_args}')

    original_argv = sys.argv[:]
    try:
        sys.argv = [module_name] + forward_args
        runpy.run_module(module_name, run_name='__main__')
    finally:
        sys.argv = original_argv


if __name__ == '__main__':
    main()
