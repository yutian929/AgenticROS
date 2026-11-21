"""Aggregate retained benchmark JSON without discarding failures or cold samples."""
import argparse
from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import statistics


def summary(rows):
    groups=defaultdict(list)
    for row in rows:
        groups[(row['layer'],row.get('phase',''),row.get('operation',''))].append(row)
    result=[]
    for key,items in sorted(groups.items()):
        record=dict(layer=key[0],phase=key[1],operation=key[2],samples=len(items),
                    successes=sum(bool(r['success']) for r in items),
                    errors=dict(Counter(r.get('error','unknown') for r in items if not r['success'])))
        record['success_rate']=record['successes']/len(items)
        for field in ('discovery_ms','response_ms','total_ms'):
            # Latency distribution includes timed-out and failed requests when measured.
            values=sorted(r[field] for r in items if field in r)
            if values:
                record[field]=dict(samples=len(values),p50=statistics.median(values),
                                   p95=values[math.ceil(.95*len(values))-1],maximum=max(values))
        result.append(record)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('paths',nargs='+',type=Path)
    args=p.parse_args();rows=[]
    for path in args.paths:
        files=sorted(path.glob('bench-*.json')) if path.is_dir() else [path]
        for file in files:rows.extend(json.loads(file.read_text()))
    print(json.dumps(summary(rows),indent=2))
