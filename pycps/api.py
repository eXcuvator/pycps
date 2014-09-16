# -*- coding: utf-8 -*-
"""
Functions to step through

1. Downloading Data Dictionaries and Monthly files
2. Parsing downloaded files
3. Storing parsed files in HDF stores.
4. Merging stored files.

Define your preferences in settings.json in this folder.
"""
import json
import logging
from pathlib import Path
from functools import partial
from operator import itemgetter

import pandas as pd

import pycps.downloaders as dl
import pycps.parsers as par

logger = logging.getLogger(__name__)
# TODO argparse CLI

_HERE_ = Path(__file__).parent
#-----------------------------------------------------------------------------
# Downloading
#-----------------------------------------------------------------------------

def download(overwrite_cached=False, config_file = False):
    if config_file == False:
        config_file = str(_HERE_ / 'settings.json')
    settings = par.read_settings(config_file)
    cached_dd = dl.check_cached(settings['dd_path'], kind='dictionary')
    cached_month = dl.check_cached(settings['monthly_path'], kind='data')

    dds = dl.all_monthly_files(kind='dictionary')
    dds = filter(itemgetter(1), dds)  # make sure not None cpsdec!
    dds = dl.filter_dds(dds, months=[par._month_to_dd(settings['date_start']),
                                     par._month_to_dd(settings['date_end'])])

    data = dl.all_monthly_files()
    data = dl.filter_monthly_files(data, months=[[settings['date_start'],
                                                  settings['date_end']]])
    if not overwrite_cached:
        def is_new(x, cache=None):
            return dl.rename_cps_monthly(x[1]) not in cache

        dds = filter(partial(is_new, cache=cached_dd), dds)
        data = filter(partial(is_new, cache=cached_month), data)

    for month, renamed in dds:
        dl.download_month(month, Path(settings['dd_path']))
        logging.info("Downloaded {}".format(renamed))

    for month, renamed in data:
        dl.download_month(month, Path(settings['monthly_path']))
        logging.info("Downloaded {}".format(renamed))

#-----------------------------------------------------------------------------
# Parsing
#-----------------------------------------------------------------------------

def parse(config_file = False):
    if config_file == False:
        config_file = str(_HERE_ / 'settings.json')
    settings_file = str(config_file)
    settings = par.read_settings(settings_file)

    dd_path = Path(settings['dd_path'])
    dds = [x for x in dd_path.iterdir() if x.suffix in ('.ddf', '.asc')]
    monthly_path = Path(settings['monthly_path'])
    months = [x for x in monthly_path.iterdir() if x.suffix in ('.Z', '.zip')]

    settings['raise_warnings'] = False

    logger.info("Reading Data file")
    with (_HERE_ / 'data.json').open() as f:
        data = json.load(f)

    id_cols = ['HRHHID', 'HRHHID2', 'PULINENO']
    dds = dds + [_HERE_ / Path('cpsm2014-01.ddf')]

    for dd in dds:
        parser = par.DDParser(dd, settings)
        df = parser.run()
        parser.write(df)
        logging.info("Added {} to {}".format(dd, parser.store_path))

    for month in months:
        dd_name = par._month_to_dd(str(month))
        store_path = settings['monthly_store']

        dd = pd.read_hdf(settings['dd_store'], key=dd_name)
        cols = data['columns_by_dd'][dd_name]
        sub_dd = dd[dd.id.isin(cols)]

        if len(cols) != len(sub_dd):
            missing = set(cols) - set(sub_dd.id.values)
            raise ValueError("IDs {} are not in the Data "
                             "Dictionary".format(missing))

        with pd.get_store(store_path) as store:
            try:
                cached_cols = store.select(month.stem).columns
                newcols = set(cols) - set(cached_cols) - set(id_cols)
                if len(newcols) == 0:
                    logger.info("Using cached {}".format(month.stem))
                    continue

            except KeyError:
                pass

        # Assuming no new rows
        df = par.read_monthly(str(month), sub_dd)
        df = par.fixup_by_dd(df, dd_name)
        # do special stuff like compute HRHHID2, bin things, etc.
        # TODO: special stuff

        df = df.set_index(id_cols)
        par.write_monthly(df, store_path, month.stem)
        logging.info("Added {} to {}".format(month, settings['monthly_store']))

# -----------------------------------------------------------------------------
# Merge
# -----------------------------------------------------------------------------


def merge():
    # settings = par.read_settings(str(_HERE_ / 'settings.json'))
    store = pd.HDFStore('data/monthly/monthly.hdf')  # from settings
    months = (x.strftime('cpsm%Y-%m') for x in m.make_months('1995-09-01'))
    months = enumerate(months, 1)

    mis, month = next(months)
    df0 = store.select(month).query('HRMIS == @mis')

    match_funcs = [m.match_age, m.match_sex, m.match_race]
    dfs = [df0]
    for mis, month in months:
        dfn = store.select(month).query('HRMIS == @mis')
        dfs.append(m.match(df0, dfn, match_funcs))

    df = m.merge(dfs)
    df = df.sort_index()
    df = m.make_wave_id(df)
