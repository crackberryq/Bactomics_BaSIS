#!/usr/bin/env python3
"""Create a reporting-only review of existing DeepSafe output."""
import argparse, hashlib, html, json
from datetime import datetime, timezone
from pathlib import Path
from basis.deep_reporting import completion_summary, tree_reference_summary
from basis.report import _render_candidate

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', default='study-isolate1/results-deep-auto/results/isolate1/deepsafe/result.json')
    args = parser.parse_args()
    source = Path(args.result).resolve(); raw = source.read_bytes(); record = json.loads(raw)
    if record.get('module') != 'deepsafe': raise ValueError('Expected DeepSafe result')
    out = source.parents[3] / 'reporting-reviews' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    summary = completion_summary(record)
    review = {'source': str(source), 'source_sha256': hashlib.sha256(raw).hexdigest(),
              'completion': summary, 'native_analyses_run': False,
              'candidates': [{'protein_id':c.get('protein_id'), 'assessment':c.get('assessment'),
                              'tree_review':tree_reference_summary(c)} for c in record['candidates'] if c.get('selected')]}
    (out/'review.json').write_text(json.dumps(review, indent=2)+'\n')
    content = ''.join(_render_candidate(c, out) for c in record['candidates'] if c.get('selected'))
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>BaSIS DeepSafe reporting review</title><style>body{font:16px system-ui;margin:2em}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5em;overflow-wrap:anywhere}pre{white-space:pre-wrap}details{margin:1em 0}</style><h1>DeepSafe reporting review</h1><p>Existing evidence; no native analyses run. Original results and exit status preserved.</p><pre>'+html.escape(json.dumps(summary,indent=2))+'</pre>'+content)
    print(json.dumps(summary, indent=2)); print('Report:',out/'report.html'); print('Evidence:',out/'review.json')
if __name__ == '__main__':main()
