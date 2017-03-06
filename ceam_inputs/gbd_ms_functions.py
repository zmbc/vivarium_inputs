# ~/ceam/ceam/gbd_data/gbd_ms_functions.py
# coding: utf-8

# TODO: MAKE SURE NEW PYTHON FUNCTIONS ARE USING THE PUBLICATION IDS!!

import os.path
import os
import shutil
from datetime import timedelta

import numpy as np
import pandas as pd

from scipy.stats import beta

from joblib import Memory
from flufl.lock import Lock

from hierarchies import dbtrees

from db_tools import ezfuncs

from ceam import config
from ceam.interpolation import Interpolation
from ceam.framework.randomness import choice

from ceam_inputs.util import stata_wrapper, get_cache_directory
from ceam_inputs.auxiliary_files import open_auxiliary_file, auxiliary_file_path

from ceam_inputs.gbd_ms_auxiliary_functions import create_age_column
from ceam_inputs.gbd_ms_auxiliary_functions import normalize_for_simulation
from ceam_inputs.gbd_ms_auxiliary_functions import get_age_group_midpoint_from_age_group_id
from ceam_inputs.gbd_ms_auxiliary_functions import get_populations
from ceam_inputs.gbd_ms_auxiliary_functions import create_sex_id_column
from ceam_inputs.gbd_ms_auxiliary_functions import get_all_cause_mortality_rate
from ceam_inputs.gbd_ms_auxiliary_functions import get_healthstate_id
from ceam.interpolation import Interpolation
from ceam.framework.randomness import choice
from ceam_inputs.gbd_ms_auxiliary_functions import expand_ages_for_dfs_w_all_age_estimates
# em 9/21: do we want to be converting from rates to probabilities in gbd_ms_functions.py?
# TODO: Yes bring in probabilities. BUT CONFIRM WITH ABIE THAT WE WANT TO BE USING ANNUAL RATES HERE
from ceam_inputs.gbd_ms_auxiliary_functions import expand_ages
from joblib import Memory
import warnings


from ceam.framework.util import from_yearly, rate_to_probability

import logging
_log = logging.getLogger(__name__)

memory = Memory(cachedir=get_cache_directory(), verbose=1)

# from transmogrifier.draw_ops import get_draws


# # Microsim functions
# This notebook contains the functions that will be used to
# re-format GBD data into a format that can be used for the cost-effectiveness
# microsim. Wherever possible, these functions will leverage the existing
# central comp functions (please see this link for more information on the
# central computation functions
# https://hub.ihme.washington.edu/display/GBD2016/Shared+functions

@memory.cache
def get_model_versions():
    """Return a mapping from modelable_entity_id to the version of that entity 
    associated with the GBD publications currently configured.
    """
    publication_ids = [int(pid) for pid in config.get('input_data', 'gbd_publication_ids').split(',')]
    mapping = ezfuncs.query('''
    SELECT modelable_entity_id, model_version_id
    FROM epi.publication_model_version
    JOIN epi.model_version USING (model_version_id)
    JOIN shared.publication USING (publication_id)
    WHERE publication_id in ({})
    '''.format(','.join([str(pid) for pid in publication_ids]))
    , conn_def='epi')

    mapping = dict(mapping[['modelable_entity_id', 'model_version_id']].values)

    return mapping


# 1. get_modelable_entity_draws (gives you incidence, prevalence, csmr, excess mortality, and other metrics at draw level)


def get_modelable_entity_draws(location_id, year_start, year_end, measure,
                               me_id):
    """
    Returns draws for a given measure and modelable entity

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    measure : int, measure
        defines which measure (e.g. prevalence) you want to pull. Use central
        comp's get_ids functions to learn about which measures are available
        and what numbers correspond with each measure

    me_id: int, modelable entity id
        modelable_entity_id takes same me_id values as are used for GBD

    Returns
    -------
    df with year_id, sex_id, age and 1k draws

    Notes
    -----
    Used by -- get_cause_level_prevalence, sum_up_csmrs_for_all_causes_in_microsim, get_post_mi_heart_failure_proportion_draws, get_excess_mortality, get_incidence, get_continuous, get_proportion, get_prevalence

    Assumptions -- None

    Questions -- None

    Unit test in place? -- No. Don't think it's necessary, since this function merely pulls draws from the database and then filters a dataframe so that only one measure is included in the output and that only the years in b/w the simulation year start and year end are included in the df.
    """

    meid_version_map = get_model_versions()
    model_version = meid_version_map[me_id]

    draws = stata_wrapper('get_modelable_entity_draws.do', 'draws_for_location{l}_for_meid{m}.csv'.format(m=me_id, l=location_id), location_id, me_id, model_version)

    # draws = python_wrapper('get_modelable_entity_draws.py', 'draws_for_location{l}_for_meid{m}.csv'.format(m=me_id, l=location_id), location_id, me_id, model_version)

    draws = draws[draws.measure_id == measure]

    draws = draws.query('year_id>={ys} and year_id<={ye}'.format(
                         ys=year_start, ye=year_end))

    draws = get_age_group_midpoint_from_age_group_id(draws)

    keepcol = ['year_id', 'sex_id', 'age']
    keepcol.extend(('draw_{i}'.format(i=i) for i in range(0, 1000)))

    # assert an error to make sure data is dense (i.e. no missing data)
    assert draws.isnull().values.any() == False, "there are nulls in the dataframe that get_modelable_entity_draws just tried to output. check that the cache to make sure the data you're pulling is correct"

    # assert an error if there are duplicate rows
    assert draws.duplicated(['age', 'year_id', 'sex_id']).sum(
    ) == 0, "there are duplicates in the dataframe that get_modelable_entity_draws just tried to output. check the cache to make sure that the data you're pulling is correct"

    return draws[keepcol].sort_values(by=['year_id', 'age', 'sex_id'])


# 2. generate_ceam_population

# TODO: Think initial age is broken
# TODO: Write a test to make sure that getting a representative sample of people in a specific age group works
def generate_ceam_population(location_id, year_start, number_of_simulants, initial_age=None, pop_age_start=config.get('simulation_parameters', 'pop_age_start'), pop_age_end=config.get('simulation_parameters', 'pop_age_end')):
    """
    Returns a population of simulants to be fed into CEAM

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    number of simulants : int, number
        year_end is the year in which you want to end the simulation

    initial_age : int
        If not None simulants will all be set to this age otherwise their
        ages will come from the population distribution

    Returns
    -------
    df with columns simulant_id, age, sex_id, and columns to indicate if
    simulant has different diseases

    Notes
    -----
    Used by -- Creates a population in base_population.py

    Assumptions -- None

    Questions -- None

    Unit test in place? -- Yes

    TODO -- Need to smooth out initial ages (JIRA ticket - CE-213)

    """

    # Use auxilliary get_populations function to bring in the both sex
    # population
    # FIXME: IF/WHEN THE OTHER FUNCTIONS INCLUDE ESTIMATES FOR 5 YEAR AGE GROUPS OVER 80, CHANGE SUM_UP_80_PLUS TO = FALSE!!!!
    pop = get_populations(location_id, year_start, 3, sum_up_80_plus = True)

    if pop_age_start != '':
        pop_age_start = float(pop_age_start)
        pop = pop.query("age >= @pop_age_start").copy()

    if pop_age_end != '':
        pop_age_end = float(pop_age_end)
        pop = pop.query("age <= @pop_age_end").copy()

    total_pop_value = pop.sum()['pop_scaled']

    # get proportion of total population in each age group
    pop['proportion_of_total_pop'] = pop['pop_scaled'] / total_pop_value

    # create a dataframe of 50k simulants
    simulants = pd.DataFrame({'simulant_id': range(0, number_of_simulants)})

    # TODO: If we're setting initial ages, we probably just want a 50/50 distribution of men/women too
    if initial_age is None:
        simulants = create_age_column(simulants, pop, number_of_simulants)
    else:
        simulants['age'] = initial_age

    simulants = create_sex_id_column(simulants, location_id, year_start)

    # TODO: Design and implement test that makes sure CEAM population looks
    # like population file pulled from GBD
    # TODO: Design and implement test that makes sure population has been
    # smoothed out-- JIRA TICKET CE-213

    # assert an error to make sure data is dense (i.e. no missing data)
    assert simulants.isnull().values.any() == False, "there are nulls in the dataframe that generate_ceam_population just tried to output. check the function and its auxiliary functions (get_populations and assign_sex_id)"

    # assert an error if there are duplicate rows
    assert simulants.duplicated(['simulant_id']).sum(
    ) == 0, "there are duplicates in the dataframe that generate_ceam_population just tried to output. check the function and its auxiliary functions (get_populations and assign_sex_id)"

    return simulants

def assign_subregions(index, location_id, year):
    """
    Assigns a location to each simulant. If the location_id
    has sub regions in the hierarchy than the simulants will be
    distributed across them uniformly weighted by each region's population.
    Otherwise all simulants will be assigned location_id.

    Parameters
    ----------
    index : pandas.Index
        the simulants to assign
    location_id : int
        the location in the locations hierarchy to descend from
    year : int
        the year to use for population estimates

    Notes
    -----
    This ignores demographic details. So if there is some region that has a
    age or sex bias in it's population, that will not be captured.
    """
    region_ids = [c.id for c in dbtrees.loctree(None, location_set_id=2).get_node_by_id(location_id).children]

    if not region_ids:
        # The location has no sub regions
        return pd.Series(location_id, index=index)

    # Get the population of each subregion and calculate the ratio of it to the
    # total, which gives us the weights to use when distributing simulants
    populations = np.array([get_populations(region_id, year, 3).pop_scaled.sum() for region_id in region_ids])
    populations = populations / populations.sum()

    return choice('assign_subregions_{}'.format(year), index, region_ids, p=populations)


# 3. assign_cause_at_beginning_of_simulation


def get_cause_level_prevalence(states, year_start):
    """
    Takes all of the sequela in 'states' and adds them up to get a total prevalence for the cause

    Parameters
    ----------
    states : dict
        dict with keys = name of cause, values = modelable entity id of cause

    year_start: int
        year_start is the year in which the simulation will begin

    Returns
    -------
    df with 1k draws where draw values are the prevalence of the cause of interest

    Notes
    -----
    Used by -- assign_cause_at_beginning_of_simulation

    Assumptions -- That the sequela prevalences associated with a cause will add up to the cause level prevalence

    Questions -- Would it be better to just pull cause level prevalence? I'm a bit worried that the sequela prevalences won't add up

    Unit test in place? -- Yes

    """
    prevalence_df = pd.DataFrame()

    for key in states.keys():

        assert set(states[key].columns) == set(['year', 'age', 'prevalence', 'sex']), "the keys in the dict passed to get_cause_level_prevalence need to be dataframes with columns year, age, prevalence, and sex"
        # get prevalence for the start year only
        states[key] = states[key].query("year == {}".format(year_start))

        # keep only the columns we need (demographic identifiers and one draw)
        states[key] = states[key][['year', 'age', 'prevalence', 'sex']]
        prevalence_df = prevalence_df.append(states[key])

    cause_level_prevalence = prevalence_df.groupby(
        ['year', 'sex', 'age'], as_index=False)[['prevalence']].sum()

    return cause_level_prevalence, states


def determine_if_sim_has_cause(simulants_df, cause_level_prevalence):
    """
    returns a dataframe with new column 'condition_envelope' that will indicate whether the simulant has the cause or is healthy (healthy is where condition_envelope = NaN at this point)

    Parameters
    ----------
    simulants_df: df
        dataframe of simulants that is made by generate_ceam_population

    cause_level_prevalence: df
        dataframe of 1k prevalence draws

    Returns
    -------
    df with indication of whether or not simulant is healthy

    Notes
    -----
    Used by -- assign_cause_at_beginning_of_simulation

    Assumptions -- None

    Questions -- I sort the prevalence and simulants dataframes by simulant_id to make sure that the prevalence is being assigned correctly to each demographic group. Is there a better way to make sure that we're applying the correct prevalence rate to each simulant?

    Unit test in place? -- Yes
    """

    # TODO: Need to include Interpolation in this function for cause_level_prevalence. There are more age values for simulants df (older ages) than there are for cause_level_prevalence, hence why an interpolation function is needed. 

    #TODO: this is weird and not general but I don't think we should be doing this lookup here anyway
    assert len(set(cause_level_prevalence.year)) == 1
    cause_level_prevalence = cause_level_prevalence.copy()
    del cause_level_prevalence['year']
    #merged = pd.merge(simulants_df, cause_level_prevalence, on=['age', 'sex'])
    probability_of_disease = Interpolation(cause_level_prevalence, ['sex'], ['age'])(simulants_df[['age', 'sex']])
  
    probability_of_NOT_having_disease = 1 - probability_of_disease
    weights = np.array([probability_of_NOT_having_disease, probability_of_disease]).T

    results = simulants_df.copy()

    results = results.set_index('simulant_id') 
 
    # Need to sort results so that the simulants are in the same order as the weights
    results['condition_envelope'] = choice('determine_if_sim_has_cause', results.index, [False, True], weights)

    return results


def get_sequela_proportions(cause_level_prevalence, states):
    """
    Returns a dictionary with keys that are modelable entity ids and values are dataframes with proportion data

    Parameters
    ----------
    cause_level_prevalence: df
        dataframe of 1k prevalence draws

    states : dict
        dict with keys = name of cause, values = dataframe of prevalence draws

    Returns
    -------
    A dictionary of dataframes where each dataframe contains proportion of cause prevalence made up by a specific sequela

    Notes
    -----
    Used -- That the prevalence of a sequela can be divided by the prevalence of the cause associated with that sequela to get the proportional prevalence.

    Questions -- None

    Unit test in place? -- Yes
    """

    sequela_proportions = {}

    for key in states.keys():
        sequela_proportions[key] = \
            pd.merge(states[key], cause_level_prevalence, on=[
                'age', 'sex', 'year'], suffixes=('_single', '_total'))
        single = sequela_proportions[key][
            'prevalence_single']
        total = sequela_proportions[key][
            'prevalence_total']
        sequela_proportions[key]['scaled_prevalence'] = np.divide(single, total)

    return sequela_proportions


def determine_which_seq_diseased_sim_has(sequela_proportions, new_sim_file):
    """
    Parameters
    ----------
    sequela_proportions: dict
        a dictionary of dataframes where each dataframe contains proportion of cause prevalence made up by a specific sequela

    new_sim_file: df
        dataframe of simulants

    Returns
    -------
    dataframe of simulants with new column condition_state that indicates if simulant which sequela simulant has or indicates that they are healthy (i.e. they do not have the disease)

    Notes
    -----
    Used by -- assign_cause_at_beginning_of_simulation

    Assumptions -- None

    Questions -- None

    Unit test in place? -- Yes
    """
    sequela_proportions = [(key, Interpolation(data[['sex', 'age', 'scaled_prevalence']], ['sex'], ['age'])) for key, data in sequela_proportions.items()]
    sub_pop = new_sim_file.query('condition_envelope == 1')
    list_of_keys, list_of_weights = zip(*[(key,data(sub_pop)) for key, data in sequela_proportions])

    results = choice('determine_which_seq_diseased_sim_has', sub_pop.index, list_of_keys, np.array(list_of_weights).T)
    new_sim_file.loc[sub_pop.index, 'condition_state'] = results

    return new_sim_file


def assign_cause_at_beginning_of_simulation(simulants_df, year_start, states):
    """
    Function that assigns chronic ihd status to starting population of
    simulants

    Parameters
    ----------
    simulants_df : dataframe
        dataframe of simulants that is made by generate_ceam_population

    year_start : int, year
        year_start is the year in which you want to start the simulation

    states : dict
        dict with keys = name of cause, values = modelable entity id of cause

    Returns
    -------
    Creates a new column for a df of simulants with a column called chronic_ihd
        chronic_ihd takes values 0 or 1
            1 indicates that the simulant has chronic ihd
            0 indicates that the simulant does not have chronic ihd
    
    Notes
    -----
    Used by -- get_disease_states

    Assumptions -- None

    Questions -- None

    Unit test in place? -- I wrote code to produce graphs to make sure we're assigning prevalence correctly at the beginning of the simulation. I need to figure out how to allow randomness back into the assign_cause_at_beginning_of_simulation so that I can get a distribution of prevalences. @Alecwd I could use your help on this.

    TODO: Automate and allow randomness in the graph production code
    """
    
    cause_level_prevalence, prevalence_draws_dictionary = get_cause_level_prevalence(states, year_start) 

    # TODO: Should we be using groupby for these loops to ensure that we're
    # not looping over an age/sex combo that does not exist
    post_cause_assignment_population = determine_if_sim_has_cause(simulants_df, cause_level_prevalence)    

    sequela_proportions = get_sequela_proportions(cause_level_prevalence, states)

    post_sequela_assignmnet_population = determine_which_seq_diseased_sim_has(sequela_proportions, post_cause_assignment_population)

    post_sequela_assignmnet_population.condition_state = post_sequela_assignmnet_population.condition_state.fillna('healthy')

    # assert an error to make sure data is dense (i.e. no missing data)
    assert  post_sequela_assignmnet_population.isnull().values.any() == False, "there are nulls in the dataframe that assign_cause_at_beginning_of_simulation just tried to output. check that you've assigned the correct me_ids"

    # assert an error if there are duplicate rows
    # assert  post_sequela_assignmnet_population.duplicated(['simulant_id']).sum() == 0, "there are duplicates in the dataframe that assign_cause_at_beginning_of_simulation just tried to output. check that you've assigned the correct me_ids"

    return post_sequela_assignmnet_population


# 4. get_cause_deleted_mortality_rate


def sum_up_csmrs_for_all_causes_in_microsim(list_of_csmrs):
    '''
    returns dataframe with columns for age, sex, year, and 1k draws
    the draws contain the sum of all the csmrs all of the causes in
    the current simulation.

    Parameters
    ----------
    list_of_csmrs: list
        list of all of the CSMRs in current simulation

    Returns
    ----------
    df with columns year_id, sex_id, age, and draw_0 - draw_999

    Notes
    -----
    Used by -- get_cause_deleted_mortality_rate

    Assumptions -- That we can add together the csmrs for every cause in the microsim and then subtract from the all-cause mortality rate to get the cause-deleted mortality rate.

    Questions -- None

    Unit test in place? -- Yes
    '''

    df = pd.DataFrame()

    for csmr_draws in list_of_csmrs:
        df = df.append(csmr_draws)

    df = df.groupby(
        ['age', 'sex', 'year'], as_index=False).sum()

    return df


@memory.cache
def get_cause_deleted_mortality_rate(location_id, year_start, year_end, list_of_csmrs):
    '''Returns the cause-delted mortality rate for a given time period and location

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    Returns
    -------
    df with columns age, year_id, sex_id, and 1k draws of cause deleted
        mortality rate

    Notes
    -----
    Used by -- Used in base_population.py

    Assumptions -- That we can subtract the csmrs for the causes we care about to get the cause-deleted mortality rate

    Questions -- None

    Unit test in place? -- Yes
    '''

    #TODO: this doesn't belong here. Should be passed in somehow
    draw = config.getint('run_configuration', 'draw_number')

    all_cause_mr = normalize_for_simulation(get_all_cause_mortality_rate(
        location_id, year_start, year_end))
    all_cause_mr = all_cause_mr[['age', 'sex', 'year', 'all_cause_mortality_rate_{}'.format(draw)]]
    all_cause_mr.columns = ['age', 'sex', 'year', 'all_cause_mortality_rate']


    if list_of_csmrs:
        all_me_id_draws = sum_up_csmrs_for_all_causes_in_microsim(list_of_csmrs).set_index(['age', 'sex', 'year'])

        cause_del_mr = all_cause_mr.set_index(['age', 'sex', 'year']) 


        # get cause-deleted mortality rate by subtracting out all of the csmrs from
        # all-cause mortality rate
        deleted = (cause_del_mr.all_cause_mortality_rate - all_me_id_draws.rate).reset_index()
        deleted.columns = ['age', 'sex', 'year', 'cause_deleted_mortality_rate']

        # assert an error to make sure data is dense (i.e. no missing data)
        assert deleted.isnull().values.any() == False, "there are nulls in the dataframe that get_cause_deleted_mortality_rate just tried to output. check the function as well as get_all_cause_mortality_rate"

        # assert an error if there are duplicate rows
        assert deleted.duplicated(['age', 'year', 'sex']).sum(
        ) == 0, "there are duplicates in the dataframe that get_cause_deleted_mortality_rate just tried to output. check the function as well as get_all_cause_mortality_rate"

        # assert that non of the cause-deleted mortality rate values are less than or equal to 0
        assert np.all(deleted.cause_deleted_mortality_rate > 0), "something went wrong with the get_cause_deleted_mortality_rate calculation. cause-deleted mortality can't be <= 0"

        return deleted
    else:
        return all_cause_mr


# 5. get_post_mi_heart_failure_proportion_draws


def get_post_mi_heart_failure_proportion_draws(location_id, year_start, year_end):
    # TODO: NEED TO WRITE TESTS TO MAKE SURE THAT POST_MI TRANSITIONS SCALE TO 1
    """
    Returns post-mi proportion draws for hf due to ihd

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int
        year_start is the year in which you want to start the simulation

    year_end : int
        year_end is the year in which you want to end the 

    Returns
    -------
    df with year_id, sex_id, age and 1k draws

    Notes
    -----
    Used by -- Used in disease_models.py to determine how many people get heart failure following an mi.

    Assumptions -- That the proportional prevalence is a good enough estimation of the proportional incidence.

    Questions -- More of a general python question -- should I be using np.multiply for multiplication? Maybe it has to do with python's floating point issues, but I was getting different results when using A*B instead of np.multiply(A,B).

    Unit test in place? --  Yes
    """

    # read in heart failure envelope. specify measure of interest
    hf_envelope = get_modelable_entity_draws(
        location_id, year_start, year_end, 6, 2412)

    # read in proportion of the cause of heart failure of interest
    proportion_draws = get_modelable_entity_draws(
        location_id, year_start, year_end, 18, 2414)

    # merge and then multiply envelope draws by proportion draws
    cause_of_hf = pd.merge(hf_envelope, proportion_draws, on=[
                           'age', 'year_id', 'sex_id'], suffixes=('_env', '_prop'))

    envelope = cause_of_hf[['draw_{}_env'.format(i) for i in range(0,1000)]].values

    proportion = cause_of_hf[['draw_{}_prop'.format(i) for i in range(0,1000)]].values

    cause_of_hf.set_index(['year_id', 'sex_id', 'age'], inplace=True)

    # TODO: Manual calculation of the multiplication below gave a little bit different values. Should I be using np.multiply or somethig else to make sure python is handling these floats correctly?
    # TODO: Ensure rate_to_probability is calculating annual rates
    output_df = pd.DataFrame(rate_to_probability(np.multiply(envelope, proportion)), columns=['draw_{}'.format(i) for i in range(1000)], index=cause_of_hf.index)  

    output_df = output_df.reset_index()

    keepcol = ['year_id', 'sex_id', 'age']
    keepcol.extend(('draw_{i}'.format(i=i) for i in range(0, 1000)))

    # assert an error to make sure data is dense (i.e. no missing data)
    assert output_df.isnull().values.any() == False, "there are nulls in the dataframe that get_post_mi_heart_failure_proportion_draws just tried to output. check that the cache to make sure the data you're pulling is correct"

    # assert an error if there are duplicate rows
    assert output_df.duplicated(['age', 'year_id', 'sex_id']).sum(
    ) == 0, "there are duplicates in the dataframe that get_post_mi_heart_failure_proportion_draws just tried to output. check the cache to make sure that the data you're pulling is correct"

    # assert that none of the incidence rate values are greater than 1 (basically ensuring that the numerator and demoniator weren't flipped)
    draw_number = config.getint('run_configuration', 'draw_number')
    assert output_df['draw_{}'.format(draw_number)].all() <= 1, "something went wrong with the get_post_mi_heart_failure_proportion_draws calculation. incidence rate can't be GT 1. Check to see if the numerator/denominator were flipped"

    return output_df[keepcol]


# 6. get_relative_risks

def get_relative_risks(location_id, year_start, year_end, risk_id, cause_id, rr_type='morbidity'):
    """
    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    risk_id: int, risk id
        risk_id takes same risk_id values as are used for GBD

    cause_id: int, cause id
        cause_id takes same cause_id values as are used for GBD

    rr_type: str
        can specify morbidity if you want RRs for incidence or mortality if you want RRs for mortality

    Returns
    -------
    df with columns year_id, sex_id, age, 1k relative risk draws

    Notes
    -----
    Used by -- Used to pull relative risks which are then multiplied by incidence rates in continuous_exposure_effect and categorical_exposure_effect

    Assumptions -- Some risks in GBD (e.g. Zinc deficiency and high sbp) don't have estimates for all ages. I have set up the code so that each age group for which we don't have GBD estimates has an RR of 1 (i.e. no elevated risk).

    Questions -- Should we set the RR to 1 for age groups for which we do not have rr estimates?

    Unit test in place? -- Yes
    """

    rr = stata_wrapper('get_relative_risks.do', 'rel_risk_of_risk{r}_in_location{l}.csv'.format(r=risk_id,l=location_id), location_id, risk_id, config.getint('simulation_parameters', 'gbd_round_id'))

    # FIXME: Will want this pull to be linked to a publication id.
    # rr = get_draws(gbd_id_field='rei_id', gbd_id=risk_id, location_id=location_id, sex_ids=[1,2], status='best', source='risk', draw_type='rr', gbd_round_id=config.getint('simulation_parameters', 'gbd_round_id'))

    # Not all rrs are updated every round. For those that aren't updated every round, we can pull the rrs from a previous gbd_round
    # if rr.values == "error":
    #    rr = get_draws(gbd_id_field='rei_id', gbd_id=risk_id, location_id=location_id, sex_ids=[1,2], status='best', source='risk', draw_type='rr', gbd_round_id=config.getint('simulation_parameters', 'gbd_round_id') + 1)

    if rr_type == 'morbidity':
        rr = rr.query("morbidity == 1")
    elif rr_type == 'mortality':
        rr = rr.query("mortality == 1")
    else:
        raise ValueError('rr_type accepts one of two values, morbidity or mortality. you typed "{}" which is incorrect'.format(rr_type))

    # FIXME: Could save some memory by pulling only the years we need initially
    rr = rr.query('year_id>={ys} and year_id<={ye}'.format(
                         ys=year_start, ye=year_end)).copy()

    rr = rr.query('cause_id == {}'.format(cause_id))

    if rr.empty:
        raise ValueError("No data for risk_id {} on cause_id {} for type {}".format(risk_id, cause_id, rr_type))

    rr = get_age_group_midpoint_from_age_group_id(rr)

    rr = expand_ages(rr)

    # need to calculate relative risks for current implementation of CEAM. Some risks (e.g Zinc deficiency and high sbp) don't have estimates for all ages (e.g. no estimates for people over age 5 for zinc).
    # TODO: Do we want to use an RR of 1 in the exposed groups? That's a pretty big assumption. It assumes that there is no risk of the risk factor on the exposure for those ages. If we don't have the data for the younger age groups, another alternative could be to backcast the relative risk of the youngest age group for which we do have data.
    rr[['rr_{}'.format(i) for i in range(0,1000)]] = rr[['rr_{}'.format(i) for i in range(0,1000)]].fillna(value=1)

    keepcol = ['year_id', 'sex_id', 'age', 'parameter']
    keepcol.extend(('rr_{i}'.format(i=i) for i in range(0, 1000)))

    # assert an error to make sure data is dense (i.e. no missing data)
    assert rr[keepcol].isnull().values.any() == False, "there are nulls in the dataframe that get_relative_risks just tried to output. check that the cache to make sure the data you're pulling is correct"

    # assert that none of the rr values are less than 1
    draw_number = config.getint('run_configuration', 'draw_number')
    assert rr['rr_{}'.format(draw_number)].all() >= 1, "something went wrong with get_relative_risks. RR cannot be LT 1. Check the data that you are pulling in and the function. Sometimes, the database does not have\
RR estimates for every age, so check to see that the function is correctly assigning relative risks to the other ages"

    return rr[keepcol]


# 7. get_pafs


def get_pafs(location_id, year_start, year_end, risk_id, cause_id, paf_type='morbidity'):
    """
    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    risk_id: int, risk id
        risk_id takes same risk_id values as are used for GBD

    cause_id: int, cause id
        cause_id takes same cause_id values as are used for GBD

    paf_type: str
        specify whether you want morbidity (YLDs) or mortality (Deaths) PAFs

    Returns
    -------
        df with columns year_id, sex_id, age, val, upper, and lower

    Notes
    -----
    Used by -- anytime a user adds a risk to a simulation

    Assumptions -- We should use PAFs for YLDs, since we use PAFs to affect incidence in CEAM. Some risks in GBD (e.g. Zinc deficiency and high sbp) don't have estimates for all ages. I have set up the code so that each age group for which we don't have GBD estimates has a PAF of 0

    Questions -- Should we set the PAF to 0 for age groups for which we do not have rr estimates? Need to submit an epihelp ticket to determine whether we should use get_draws or transmogrifier.risk.risk_draws.

    Unit test in place? -- Yes
    """
    if paf_type == 'morbidity':
        measure_id = 3
    elif paf_type == 'mortality':
        measure_id = 1
    else:
        raise ValueError('paf_type accepts one of two values, morbidity or mortality. you typed "{}" which is incorrect'.format(rr_type))

    pafs = stata_wrapper('get_pafs.do', 'PAFs_for_{c}_in_{l}.csv'.format(c=cause_id, l=location_id), location_id, cause_id, config.getint('simulation_parameters', 'gbd_round_id'), measure_id)

    keepcol = ['year_id', 'sex_id', 'age']
    keepcol.extend(('draw_{i}'.format(i=i) for i in range(0, 1000)))

    # only want one risk at a time and only metric id 2 (percentages or pafs)
    pafs = pafs.query("rei_id == @risk_id and metric_id == 2 and year_id >= @year_start and year_id <= @year_end")

     # FIXME: Why continue if pafs is empty??
    # if pafs.empty:
    #    continue

    pafs = get_age_group_midpoint_from_age_group_id(pafs)

    pafs = expand_ages(pafs)

    # TODO: Need to set age, year, sex index here again to make sure that we assign the correct value to points outside of the range
    # need to back calculate PAFS to earlier ages for risks that don't
    # start until a certain age
    pafs[['draw_{}'.format(i) for i in range(0,1000)]] = pafs[['draw_{}'.format(i) for i in range(0,1000)]].fillna(value=0)    

    # assert an error to make sure data is dense (i.e. no missing data)
    assert pafs[keepcol].isnull().values.any() == False, "there are nulls in the dataframe that get_pafs just tried to output. check that the cache to make sure the data you're pulling is correct"

    # assert an error if there are duplicate rows
    assert pafs.duplicated(['age', 'year_id', 'sex_id']).sum(
    ) == 0, "there are duplicates in the dataframe that get_pafs just tried to output. check the cache to make sure that the data you're pulling is correct"

    # assert that none of the paf values are greater than 1
    draw_number = config.getint('run_configuration', 'draw_number')

    # FIXME: I don't think this test is actually working correctly
    assert pafs['draw_{}'.format(draw_number)].all() <= 1, "something went wrong with get_pafs. pafs cannot be GT 1. Check the data that you are pulling in and the function. Sometimes, a risk does not have paf estimates for every age, so check to see that the function is correctly assigning relative risks to the other ages"

    return pafs[keepcol]


# 8. get_exposures


def get_exposures(location_id, year_start, year_end, risk_id):
    """
    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    risk_id: int, risk id
        risk_id takes same risk_id values as are used for GBD

    Returns
    -------
    df with columns year_id, sex_id, age and 1k exposure draws

    Notes
    -----
    Used by -- anytime a user adds a risk to a simulation
 
    Assumptions -- Some risks in GBD (e.g. Zinc deficiency and high sbp) don't have estimates for all ages. I have set up the code so that each age group for which we don't have GBD estimates has an exposure of 0

    Questions -- Should we set the exposure to 0 for age groups for which we do not have rr estimates? Need to submit an epihelp ticket to determine whether we should use get_draws or transmogrifier.risk.risk_draws.

    Unit test in place? -- No. Just pulls exposures from the database and then does some light processing (e.g. gets age group midpoints)
    """

    # exposure = get_draws(gbd_id_field='rei_id', gbd_id=108, location_id=180, source='risk', draw_type='exposure', gbd_round_id=config.getint('simulation_parameters', 'gbd_round_id'))

    exposure = stata_wrapper('get_exposures.do', 'Exposure_of_risk{r}_in_location{l}.csv'.format(r=risk_id, l=location_id), location_id, risk_id, config.getint('simulation_parameters', 'gbd_round_id'))

    # Not all exposures are updated every round. For those that aren't updated every round, we can pull the rrs from a previous gbd_round
    # if exposure.values == "error":
    #    exposure == get_draws(gbd_id_field='rei_id', gbd_id=108, location_id=180, source='risk', draw_type='exposure', gbd_round_id=config.getint('simulation_parameters', 'gbd_round_id') + 1)

    exposure = exposure.query("year_id >= @year_start and year_id <= @year_end")

    exposure = get_age_group_midpoint_from_age_group_id(exposure)

    # TODO: Need to set age, year, sex index here again to make sure that we assign the correct value to points outside of the range
    # TODO: Confirm that we want to be using cat1 here. Cat1 seems really high for risk_id=238 (handwashing without soap) for Kenya
    # TODO: Do we want to set the exposure to 0 for the younger ages for which we don't have data? It's an exceptionally strong assumption. We could use the exposure for the youngest age for which we do have data, or do something else, if we wanted to. --EM 12/12
    exposure = expand_ages(exposure)

    exposure[['draw_{}'.format(i) for i in range(0,1000)]] = exposure[['draw_{}'.format(i) for i in range(0,1000)]].fillna(value=0)

    keepcol = ['year_id', 'sex_id', 'age', 'parameter'] + ['draw_{i}'.format(i=i) for i in range(0, 1000)]

    # assert an error to make sure data is dense (i.e. no missing data)
    assert exposure[keepcol].isnull().values.any() == False, "there are nulls in the dataframe that get_exposures just tried to output. check that the cache to make sure the data you're pulling is correct"

    # assert an error if there are duplicate rows
    assert exposure.duplicated(['age', 'year_id', 'sex_id', 'parameter']).sum(
    ) == 0, "there are duplicates in the dataframe that get_exposures just tried to output. check the cache to make sure that the data you're pulling is correct"

    return exposure[keepcol]


# ### 9. TMREDs
# # TODO: Confirm that TMREDs are being calculated correct

# tmred_df = pd.read_excel('/snfs1/Project/Cost_Effectiveness/dev/data/gbd/risk_data/risk_variables.xlsx')

# # theoretical minimum risk exposure levels
# tmred_df = pd.read_excel('/snfs1/Project/Cost_Effectiveness/dev/data/gbd/risk_data/risk_variables.xlsx')

# # dictionary to hold TMREDs
# risk_tmred = {}

# # save max and min TMREDs to dictionary (distributions are all uniform)
# for risk in ['metab_sbp','smoking']:
#     risk_tmred[risk] = tmred_df.loc[tmred_df.risk==risk,['tmred_dist','tmred_para1','tmred_para2','rr_scalar','inv_exp']]

# risk_tmred['metab_sbp']

# risk_tmrel = {}

# # draw from uniform distribution for each risk factor
# for risk in ['metab_sbp']:
#     risk_tmrel[risk] = np.random.uniform(low=risk_tmred[risk]['tmred_para1'],high=risk_tmred[risk]['tmred_para2'],size=1)[0]
#     risk_tmrel[risk] = ((risk_tmred[risk]['tmred_para1'].values.astype(float)
#                          + risk_tmred[risk]['tmred_para2'].values.astype(float))/2)[0]

# risk_tmrel['metab_sbp']


# 10. load_data_from_cache


memory = Memory(cachedir=get_cache_directory(), verbose=1)


@memory.cache
def _inner_cached_call(funct, *args, **kwargs):
    return funct(*args, **kwargs)


def load_data_from_cache(funct, col_name, *args, src_column=None, **kwargs):
    """
    load_data_from_cache is a functor that will
    check a cache to see if data exists in that cache.
    If the data does not exist in the cache,
    load_data_from_cache will run a function (funct)
    with arguments (args,kwargs)

    Parameters
    ----------
    funct : str
        function to run if data is not already loaded into the cache
        (e.g. get_cause_deleted_mortality_rate)

    col_name : str
        rename the draw column to whichever column_name you want

    args,kwargs : int
        input the arguments required by the function (funct)
        (e.g. location_id, year_start, year_end)

    Returns
    -------
    df with input data for CEAM
    """

    # This causes the files that the cache writes to be both readable and
    # writeable by other users
    old_umask = os.umask(0)

    function_output = _inner_cached_call(funct, *args, **kwargs)

    os.umask(old_umask)

    draw = config.getint('run_configuration', 'draw_number')

    if col_name:
        if src_column is not None:
            if isinstance(src_column, str):
                column_map = {src_column.format(draw=draw): col_name}
            else:
                column_map = {src.format(draw=draw):dest for src, dest in zip(src_column, col_name)}
        else:
            column_map = {'draw_{draw}'.format(draw=draw): col_name}

        # if 'parameter' is in columns, then keep it, else do not keep it (need parameter for the relative risk estimations)
        if 'parameter' in function_output.columns:
            keepcol = ['year_id', 'age', 'sex_id', 'parameter'] + list(column_map.keys())
        else:
            keepcol = ['year_id', 'age', 'sex_id'] + list(column_map.keys())

        function_output = function_output[keepcol]
        function_output = function_output.rename(columns=column_map)

        return normalize_for_simulation(function_output)
    return function_output


# 11. get_severity_splits


# 12. get_sbp_mean_sd

def get_sbp_mean_sd(location_id, year_start, year_end):
    ''' Returns a dataframe of mean and sd of sbp in LOG SPACE

    Parameters
    ----------
    location_id : int

    year_start : int

    year_end : int

    Returns
    -------
    df with mean and sd values in LOG space

    Notes
    -----
    Assumptions -- That people under age 25 have the TMRED SBP

    Questions -- We have estimates starting in the age 25-29 age group. Should we be using the midpoint or age 25 as the starting point?

    Unit test in place? -- Yes

    TODO: Might want to change the TMRED. Need to catch up with Stan regarding calculating TMREDs + write a function that will allow us to calculate TMREDs for a given risk.
    '''

    output_df = pd.DataFrame()
    sbp_dir = os.path.join(get_cache_directory(), 'sbp')

    draws = pd.DataFrame()

    for sex_id in [1, 2]:
        for year_id in np.arange(year_start, year_end + 1, 5):
            path = auxiliary_file_path('Systolic Blood Pressure Distributions',
                                     location_id=location_id,
                                     year_id=year_id,
                                     sex_id=sex_id)
            one_year_file = pd.read_stata(path)
            one_year_file['year_id'] = year_id
            one_year_file['sex_id'] = sex_id
            draws = draws.append(one_year_file)

        #TODO: Need to rethink setting ages for this function. Since sbp estimates start for the age 25-29 group, it should start at age 25, not 27.5.
    draws = get_age_group_midpoint_from_age_group_id(draws)

    draws = expand_ages(draws)

    # set index
    draws.set_index(['year_id', 'sex_id', 'age'], inplace=True)
 
    # set nulls to be 1 to keep from messing up the math below. the nulls are the younger age groups (simulants less than 27.5 years old) and they'll get an sbp of 112 and an sd of .001 because we want them to be at the TMRED
    draws[['exp_mean_{}'.format(i) for i in range(0,1000)]] = draws[['exp_mean_{}'.format(i) for i in range(0,1000)]].fillna(value=1) 
    draws[['exp_sd_{}'.format(i) for i in range(0,1000)]] = draws[['exp_sd_{}'.format(i) for i in range(0,1000)]].fillna(value=1)
    
    # FIXME: This process does produce a df that has null values for simulants under 27.5 years old for the exp_mean and exp_sd cols. Dont think this will affect anything but may be worth fixing        
    exp_mean = draws[['exp_mean_{}'.format(i) for i in range(0,1000)]].values
    exp_sd = draws[['exp_sd_{}'.format(i) for i in range(0,1000)]].values

    mean_df = pd.DataFrame(np.log(exp_mean), columns=['log_mean_{}'.format(i) for i in range(1000)], index=draws.index)
    sd_df = pd.DataFrame(np.divide(exp_sd, exp_mean), columns=['log_sd_{}'.format(i) for i in range(1000)], index=draws.index)
   
    output_df = mean_df.join(sd_df)

    for i in range(0,1000):
        output_df.loc[pd.IndexSlice[output_df.index.levels[2] < 27.5], 'log_mean_{}'.format(i)] = np.log(112)
        output_df.loc[pd.IndexSlice[output_df.index.levels[2] < 27.5], 'log_sd_{}'.format(i)] = .001

    output_df = output_df.reset_index()

    # assert an error if there are duplicate rows
    assert output_df.duplicated(['age', 'year_id', 'sex_id']).sum(
    ) == 0, "there are duplicates in the dataframe that get_sbp_mean_sd just tried to output. make sure what youre pulling from /share/epi/risk/paf/metab_sbp_interm/ is correct"

    keepcol = ['year_id', 'sex_id', 'age']
    keepcol.extend(('log_mean_{i}'.format(i=i) for i in range(0, 1000)))
    keepcol.extend(('log_sd_{i}'.format(i=i) for i in range(0, 1000)))

    return output_df[keepcol].sort_values(by=['year_id', 'age', 'sex_id'])

# 13 get_angina_proportions


def get_angina_proportions():
    '''Format the angina proportions so that we can use them in CEAM.
    This is messy. The proportions were produced by Catherine Johnson.
    The proportion differs by age, but not by sex, location, or time.
    This will likely change post GBD-2016.

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int
        year_start is the year in which you want to start the simulation


    Returns
    -------
    df with year_id, sex_id, age and 1k draws

    Notes
    -----
    Assumptions -- The file does not have estimates for people under age 20. I've set the proportions for people under age 20 to be the same as the proportion for people that are 20 years old. This shouldn't have much of an impact on anything, since we don't expect for people under age 20 to have heart attacks.

    Questions -- Is it valid to assign the angina proportion for 20 year olds to be the angina proportions for people under the age of 20? Who should we talk to about having these proportions stored in a better place (e.g. the database)? Who should we talk to about ensuring that this file doesn't move? How can we ensure that the file is updated if need be?

    Unit test in place? -- Yes
    '''

    # TODO: Need to figure out a way to check to see if this file is ever updated. Would be nice if we could think of a better way to make sure we're using the most up to date data.
    with open_auxiliary_file('Angina Proportions') as f:
        ang = pd.read_csv(f)

    # not sure why income is included in this file. estimates are the same for high and low income countries. we'll filter
    # on high income to get rid of the superfluous rows.
    ang = ang.query("income == 'high'")    

    ang = get_age_group_midpoint_from_age_group_id(ang)

    # we don't have estimates under age 22.5, so I'm filling all ages under
    # 20 with the same proportion that we have for 20 year olds
    # TODO: Should check this assumption w/ Abie
    # creating a copy of ang to use pd.get_value
    ang_copy = ang.set_index('age').copy()  
 
    # values are same for each sex, so we can grab the value 
    # for the lowest age from either sex to apply to the younger age 
    # groups for which we do not have data
    value_at_youngest_age_for_which_we_have_data = ang_copy.query("sex_id == 1").get_value(22.5, 'angina_prop')

    total_ang = pd.DataFrame()

    # the data is not year specific. we manually add year_id values here
    # TODO: Probably a more sophisticated way to do this
    for year in [1990, 1995, 2000, 2005, 2010, 2013, 2015]: 
        one_year = ang.copy()
        one_year['year_id'] = year
        total_ang = total_ang.append(one_year)    

    total_ang = total_ang[['year_id', 'sex_id', 'age', 'angina_prop']]

    total_ang = total_ang.apply(lambda x: x.fillna(value_at_youngest_age_for_which_we_have_data), axis=0)

    return total_ang


# 14. get_disability_weight


def get_disability_weight(dis_weight_modelable_entity_id=None, healthstate_id=None):
    """Returns a dataframe with disability weight draws for a given healthstate id

    Parameters
    ----------
    dis_weight_modelable_entity_id : int

    Returns
    -------
    df with disability weight draws

    Notes
    -----
    Assumptions -- None

    Questions -- How can IHME create a more systematic way for access this data? The current way (looking in one csv prepared by central comp and then checking another if the draws are not in the first csv) is pretty disorganized. Since many disability weights are going to be updated in 2016, these files may move. I would propose that we ask central comp to store the disability weights in the database.

    Unit test in place? -- Yes
    """

    if healthstate_id is None:
        healthstate_id = get_healthstate_id(dis_weight_modelable_entity_id)

    with open_auxiliary_file('Disability Weights') as f:
        dws_look_here_first = pd.read_csv(f)

    with open_auxiliary_file('Combined Disability Weights') as f:
        dws_look_here_second = pd.read_csv(f)

    if healthstate_id in dws_look_here_first.healthstate_id.tolist():
        df = dws_look_here_first.query("healthstate_id == @healthstate_id").copy()
        df['modelable_entity_id'] = dis_weight_modelable_entity_id

    elif healthstate_id in dws_look_here_second.healthstate_id.tolist():
        df = dws_look_here_second.query("healthstate_id == @healthstate_id").copy()
        df['modelable_entity_id'] = dis_weight_modelable_entity_id

    # TODO: Need to confirm with someone on central comp that all 'asymptomatic' sequala get this healthstate_id
    elif healthstate_id == 799:
        df = pd.DataFrame({'healthstate_id':[799], 'healthstate': ['asymptomatic'], 'modelable_entity_id':[dis_weight_modelable_entity_id], 'draw{}'.format(config.getint('run_configuration', 'draw_number')) : [0]})
    else:
        raise ValueError("""the modelable entity id {m} has a healthstate_id of {h}. it looks like there 
        are no draws for this healthstate_id in the csvs that get_healthstate_id_draws checked.
        look in this folder for the draws for healthstate_id{h}: /home/j/WORK/04_epi/03_outputs/01_code/02_dw/03_custom.
        if you can't find draws there, talk w/ central comp""".format(m=dis_weight_modelable_entity_id, h=healthstate_id)) 

    return df['draw{}'.format(config.getint('run_configuration', 'draw_number'))].iloc[0]


# 15. get_asympt_ihd_proportions


def get_asympt_ihd_proportions(location_id, year_start, year_end):
    """
    Gets the proportion of post-mi simulants that will get asymptomatic ihd.
    Proportion that will get asymptomatic ihd is equal to 1 - proportion of 
    mi 1 month survivors that get angina + proportion of mi 1 month survivors
    that get heart failure

    Parameters
    ----------
    Feed in parameters required by get_post_mi_heart_failure_proportion_draws and get_angina_proportion_draws

    Returns
    -------
    df with post-mi asymptomatic ihd proportions

    Notes
    -----
    Assumptions -- That all people who survive a heart attack then get one of asymptomatic ihd, heart failure, or angina

    Questions -- None

    Unit test in place? -- Yes
    """

    hf_prop_df = get_post_mi_heart_failure_proportion_draws(location_id, year_start, year_end)

    angina_prop_df = get_angina_proportions()

    merged = pd.merge(hf_prop_df, angina_prop_df, on=['age', 'year_id', 'sex_id'])
    
    merged = merged.set_index(['year_id', 'sex_id', 'age'])

    hf_values = merged[['draw_{}'.format(i) for i in range(0, 1000)]].values
    angina_values = merged[['angina_prop']].values

    # TODO: RAISE AN ERROR IF PROPORTIONS ARE GREATER THAN 1 FOR NOW. MAY WANT TO DELETE
    # ERROR IN THE FUTURE AND SCALE DOWN TO 1 INSTEAD
    # assert all(hf_values + angina_values) <= 1, "post mi proportions cannot be gt 1"      

    asympt_prop_df = pd.DataFrame(1 - hf_values - angina_values, columns=['asympt_prop_{}'.format(i) for i in range(1000)], index=merged.index)
 
    keepcol = ['year_id', 'sex_id', 'age']
    keepcol.extend(('asympt_prop_{i}'.format(i=i) for i in range(0, 1000)))

    return asympt_prop_df.reset_index()[keepcol] 


def get_age_specific_fertility_rates(location_id, year_start, year_end):
    #TODO: I'm loading this from disk because central comp doesn't have a good
    # tool for ingesting covariates from python and I don't feel like writing
    # any more stata. They say there should be something in a couple of weeks
    # and we should switch to it asap. -Alec 11/01/2016
    with open_auxiliary_file('Age-Specific Fertility Rates') as f:
        asfr = pd.read_csv(f)

    asfr = asfr.query('location_id == @location_id and year_id >= @year_start and year_id <= @year_end')
    asfr = get_age_group_midpoint_from_age_group_id(asfr)

    return asfr


def get_etiology_pafs(location_id, year_start, year_end, risk_id, cause_id):
    """
    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    risk_id: int, risk id
        risk_id takes same risk_id values as are used for GBD

    cause_id: int, cause id
        cause_id takes same cause_id values as are used for GBD

    -------
    Returns
        df with columns year_id, sex_id, age, val, upper, and lower

    """
    # For some of the diarrhea etiologies, PAFs are negative. Wouldn't make sense for the simulation to use negative pafs (i.e. incidence * PAF returns a negative incidence if PAF is negative), so we'll clip to 0. Guessing that any other diseases that deal with etiologies in the future won't need to be treated this way. --EM 12/13
    # uses get pafs, but then scales the negative pafs to 0. the diarrhea team has some pafs that are negative because they wanted to include full uncertainty. this seems implausible in the real world, unless one is arguing that some pathogens have a protective effect

    eti_pafs = get_pafs(location_id, year_start, year_end, risk_id, cause_id)
 
    # now make the negative etiology paf draws 0
    draws = eti_pafs._get_numeric_data()
    draws[draws < 0] = 0

    return eti_pafs


def get_etiology_probability(etiology_name):
    """
    Gets the proportion of diarrhea cases that are associated with a specific etiology

    Parameters
    ----------
    etiology_name: str
        etiology_name is the name of the etiology of interest

    Returns
    -------
    """

    etiology_df = pd.DataFrame()

    # TODO: Ask Chris T. if this works for cholera and c diff, since they are modeled differently than the other etiologies
    # TODO: Will want to cache this data in the future instead of pulling it from Chris Troeger's J Temp file
    etiology_proportion_draws = pd.read_stata("/home/j/temp/ctroeger/Diarrhea/DALYs/Draws/diarrhea_{}_eti_draw_proportion.dta".format(etiology_name))

    etiology_proportion_draws = etiology_proportion_draws.query("location_id == {}".format(config.getint('simulation_parameters', 'location_id')))

    etiology_proportion_draws = get_age_from_age_group_id(etiology_proportion_draws)

    for sex in (1, 2):
        one_sex = etiology_proportion_draws.query("sex_id == @sex")
        # TODO: Figure out if we want to get info from the config in this script or elsewhere
        one_sex = set_age_year_index(one_sex, 'early neonatal', 3, config.getint('simulation_parameters', 'year_start') , config.getint('simulation_parameters', 'year_end'))

        etiology_df = etiology_df.append(one_sex)

    etiology_df.reset_index(level=['age', 'year_id'], inplace=True)

    keepcol = ['year_id', 'sex_id', 'age', 'draw_{i}'.format(i=config.getint('run_configuration', 'draw_number'))]

    return etiology_df[keepcol]


def get_etiology_specific_incidence(location_id, year_start, year_end, eti_risk_id, cause_id, me_id):
    """
    Gets the paf of diarrhea cases that are associated with a specific etiology

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    eti_risk_id: int, risk id
        eti_risk_id takes same rei id values as are used for GBD

    cause_id: int, cause id
        cause_id takes same cause_id values as are used for GBD

    me_id: int, modelable_entity_id
        me_id takes modelable entity id of a cause

    Returns
    -------
    A dataframe of etiology specific incidence draws.
        Column are age, sex_id, year_id, and {etiology_name}_incidence_{draw} (1k draws)
    """

    # TODO: Figure out what we want to do regarding caching data. If we are using only one draw number, should we 
    # cache the data? If we cache the data, will we actually pull a different value for each draw
    draw_number = config.getint('run_configuration', 'draw_number')
    
    diarrhea_envelope_incidence = get_modelable_entity_draws(location_id, year_start, year_end,
                                                           measure=6, me_id=me_id) # measure=incidence, me_id=diarrhea envelope TODO: Make me_id an argument to be passed into the fucntion (i.e. make this function more flexible than just diarrhea)

    etiology_paf = get_etiology_pafs(location_id, year_start, year_end, eti_risk_id, cause_id)
    
    # TODO: Figure out if the interpolation should happen before the merge or in the simulation
    # merge interpolated pafs and interpolated incidence
    etiology_specific_incidence= pd.merge(diarrhea_envelope_incidence, etiology_paf, on=['age', 'year_id', 'sex_id'],
                                          suffixes=('_envelope', '_pafs'))

    etiology_specific_incidence.set_index(['year_id', 'sex_id', 'age'], inplace=True)
    
    pafs = etiology_specific_incidence[['draw_{}_pafs'.format(i) for i in range(0, 1000)]].values
    envelope_incidence_draws = etiology_specific_incidence[['draw_{}_envelope'.format(i) for i in range(0, 1000)]].values
    output_df = pd.DataFrame(np.multiply(pafs, envelope_incidence_draws), columns=['draw_{}'.format(i) for i in range(0, 1000)], index=etiology_specific_incidence.index)

    return output_df.reset_index()


def get_etiology_specific_prevalence(location_id, year_start, year_end, eti_risk_id, cause_id, me_id):
    """
    Gets draws of prevalence of diarrhea due to a specific specific etiology

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    year_start : int, year
        year_start is the year in which you want to start the simulation

    year_end : int, end year
        year_end is the year in which you want to end the simulation

    risk_id: int, risk id
        risk_id takes same risk_id values as are used for GBD

    cause_id: int, cause id
        cause_id takes same cause_id values as are used for GBD

    me_id: int, modelable_entity_id
        me_id takes modelable entity id of a cause

    Returns
    -------
    A dataframe of etiology specific prevalence draws. 
        Column are age, sex_id, year_id, and {etiology_name}_incidence_{draw} (1k draws)
    """

    draw_number = config.getint('run_configuration', 'draw_number')   
 
    diarrhea_envelope_prevalence = get_modelable_entity_draws(location_id, year_start, year_end,
                                                           measure=5, me_id=me_id) # measure=prevalence, me_id=diarrhea envelope
    
    etiology_paf = get_pafs(location_id, year_start, year_end, eti_risk_id, cause_id)

    etiology_specific_prevalence= pd.merge(diarrhea_envelope_prevalence, etiology_paf, on=['age', 'year_id', 'sex_id'], 
                                          suffixes=('_envelope', '_pafs'))

    etiology_specific_prevalence.set_index(['year_id', 'sex_id', 'age'], inplace=True)

    pafs = etiology_specific_prevalence[['draw_{}_pafs'.format(i) for i in range(0, 1000)]].values
    envelope_prevalence_draws = etiology_specific_prevalence[['draw_{}_envelope'.format(i) for i in range(0, 1000)]].values
    output_df = pd.DataFrame(np.multiply(pafs, envelope_prevalence_draws), columns=['draw_{}'.format(i) for i in range(0, 1000)], index=etiology_specific_prevalence.index)

    return output_df.reset_index()


# TODO: Figure out if we need to do anything to the remission rates. We have remission for all diarrhea.
# Do we need to split out remission to get remission from the different severity states?7
def get_diarrhea_severity_split_excess_mortality(excess_mortality_dataframe, severity_split):
    if severity_split == 'severe':
        # FIXME: Need to use severity split draws. Manually setting proportions for now
        severe_diarrhea_proportion = .14
        excess_mortality_dataframe['rate'] = excess_mortality_dataframe['rate'] / severe_diarrhea_proportion
    elif severity_split in ['mild', 'moderate']:
        # set the excess mortality rate to 0
        excess_mortality_dataframe['rate'] = 0
    else:
        raise ValueError("you supplied an invalid value for severity split argument. you wrote '{}'. acceptable severity splits are mild, moderate, or severe".format(severity_split))
    return excess_mortality_dataframe


# TODO: Write a SQL query for get_covariate_estimates that returns a covariate id instead of covariate short name, because names are subject to change but ids should stay the same
# TODO: Also link that covariate id to a publication id, if possible
def get_covariate_estimates(location_id, year_start, year_end, covariate_short_name):
    """
    Gets covariate estimates for a specified location. Processes data to put in correct format for CEAM (i.e. gets estimates for all years/ages/ and both sexes.

    Parameters
    ----------
    location_id : int
        location_id takes same location_id values as are used for GBD

    covariate_short_name: str
        the covariate_short_name for the covariate of interest.
        you can look up covariate_short_names here: http://cn307.ihme.washington.edu:9998/
        (check the covariate_metadata_tab in website above)

    Returns
    -------
    A dataframe of covariate_estimates.
        Column are age, sex_id, year_id, and {etiology_name}_incidence_{draw} (1k draws)
    """
    covariate_estimates = stata_wrapper('get_covariate_estimates.do', 'covariate_estimates_for_covariate_{c}.csv'.format(c=covariate_short_name), covariate_short_name)

    covariate_estimates = covariate_estimates.query("location_id == @location_id")

    expanded_estimates = expand_ages_for_dfs_w_all_age_estimates(covariate_estimates)

    expanded_estimates = expanded_estimates.query("year_id >= @year_start and year_id <= @year_end")

    keepcols = ['location_id', 'year_id', 'sex_id', 'age', 'covariate_id', 'covariate_name_short', 'mean_value', 'lower_value', 'upper_value']

    return expanded_estimates[keepcols]


# FIXME: Won't need function below once ORS exposure and RR estimates are uploaded to the database
def get_ors_exposure(location_id, year_start, year_end, draw_number):
    covariate_estimates_input = pd.read_csv("/share/epi/risk/bmgf/draws/exp/diarrhea_ors.csv")

    covariate_estimates = covariate_estimates_input.query("location_id == {}".format(location_id)).copy()

    expanded = expand_ages_for_dfs_w_all_age_estimates(covariate_estimates)

    expanded_estimates = expanded.query("year_id >= {ys} and year_id <= {ye}".format(ys = year_start, ye = year_end)).copy()

    keepcols = ['year_id', 'sex_id', 'age', 'cat1', 'cat2']

    expanded_estimates.rename(columns={'draw_{}'.format(draw_number): 'cat1'}, inplace=True)

    expanded_estimates['cat2'] = 1 - expanded_estimates['cat1']

    expanded_estimates = expanded_estimates[keepcols]

    return normalize_for_simulation(expanded_estimates)


# End.
