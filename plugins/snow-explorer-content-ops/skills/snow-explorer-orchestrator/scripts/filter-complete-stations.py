"""Keep only complete creation candidates; never create an empty import file.

Usage: python filter-complete-stations.py input.json output.json report.json
The report lists excluded stations and every missing field. No source is edited.
"""
import copy
import json
import sys
from pathlib import Path

STATION_FIELDS = (
    'name', 'slug', 'country_code', 'website_url', 'latitude', 'longitude',
    'altitude_min_m', 'altitude_max_m', 'ski_area_km', 'pistes_count', 'lifts_count',
    'season_open_date', 'season_close_date', 'description_md',
)
BLOCKS = {'pistes': ('green', 'blue', 'red', 'black'),
          'remontees': ('tire_fesses', 'telesieges', 'telepheriques'), 'snowpark': ('count',)}


def missing(value):
    return value is None or isinstance(value, str) and not value.strip()


def filter_complete(batch):
    output = copy.deepcopy(batch)
    kept, excluded = [], []
    for record in batch['document']['stations']:
        station = record['station']
        fields = ['station.' + key for key in STATION_FIELDS if missing(station.get(key))]
        if missing(station.get('region_id')) and missing(station.get('region_name')):
            fields.append('station.region')
        if station.get('country_code') == 'FR' and missing(station.get('department')):
            fields.append('station.department')
        for block, keys in BLOCKS.items():
            fields.extend(block + '.' + key for key in keys if missing(record.get(block, {}).get(key)))
        season = next((s for s in record.get('forfaits_avances', {}).get('seasons', [])
                       if s.get('season') == batch.get('scope', {}).get('season')), None)
        if not season or not season.get('periods') or not season.get('passes') or any(
                not p.get('prices') for p in season['passes']):
            fields.append('forfaits_avances.seasons')
        for role in ('cover', 'logo', 'piste_map'):
            asset = next((a for a in batch['media'] if a.get('station_slug') == station.get('slug') and a.get('role') == role), None)
            if not asset or asset.get('status') != 'found' or missing(asset.get(
                    'data_base64' if asset.get('source_kind') == 'generated' else 'source_url')):
                fields.append('media.' + role)
        if fields:
            excluded.append({'slug': station.get('slug'), 'name': station.get('name'), 'missing_fields': fields})
        else:
            kept.append(record)
    slugs = {record['station']['slug'] for record in kept}
    output['document']['stations'] = copy.deepcopy(kept)
    output['media'] = [asset for asset in output['media'] if asset.get('station_slug') in slugs]
    output['research'] = [row for row in output.get('research', []) if row.get('station_slug') in slugs]
    return output, excluded


if __name__ == '__main__':
    source, target, report = map(Path, sys.argv[1:])
    if len({p.resolve() for p in (source, target, report)}) != 3 or target.exists():
        raise SystemExit('Use three different paths and a new output file.')
    batch, excluded = filter_complete(json.loads(source.read_text()))
    report.write_text(json.dumps({'included': [s['station']['slug'] for s in batch['document']['stations']],
                                 'excluded': excluded}, ensure_ascii=False, indent=2) + '\n')
    if batch['document']['stations']:
        target.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + '\n')
    else:
        print('Aucune station complète : aucun fichier importable créé.', file=sys.stderr)
        sys.exit(2)
