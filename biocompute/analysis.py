"""Real local analysis of FASTQ sequences; not a simulated job."""
from __future__ import annotations
import csv
import gzip
import html
import json
from pathlib import Path

from .config import safe_path


def fastq_stats(path: Path, max_reads: int) -> dict:
    opener = gzip.open if path.suffix == '.gz' else open
    n = bases = gc = length_sum = qsum = 0
    with opener(path, 'rt', encoding='utf-8') as handle:
        while n < max_reads:
            quartet = [handle.readline() for _ in range(4)]
            if not quartet[0]:
                break
            if not all(quartet) or not quartet[0].startswith('@') or not quartet[2].startswith('+'):
                raise ValueError(f'Invalid FASTQ record in {path.name}, read {n+1}')
            seq = quartet[1].rstrip('\r\n')
            qual = quartet[3].rstrip('\r\n')
            if len(seq) != len(qual):
                raise ValueError(f'Sequence/quality length mismatch in {path.name}, read {n+1}')
            n += 1
            length_sum += len(seq)
            bases += len(seq)
            gc += sum(1 for base in seq.upper() if base in ('G', 'C'))
            qsum += sum(ord(ch) - 33 for ch in qual)
    return {'reads': n, 'mean_length': round(length_sum / n, 2) if n else 0,
            'gc_percent': round(100 * gc / bases, 2) if bases else 0,
            'mean_quality': round(qsum / bases, 2) if bases else 0}


def run_fastq_qc(samples: list, params: dict, destination: Path, emit):
    destination.mkdir(parents=True, exist_ok=True)
    if not samples:
        raise ValueError('Choose at least one sample with FASTQ input files')
    max_reads = int(params.get('max_reads', 100000))
    min_length = int(params.get('min_length', 20))
    rows = []
    for idx, sample in enumerate(samples):
        sample_name = sample['sample']
        paths = [sample.get('file1'), sample.get('file2')]
        paths = [safe_path(p) for p in paths if p]
        if not paths:
            raise ValueError(f'Sample {sample_name} has no input FASTQ')
        for read_number, path in enumerate(paths, 1):
            emit(f'[{idx+1}/{len(samples)}] QC {sample_name} R{read_number}: {path.name}')
            stats = fastq_stats(path, max_reads)
            rows.append({'sample': sample_name, 'condition': sample.get('condition', ''),
                         'replicate': sample.get('replicate', 1), 'read': f'R{read_number}',
                         'file': path.name, **stats,
                         'warning': 'Short mean read length' if stats['mean_length'] < min_length else ''})
            emit(f"    {stats['reads']} reads, GC {stats['gc_percent']}%, mean Q {stats['mean_quality']}")
    fields = ['sample', 'condition', 'replicate', 'read', 'file', 'reads', 'mean_length', 'gc_percent', 'mean_quality', 'warning']
    with (destination / 'qc_metrics.csv').open('w', newline='', encoding='utf-8') as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = {'sample_count': len(samples), 'file_count': len(rows), 'read_count': sum(r['reads'] for r in rows), 'metrics': rows}
    (destination / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    table_rows = '\n'.join('<tr>' + ''.join(f'<td>{html.escape(str(row[key]))}</td>' for key in fields) + '</tr>' for row in rows)
    report = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>BioCompute FASTQ QC</title><style>body{{font:15px system-ui;max-width:1250px;margin:60px auto;padding:0 24px;background:#0d1323;color:#eaf0ff}}table{{border-collapse:collapse;width:100%;margin-top:24px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #2b3851}}th{{color:#7eceff}}.sub{{color:#9caabd}}h1{{font-size:32px}}</style></head><body><h1>FASTQ Quality Explorer</h1><p class="sub">{len(samples)} samples · {len(rows)} FASTQ files · {summary['read_count']} reads analyzed</p><table><thead><tr>{''.join('<th>'+html.escape(k.replace('_',' ').title())+'</th>' for k in fields)}</tr></thead><tbody>{table_rows}</tbody></table><p class="sub">Produced by BioCompute local QC engine. This is a quick overview, not a substitute for FastQC/MultiQC.</p></body></html>'''
    (destination / 'report.html').write_text(report, encoding='utf-8')
    emit(f'Completed. {len(rows)} file metrics saved to results/')
