import joblib
from joblib import Memory

import pandas as pd

from ceam import config
from ceam_inputs import gbd_ms_functions as functions
from ceam_inputs import distributions
from ceam_inputs.util import get_cache_directory, gbd_year_range
from ceam_inputs.gbd_mapping import meid

memory = Memory(cachedir=get_cache_directory(), verbose=1)

from ceam_public_health.util.risk import RiskEffect


def get_excess_mortality(modelable_entity_id):
    """Get excess mortality associated with a modelable entity.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'rate' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws,
            'rate',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=9,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df

def get_incidence(modelable_entity_id):
    """Get incidence rates for a modelable entity.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'rate' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws,
            'rate',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=6,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df


def get_cause_specific_mortality(modelable_entity_id):
    """Get excess mortality associated with a modelable entity.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'rate' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws, 'rate',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=15,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df


def get_remission(modelable_entity_id):
    """Get remission rates for a modelable entity.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'rate' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws, 'remission',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=7,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df


def get_duration_in_days(modelable_entity_id):
    """Get duration of disease for a modelable entity in days.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'duration' columns
    """

    remission = get_remission(modelable_entity_id)

    duration = remission.copy()

    duration['duration'] = (1 / duration['remission']) *365

    duration.metadata = {'modelable_entity_id': modelable_entity_id}

    return duration[['year', 'age', 'duration', 'sex']]


def get_continuous(modelable_entity_id):
    """Get the continuous measure from a modelable entity. This measure is used
    for things like the distribution of BMI in a population.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'value' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws,
            'value',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=19,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df


def get_proportion(modelable_entity_id):
    """Get proportion data for a modelable entity. This is used for entities that represent
    outcome splits like severities of heart failure after an infarction.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'proportion' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws,
            'proportion',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=18,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df

@memory.cache
def get_age_bins():
    from db_tools import ezfuncs # This import is here to make the dependency on db_tools optional if the data is available from cache
    return ezfuncs.query('''select age_group_id, age_group_years_start, age_group_years_end, age_group_name from age_group''', conn_def='shared')

def get_prevalence(modelable_entity_id):
    """Get prevalence data for a modelable entity.

    Parameters
    ----------
    modelable_entity_id : int
                          The entity to retrieve

    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'prevalence' columns
    """
    year_start, year_end = gbd_year_range()

    df = functions.load_data_from_cache(
            functions.get_modelable_entity_draws,
            'prevalence',
            location_id=config.getint('simulation_parameters', 'location_id'),
            year_start=year_start,
            year_end=year_end,
            measure=5,
            me_id=modelable_entity_id
        )
    df.metadata = {'modelable_entity_id': modelable_entity_id}
    return df


def get_disease_states(population, states):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start = config.getint('simulation_parameters', 'year_start')

    population = population.reset_index()
    population['simulant_id'] = population['index']
    condition_column = functions.load_data_from_cache(functions.assign_cause_at_beginning_of_simulation, col_name=None, simulants_df=population[['simulant_id', 'age', 'sex']], year_start=year_start, states=states)

    return condition_column

def get_all_cause_mortality_rate():
    """Get the all cause mortality rate.


    Returns
    -------
    pandas.DataFrame
        Table with 'age', 'sex', 'year' and 'rate' columns
    """

    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    return functions.load_data_from_cache(functions.get_all_cause_mortality_rate, \
            'rate', \
            location_id,
            year_start,
            year_end,
            src_column='all_cause_mortality_rate_{draw}')

def get_cause_deleted_mortality_rate(list_of_csmrs):
    # This sort is a because we don't want the cache to invalidate when
    # the csmrs come in in different orders but they aren't hashable by
    # standard python so we can't put them in a set.
    list_of_csmrs = sorted(list_of_csmrs, key=lambda x: joblib.hash(x))
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    return functions.get_cause_deleted_mortality_rate(location_id=location_id, year_start=year_start, year_end=year_end, list_of_csmrs=list_of_csmrs)


def get_relative_risks(risk_id, cause_id, rr_type='morbidity'):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    funct_output = functions.load_data_from_cache(functions.get_relative_risks, col_name='rr', src_column='rr_{draw}', location_id=location_id, year_start=year_start, year_end=year_end, risk_id=risk_id, cause_id=cause_id, rr_type=rr_type)

    # need to reshape the funct output since there can be multiple categories
    output = funct_output.pivot_table(index=['age', 'year', 'sex'], columns=[funct_output.parameter.values], values=['rr'])
    output.columns = output.columns.droplevel()
    output.reset_index(inplace=True)

    output.metadata = {'risk_id': risk_id, 'cause_id': cause_id}
    return output


def get_pafs(risk_id, cause_id, paf_type='morbidity'):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    gbd_round_id = config.getint('simulation_parameters', 'gbd_round_id')
    df = functions.load_data_from_cache(functions.get_pafs, col_name='PAF', location_id=location_id, year_start=year_start, year_end=year_end, risk_id=risk_id, cause_id=cause_id, gbd_round_id=gbd_round_id, paf_type=paf_type)
    df.metadata = {'risk_id': risk_id, 'cause_id': cause_id}
    return df


def get_exposures(risk_id):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    gbd_round_id = config.getint('simulation_parameters', 'gbd_round_id')
    funct_output = functions.load_data_from_cache(functions.get_exposures, col_name='exposure', location_id=location_id, year_start=year_start, year_end=year_end, risk_id=risk_id, gbd_round_id=gbd_round_id)

    # need to reshape the funct output since there can be multiple categories
    output = funct_output.pivot_table(index=['age', 'year', 'sex'], columns=[funct_output.parameter.values], values=['exposure'])
    output.columns = output.columns.droplevel()
    output.reset_index(inplace=True)

    output.metadata = {'risk_id': risk_id}
    return output


def generate_ceam_population(number_of_simulants, initial_age=None, year_start=None):
    location_id = config.getint('simulation_parameters', 'location_id')
    # FIXME: Think that pop_age_start and pop_age_end need to be passed in the same way
    pop_age_start = config.getfloat('simulation_parameters', 'pop_age_start')
    pop_age_end = config.getfloat('simulation_parameters', 'pop_age_end')
    if year_start is None:
        year_start, year_end = gbd_year_range()
    population = functions.load_data_from_cache(functions.generate_ceam_population, col_name=None, location_id=location_id, year_start=year_start, number_of_simulants=number_of_simulants, initial_age=initial_age, pop_age_start=pop_age_start, pop_age_end=pop_age_end)
    population['sex'] = population['sex_id'].map({1:'Male', 2:'Female'}).astype('category')
    population['alive'] = True
    return population


def get_age_specific_fertility_rates():
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    return functions.load_data_from_cache(functions.get_age_specific_fertility_rates, col_name=['mean_value', 'lower_value', 'upper_value'], src_column=['mean_value', 'lower_value', 'upper_value'], location_id=location_id, year_start=year_start, year_end=year_end)


def get_etiology_probability(etiology_name):
    return functions.load_data_from_cache(functions.get_etiology_probability, etiology_name=etiology_name)


def get_etiology_specific_prevalence(eti_risk_id, cause_id, me_id):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    draw_number = config.getint('run_configuration', 'draw_number')
    return functions.load_data_from_cache(functions.get_etiology_specific_prevalence, location_id=location_id,
                                          year_start=year_start, year_end=year_end, eti_risk_id=eti_risk_id,
                                          cause_id=cause_id, me_id=me_id, col_name='prevalence')



def get_etiology_specific_incidence(eti_risk_id, cause_id, me_id):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    draw_number = config.getint('run_configuration', 'draw_number')
    return functions.load_data_from_cache(functions.get_etiology_specific_incidence, location_id=location_id,
                                          year_start=year_start, year_end=year_end, eti_risk_id=eti_risk_id,
                                          cause_id=cause_id, me_id=me_id, col_name='eti_inc')




def get_bmi_distributions():
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    draw = config.getint('run_configuration', 'draw_number')

    return distributions.get_bmi_distributions(location_id, year_start, year_end, draw)

def get_fpg_distributions():
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    draw = config.getint('run_configuration', 'draw_number')

    return distributions.get_fpg_distributions(location_id, year_start, year_end, draw)


def make_gbd_risk_effects(risk_id, causes, effect_function, risk_name):
    return [RiskEffect(
        get_relative_risks(risk_id=risk_id, cause_id=cause_id),
        get_pafs(risk_id=risk_id, cause_id=cause_id),
        cause_name, risk_name,
        effect_function)
        for cause_id, cause_name in causes]

def make_gbd_disease_state(cause, dwell_time=0):
    from ceam_public_health.components.disease import ExcessMortalityState
    if hasattr(cause, 'mortality'):
        if isinstance(cause.mortality, meid):
            csmr = get_cause_specific_mortality(cause.mortality)
        else:
            csmr = cause.mortality
    else:
        csmr = pd.DataFrame()

    if hasattr(cause, 'disability_weight'):
        if isinstance(cause.disability_weight, meid):
            disability_weight = functions.get_disability_weight(cause.disability_weight)
        else:
            disability_weight = cause.disability_weight
    else:
        disability_weight = 0.0

    if hasattr(cause, 'excess_mortality'):
        if isinstance(cause.excess_mortality, meid):
            excess_mortality = get_excess_mortality(cause.excess_mortality)
        else:
            excess_mortality = cause.excess_mortality
    else:
        excess_mortality = 0.0

    if hasattr(cause, 'prevalence'):
        if isinstance(cause.prevalence, meid):
            prevalence = get_prevalence(cause.prevalence)
        else:
            prevalence = cause.prevalence
    else:
        prevalence = 0.0

    return ExcessMortalityState(
            cause.name,
            dwell_time=dwell_time,
            disability_weight=disability_weight,
            excess_mortality_data=excess_mortality,
            prevalence_data=prevalence,
            csmr_data=csmr
        )


def get_diarrhea_severity_split_excess_mortality(excess_mortality_dataframe, severity_split):
    return functions.get_diarrhea_severity_split_excess_mortality(excess_mortality_dataframe, severity_split)

def get_covariate_estimates(covariate_name_short):
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()

    return functions.get_covariate_estimates(location_id, year_start, year_end, covariate_name_short) 

def get_ors_exposure():
    location_id = config.getint('simulation_parameters', 'location_id')
    year_start, year_end = gbd_year_range()
    draw_number = config.getint('run_configuration', 'draw_number')

    return functions.load_data_from_cache(functions.get_ors_exposure, location_id=location_id, year_start=year_start, year_end=year_end, draw_number=draw_number, col_name=None)


def get_severity_splits(parent_meid, child_meid):
    draw_number = config.getint('run_configuration', 'draw_number')

    return functions.get_severity_splits(parent_meid=parent_meid, child_meid=child_meid, draw_number=draw_number)
    
def get_severe_diarrhea_excess_mortality():
    draw_number = config.getint('run_configuration', 'draw_number')
    severe_diarrhea_proportion = get_severity_splits(1181, 2610) 

    return functions.get_severe_diarrhea_excess_mortality(excess_mortality_dataframe=get_excess_mortality(1181), severe_diarrhea_proportion=severe_diarrhea_proportion)


def make_age_group_1_to_4_rates_constant(df):
    """
    Takes a dataframe where incidence or excess mortality rates are
        being set at age group midpoints and reassigns the values
        that are set at the age group 1 - 4 midpoint (3) and assigns
        those values to the age group end and age group start. That
        way our interpolation spline will yield constant values in
        between the age group start and age group end for the 1 to
        4 age group

    Parameters
    ----------
    df: pd.DataFrame()
        df with excess mortality or incidence rates for each age, 
        sex, year, and location
    """
    age_bins = get_age_bins()
    new_rows = pd.DataFrame()
    
    assert 3 in df.age.values, "the input dataframe needs to" + \
                               " simulants that are at the" + \
                               " age group midpoint"
    
    assert [1, 2, 4, 5] not in df.age.values, "the input df" + \
        "should only have simulants that are at the age" + \
        "group midpoint for the 1 to 4 age group"
    

    # get estimates for the age 1-4 age group (select at the age
    #     group midpoint)
    for index, row in df.loc[df.age == 3].iterrows():
        year = (row['year'])
        age_group_max = age_bins.set_index('age_group_name').get_value('1 to 4', 'age_group_years_end')  # the age group max for the 1-4 age group
        age = age_group_max
        if 'rate' in df.columns:
            value_col = 'rate'
            value = (row['rate'])
        elif 'eti_inc' in df.columns:
            value_col = 'eti_inc'
            value = (row['eti_inc'])
        sex = (row['sex'])
        # create a new line in the daataframe
        line = pd.DataFrame({"year": year,
                            "age": 5, value_col: value, "sex": sex},
                            index=[index+1])
        new_rows = new_rows.append(line)
        
    df = pd.concat([df, new_rows]).sort_values(
        by=['year', 'sex', 'age']).reset_index(drop=True)
    age_group_min = age_bins.set_index('age_group_name').get_value('1 to 4', 'age_group_years_start')  # the age group min for the 1-4 age group
    df.loc[df.age == 3, 'age'] = age_group_min
    
    return df

