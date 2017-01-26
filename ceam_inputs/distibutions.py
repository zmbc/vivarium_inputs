import numpy as np
import pandas as pd

from scipy.stats import fisk, beta

from joblib import Memory

from hierarchies import dbtrees

from ceam.interpolation import Interpolation

from ceam_inputs.util import get_cache_directory
from ceam_inputs.auxiliary_files import open_auxiliary_file, auxiliary_file_path
from ceam_inputs.gbd_ms_auxiliary_functions import get_age_group_midpoint_from_age_group_id

memory = Memory(cachedir=get_cache_directory(), verbose=1)

def get_fpg_distributions(location_id, year_start, year_end, draw):
    parameters = pd.DataFrame()
    columns = ['age_group_id', 'sex_id', 'year_id', 'sll_loc_{}'.format(draw), 'sll_scale_{}'.format(draw), 'sll_error_{}'.format(draw)]
    sub_location_ids = dbtrees.loctree(None, location_set_id=2).get_node_by_id(location_id).children
    for sub_location_id in sub_location_ids:
        for sex_id in [1,2]:
            for year_id in np.arange(year_start, year_end + 1, 5):
                with open_auxiliary_file('Fasting Plasma Glucose Distributions',
                        location_id=location_id,
                        year_id=year_id,
                        sex_id=sex_id) as f:
                    df = pd.read_csv(f)[columns]
                    df['location'] = sub_location_id
                    parameters = pd.concat([parameters, df])
    parameters = parameters.drop_duplicates()
    parameters.loc[parameters.sex_id == 1, 'sex'] = 'Male'
    parameters.loc[parameters.sex_id == 2, 'sex'] = 'Female'
    parameters = get_age_group_midpoint_from_age_group_id(parameters)
    parameters = parameters[['age', 'sex', 'year_id', 'location', 'sll_loc_{}'.format(draw), 'sll_scale_{}'.format(draw), 'sll_error_{}'.format(draw)]]
    parameters.columns = ['age', 'sex', 'year', 'location', 'loc', 'scale', 'error']
    return Interpolation(
            parameters[['age', 'year', 'sex', 'error', 'scale', 'loc', 'location']],
            categorical_parameters=('sex', 'location'),
            continuous_parameters=('age', 'year'),
            #TODO: This is not the right distribution to be using. Should be this: https://en.wikipedia.org/wiki/Shifted_log-logistic_distribution
            func=lambda p: fisk(c=p['error'], loc=p['loc'], scale=p['scale']).ppf,
            )

def _bmi_ppf(parameters):
   return beta(a=parameters['a'], b=parameters['b'], scale=parameters['scale'], loc=parameters['loc']).ppf

@memory.cache
def get_bmi_distributions(location_id, year_start, year_end, draw, func=_bmi_ppf):
    a = pd.DataFrame()
    b = pd.DataFrame()
    loc = pd.DataFrame()
    scale = pd.DataFrame()
    for sex_id in [1,2]:
        for year_id in np.arange(year_start, year_end + 1, 5):
            with open_auxiliary_file('Body Mass Index Distributions',
                                     parameter='bshape1',
                                     location_id=location_id,
                                     year_id=year_id,
                                     sex_id=sex_id) as f:
                a = a.append(pd.read_csv(f))
            with open_auxiliary_file('Body Mass Index Distributions',
                                     parameter='bshape2',
                                     location_id=location_id,
                                     year_id=year_id,
                                     sex_id=sex_id) as f:
                b = b.append(pd.read_csv(f))
            with open_auxiliary_file('Body Mass Index Distributions',
                                     parameter='mm',
                                     location_id=location_id,
                                     year_id=year_id,
                                     sex_id=sex_id) as f:
                loc = loc.append(pd.read_csv(f))
            with open_auxiliary_file('Body Mass Index Distributions',
                                     parameter='scale',
                                     location_id=location_id,
                                     year_id=year_id,
                                     sex_id=sex_id) as f:
                scale = scale.append(pd.read_csv(f))

    a = a.set_index(['age_group_id', 'sex_id', 'year_id'])
    b = b.set_index(['age_group_id', 'sex_id', 'year_id'])
    loc = loc.set_index(['age_group_id', 'sex_id', 'year_id'])
    scale = scale.set_index(['age_group_id', 'sex_id', 'year_id'])

    distributions = pd.DataFrame()
    distributions['a'] = a['draw_{}'.format(draw)]
    distributions['b'] = b['draw_{}'.format(draw)]
    distributions['loc'] = loc['draw_{}'.format(draw)]
    distributions['scale'] = scale['draw_{}'.format(draw)]

    distributions = distributions.reset_index()
    distributions = get_age_group_midpoint_from_age_group_id(distributions)
    distributions['year'] = distributions.year_id
    distributions.loc[distributions.sex_id == 1, 'sex'] = 'Male'
    distributions.loc[distributions.sex_id == 2, 'sex'] = 'Female'

    return Interpolation(
            distributions[['age', 'year', 'sex', 'a', 'b', 'scale', 'loc']],
            categorical_parameters=('sex',),
            continuous_parameters=('age', 'year'),
            func=func,
            )