import numpy as np
import pandas as pd
from scipy.stats import beta

from ceam.interpolation import Interpolation

from ceam_inputs import gbd
from ceam_inputs.gbd_ms_auxiliary_functions import get_age_group_midpoint_from_age_group_id


def sll_ppf(percentile, location, scale, shape):
    """ compute the value of the shifted-log-logistic distribution
    Parameters
    ----------
    percentile : float or array of floats between 0 and 1
    location, scale, shape : floats or array of floats, scale > 0

    Returns
    -------
    returns float or array of floats
    """
    assert np.all(scale > 0), 'scale must be positive'

    percentile = np.atleast_1d(percentile)
    location = np.broadcast_to(location, percentile.shape)
    scale = np.broadcast_to(scale, percentile.shape)
    shape = np.broadcast_to(shape, percentile.shape)

    f = 1. - percentile
    idx = f != 0

    z = 1/shape[idx] * ((1/f[idx] - 1)**shape[idx] - 1)
    x = location[idx] + scale[idx]*z

    result = np.full(f.shape, np.inf)
    result[idx] = x

    if len(result) > 1:
        return result
    else:
        return result[0]


def _fpg_ppf(parameters):
    def inner(percentile):
        if parameters.empty:
            return pd.Series()
        else:
            return sll_ppf(percentile, parameters['loc'], parameters['scale'], parameters['error'])
    return inner


def get_fpg_distributions(location_id, year_start, year_end, draw):
    parameters = pd.DataFrame()
    columns = ['age_group_id', 'sex_id', 'year_id', 'sll_loc_{}'.format(draw),
               'sll_scale_{}'.format(draw), 'sll_error_{}'.format(draw)]
    sub_location_ids = gbd.get_subregions(location_id)
    if not sub_location_ids:
        sub_location_ids = [location_id]

    for sub_location_id in sub_location_ids:
        for sex_id in [1, 2]:
            for year_id in np.arange(year_start, year_end + 1, 5):
                df = gbd.get_data_from_auxiliary_file('Fasting Plasma Glucose Distributions',
                                                      location_id=sub_location_id,
                                                      year_id=year_id,
                                                      sex_id=sex_id)
                df = df[columns]
                df['location'] = sub_location_id
                parameters = pd.concat([parameters, df])
    parameters = parameters.drop_duplicates()
    parameters.loc[parameters.sex_id == 1, 'sex'] = 'Male'
    parameters.loc[parameters.sex_id == 2, 'sex'] = 'Female'
    parameters = get_age_group_midpoint_from_age_group_id(parameters)
    parameters = parameters[['age', 'sex', 'year_id', 'location', 'sll_loc_{}'.format(draw),
                             'sll_scale_{}'.format(draw), 'sll_error_{}'.format(draw)]]
    parameters.columns = ['age', 'sex', 'year', 'location', 'loc', 'scale', 'error']

    return Interpolation(parameters[['age', 'year', 'sex', 'error', 'scale', 'loc', 'location']],
                         categorical_parameters=('sex', 'location'),
                         continuous_parameters=('age', 'year'),
                         func=_fpg_ppf)


def _bmi_ppf(parameters):
    return beta(a=parameters['a'], b=parameters['b'], scale=parameters['scale'], loc=parameters['loc']).ppf


def get_bmi_distributions(location_id, year_start, year_end, draw, func=_bmi_ppf):
    a = pd.DataFrame()
    b = pd.DataFrame()
    loc = pd.DataFrame()
    scale = pd.DataFrame()
    for sex_id in [1, 2]:
        for year_id in np.arange(year_start, year_end + 1, 5):
            a = a.append(gbd.get_data_from_auxiliary_file('Body Mass Index Distributions',
                                                          parameter='bshape1',
                                                          location_id=location_id,
                                                          year_id=year_id,
                                                          sex_id=sex_id))

            b = b.append(gbd.get_data_from_auxiliary_file('Body Mass Index Distributions',
                                                          parameter='bshape2',
                                                          location_id=location_id,
                                                          year_id=year_id,
                                                          sex_id=sex_id))
            loc = loc.append(gbd.get_data_from_auxiliary_file('Body Mass Index Distributions',
                                                              parameter='mm',
                                                              location_id=location_id,
                                                              year_id=year_id,
                                                              sex_id=sex_id))
            scale = scale.append(gbd.get_data_from_auxiliary_file('Body Mass Index Distributions',
                                                                  parameter='scale',
                                                                  location_id=location_id,
                                                                  year_id=year_id,
                                                                  sex_id=sex_id))
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

    return Interpolation(distributions[['age', 'year', 'sex', 'a', 'b', 'scale', 'loc']],
                         categorical_parameters=('sex',),
                         continuous_parameters=('age', 'year'),
                         func=func)
